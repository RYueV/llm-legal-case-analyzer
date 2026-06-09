import html
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from dateWindowBuilder import build_date_windows
from dateWindowSanitizer import sanitize_date_windows
from evidenceAwareMerger import merge_window_annotations, render_merged_cards_html
from extractionAuditor import audit_schema_extractions
from evidenceContextExpander import build_refill_contexts
from missingFieldRefiner import refine_missing_fields
from flowiseClient import extract_text_from_flowise_response, query_flowise_form
from headerExtractor import extract_case_context_from_zones, extract_header_metadata_from_zones
from pdfParser import extract_pdf_pages
from referencesScanner import build_reference_blacklist, scan_legal_references
from sentenceExtractor import extract_sentences_from_zones
from structureSplitter import check_zone_integrity, split_pages_into_zones


# КОНСТАНТЫ
app = FastAPI(title="Legal Act Analyzer MVP")

MAX_WINDOW_WORKERS = int(os.getenv("WINDOW_ANNOTATION_WORKERS", "3"))
MAX_EXTRACTION_WORKERS = int(os.getenv("SCHEMA_EXTRACTION_WORKERS", "6"))
ENABLE_MISSING_FIELD_FALLBACK = os.getenv("ENABLE_MISSING_FIELD_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}
FALLBACK_TRIGGER_STATUSES = {"needs_refill"}


EXTRACTION_JOBS = {
    "caseProcedure": {
        "extraction_type": "caseProcedure",
        "input_cards": ["caseSkeletonCard"],
        "target_sections": ["metadata", "procedure"],
    },
    "claimEconomics": {
        "extraction_type": "claimEconomics",
        "input_cards": ["claimAndDealCard"],
        "target_sections": ["claim", "market_terms"],
    },
    "creditorProfile": {
        "extraction_type": "creditorProfile",
        "input_cards": ["creditorStatusCard"],
        "target_sections": ["creditor_profile", "behavior"],
    },
    "debtorCrisis": {
        "extraction_type": "debtorCrisis",
        "input_cards": ["debtorCrisisCard"],
        "target_sections": ["crisis"],
    },
    "proofEvidence": {
        "extraction_type": "proofEvidence",
        "input_cards": ["proofAndEvidenceCard"],
        "target_sections": ["proof", "proof_burden"],
    },
    "courtQualification": {
        "extraction_type": "courtQualification",
        "input_cards": ["courtAssessmentCard"],
        "target_sections": ["qualification", "motivation"],
    },
}


# РАСПАКОВКА И БАЗОВЫЕ УТИЛИТЫ
### распаковка результата pdfParser
def unpack_pdf_parse_result(parsed: Any) -> dict[str, Any]:
    # проверка кортежного результата парсера
    if isinstance(parsed, tuple):
        return {
            "pages": parsed[0],
            "raw_total_length": parsed[1] if len(parsed) > 1 else None,
            "cleaned_total_length": parsed[2] if len(parsed) > 2 else None,
            "main_case_number": parsed[3] if len(parsed) > 3 else None,
        }

    return {
        "pages": parsed,
        "raw_total_length": None,
        "cleaned_total_length": None,
        "main_case_number": None,
    }




### получение длины строки
def safe_len(value: Any) -> int:
    return len(str(value or ""))


### форматирование числа для интерфейса
def format_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


### извлечение текста страницы
def extract_page_text(page: Any) -> str:
    if isinstance(page, dict):
        for key in ("text", "clean_text", "content", "page_text"):
            if page.get(key):
                return str(page.get(key) or "")
        return ""
    return str(page or "")


### подсчет символов по страницам
def count_pages_chars(pages: list[Any]) -> int:
    return sum(safe_len(extract_page_text(page)) for page in pages or [])


### получение итогового статуса аудита
def get_audit_status(final_case) -> str:
    # извлечение блока аудита
    audit = (final_case or {}).get("audit") or {}
    return str(audit.get("overall_status") or "unknown")


### получение длины вторичного контекста языковой модели
def get_secondary_context_chars(final_case) -> int:
    # извлечение блока аудита
    audit = (final_case or {}).get("audit") or {}

    # поиск прямого счетчика вторичного контекста
    for key in (
        "secondary_context_chars",
        "fallback_context_chars",
        "refill_context_chars",
    ):
        value = audit.get(key)
        if isinstance(value, int):
            return value

    # поиск счетчика в отчете дозаполнения
    fallback = audit.get("fallback") or {}
    stats = fallback.get("refinement_stats") or {}
    value = stats.get("total_input_chars")
    if isinstance(value, int):
        return value

    # поиск счетчика в аудите дозаполнения
    refinement_audit = audit.get("refinement_audit") or {}
    value = (refinement_audit.get("stats") or {}).get("total_input_chars")
    if isinstance(value, int):
        return value

    return 0


### проверка необходимости дозаполнения
def should_run_missing_field_fallback(final_case) -> bool:
    if not ENABLE_MISSING_FIELD_FALLBACK:
        return False
    return get_audit_status(final_case) in FALLBACK_TRIGGER_STATUSES


# ДОЗАПОЛНЕНИЕ ПРОПУЩЕННЫХ ПОЛЕЙ
### выполнение полного цикла дозаполнения
def build_and_apply_missing_field_fallback(
    *,
    first_pass_case: dict[str, Any], # результат первичного аудита
    schema_extractions: list[dict[str, Any]], # результаты извлечения схемы
    header: Any, # данные шапки акта
    case_context, # контекст дела
    legal_references_report: dict[str, Any], # отчет о правовых ссылках
    merged_cards: dict[str, Any], # объединенные карточки
    sentences: list[dict[str, Any]], # предложения документа
) -> tuple:
    # сбор адресного контекста по задачам аудита
    refill_report = build_refill_contexts(
        final_case=first_pass_case,
        merged_cards=merged_cards,
        sentences=sentences,
    )

    # извлечение карты участников для экстрактора дозаполнения
    entity_map = (merged_cards or {}).get("entity_map") or {}

    # запуск дозаполнения проблемных полей
    refinement_report = refine_missing_fields(
        refill_report=refill_report,
        header=header,
        case_context=case_context,
        entity_map=entity_map,
        max_workers=1,
    )

    # повторный аудит с учетом результатов дозаполнения
    refined_case = audit_schema_extractions(
        schema_extractions=schema_extractions,
        header=header,
        case_context=case_context,
        legal_references_report=legal_references_report,
        merged_cards=merged_cards,
        refinement_extractions=refinement_report,
    )

    # фиксация статистики дозаполнения в итоговом аудите
    audit = refined_case.setdefault("audit", {})
    fallback_stats = {
        "triggered": True,
        "initial_status": get_audit_status(first_pass_case),
        "final_status": get_audit_status(refined_case),
        "refill_context_stats": refill_report.get("stats", {}) if isinstance(refill_report, dict) else {},
        "refinement_stats": refinement_report.get("stats", {}) if isinstance(refinement_report, dict) else {},
    }
    audit["fallback"] = fallback_stats

    return refined_case, refill_report, refinement_report

# ПОДГОТОВКА КОНТЕКСТА ДЛЯ АННОТАЦИИ ОКОН
### формирование контекста дела
def build_case_context_text(*, header: Any, case_context = None) -> str:
    # начальная инициализация контекста дела
    dispute_context = None
    context_debtor = None

    # использование расширенного контекста при наличии
    if case_context is not None:
        dispute_context = getattr(case_context, "dispute_context", None)
        context_debtor = getattr(case_context, "debtor_name", None)

    # выбор должника из расширенного контекста или шапки
    debtor_name = context_debtor or getattr(header, "debtor_name", None)

    return (
        "CASE_CONTEXT:\n"
        f"Номер дела: {getattr(header, 'case_number', None)}\n"
        f"Дата акта: {getattr(header, 'act_date', None)}\n"
        f"Суд: {getattr(header, 'court_name', None)}\n"
        f"Судьи: {getattr(header, 'judges', [])}\n"
        f"Должник: {debtor_name}\n"
        f"Обособленный спор: {dispute_context or 'None'}"
    ).strip()


### формирование входа для аннотации окна
def build_window_annotation_input(
    *,
    header: Any, # данные шапки акта
    case_context, # контекст дела
    window: dict[str, Any], # окно документа
    question: str = "", # вопрос пользователя
) -> str:
    return (
        f"{build_case_context_text(header=header, case_context=case_context)}\n\n"
        f"WINDOW_ID: {window.get('window_id')}\n"
        f"ZONE_ID: {window.get('zone_id')}\n"
        f"ZONE_TYPE: {window.get('zone_type')}\n"
        f"PAGES: {window.get('page_start')}-{window.get('page_end')}\n"
        f"DATES: {window.get('dates', [])}\n\n"
        f"USER_QUESTION:\n{question.strip() or '—'}\n\n"
        f"WINDOW:\n{window.get('text', '')}"
    ).strip()


### аннотация одного окна через flowise
def annotate_one_window(
    *,
    header: Any, # данные шапки акта
    case_context, # контекст дела
    window: dict[str, Any], # окно документа
    question: str, # вопрос пользователя
) -> dict[str, Any]:
    # вызов flowise для одной задачи аннотации окна
    response = query_flowise_form(
        task_type="windowAnnotation",
        input_text=build_window_annotation_input(
            header=header,
            case_context=case_context,
            window=window,
            question=question,
        ),
        timeout=300,
    )

    return {
        "window_id": window.get("window_id"),
        "zone_id": window.get("zone_id"),
        "zone_type": window.get("zone_type"),
        "page_start": window.get("page_start"),
        "page_end": window.get("page_end"),
        "sentence_ids": window.get("sentence_ids", []),
        "date_sentence_ids": window.get("date_sentence_ids", []),
        "dates": window.get("dates", []),
        "annotation_text": extract_text_from_flowise_response(response),
    }


### параллельная аннотация окон
def annotate_windows_parallel(
    *,
    header: Any, # данные шапки акта
    case_context, # контекст дела
    windows: list[dict[str, Any]], # окна документа
    question: str, # вопрос пользователя
    max_workers: int = MAX_WINDOW_WORKERS, # максимум потоков
) -> list[dict[str, Any]]:
    if not windows:
        return []

    # расчет безопасного числа потоков
    workers = max(1, min(max_workers, len(windows)))
    results_by_index: dict[int, dict[str, Any]] = {}

    # параллельный запуск аннотации окон
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(
                annotate_one_window,
                header=header,
                case_context=case_context,
                window=window,
                question=question,
            ): index
            for index, window in enumerate(windows)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            window = windows[index]

            # сохранение результата с восстановлением исходного порядка
            try:
                results_by_index[index] = future.result()
            except Exception as error:
        # возврат ошибки анализа пользователю
                results_by_index[index] = {
                    "window_id": window.get("window_id"),
                    "zone_id": window.get("zone_id"),
                    "zone_type": window.get("zone_type"),
                    "page_start": window.get("page_start"),
                    "page_end": window.get("page_end"),
                    "dates": window.get("dates", []),
                    "annotation_text": "",
                    "error": str(error),
                }

    return [results_by_index[index] for index in range(len(windows))]



# ПОДГОТОВКА ДАННЫХ ДЛЯ ИЗВЛЕЧЕНИЯ СХЕМЫ
### построение индекса предложений
def build_sentence_index(sentences: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(sentence.get("sentence_id")): dict(sentence) for sentence in sentences}


### формирование текста карты участников
def build_entity_map_text(entity_map: dict[str, Any]) -> str:
    if not entity_map:
        return "—"

    lines: list[str] = []
    for name, roles in entity_map.items():
        if isinstance(roles, list):
            role_text = "; ".join(str(role) for role in roles if str(role).strip())
        else:
            role_text = str(roles or "").strip()

        if role_text:
            lines.append(f"{name} — {role_text}")
        else:
            lines.append(str(name))

    return "\n".join(lines) if lines else "—"


### формирование обязательного payload по шапке
def build_header_payload(*, header: Any, case_context) -> list[dict[str, Any]]:
    # выбор должника для обязательного payload
    debtor_name = (
        getattr(case_context, "debtor_name", None)
        or getattr(header, "debtor_name", None)
        or "unknown"
    )

    # сбор строк служебного контекста
    lines = [
        f"Номер дела: {getattr(header, 'case_number', None) or 'unknown'}",
        f"Дата акта: {getattr(header, 'act_date', None) or 'unknown'}",
        f"Суд: {getattr(header, 'court_name', None) or 'unknown'}",
        f"Судьи: {'; '.join(getattr(header, 'judges', []) or []) or 'unknown'}",
        f"Должник: {debtor_name}",
        f"Обособленный спор: {getattr(case_context, 'dispute_context', None) or 'unknown'}",
    ]

    return [
        {
            "fragment_id": "header#001",
            "card_name": "header",
            "text": "\n".join(lines),
            "evidence_source": "zone=header",
        }
    ]

### формирование источника карточки
def build_card_evidence_source(fragment: dict[str, Any]) -> str:
    # извлечение сохраненных координат источника
    zone_types = [str(item).strip() for item in fragment.get("source_zone_types") or [] if str(item).strip()]
    window_ids = [str(item).strip() for item in fragment.get("source_window_ids") or [] if str(item).strip()]
    zone_ids = [str(item).strip() for item in fragment.get("source_zone_ids") or [] if str(item).strip()]

    # сбор человекочитаемого источника
    parts: list[str] = []

    if zone_types:
        parts.append(f"zone={', '.join(dict.fromkeys(zone_types))}")
    if window_ids:
        parts.append(f"window={', '.join(dict.fromkeys(window_ids))}")
    if zone_ids:
        parts.append(f"zone_id={', '.join(dict.fromkeys(zone_ids))}")

    return "; ".join(parts) if parts else "none"


### формирование полезной нагрузки карточки для экстрактора
def build_card_source_payload(
    *,
    card_name: str, # имя карточки
    merged_cards: dict[str, Any], # объединенные карточки
    sentence_index: dict[str, dict[str, Any]], # индекс предложений
    header = None,
    case_context = None,
) -> list[dict[str, Any]]:
    # выбор фрагментов нужной карточки
    fragments = (merged_cards.get("cards") or {}).get(card_name) or []
    payload: list[dict[str, Any]] = []

    for index, fragment in enumerate(fragments, start=1):
        payload.append(
            {
                "fragment_id": f"{card_name}#{index:03d}",
                "card_name": card_name,
                "text": fragment.get("text", ""),
                "evidence_source": build_card_evidence_source(fragment),
            }
        )

    # подстановка шапки при отсутствии skeleton карточки
    if not payload and card_name == "caseSkeletonCard" and header is not None:
        payload.extend(build_header_payload(header=header, case_context=case_context))

    return payload

### формирование входа для извлечения схемы
def build_schema_extraction_input(
    *,
    extraction_type: str, # тип извлечения
    input_cards: list[str], # входные карточки
    target_sections: list[str], # целевые блоки схемы
    header: Any, # данные шапки акта
    case_context, # контекст дела
    entity_map: dict[str, Any], # карта участников
    card_payload: list[dict[str, Any]], # фрагменты карточек
) -> str:
    return (
        "КОНТЕКСТ ДЕЛА:\n"
        f"{build_case_context_text(header=header, case_context=case_context)}\n\n"
        "КАРТА УЧАСТНИКОВ:\n"
        f"{build_entity_map_text(entity_map)}\n\n"
        "ОСНОВНОЙ МАТЕРИАЛ:\n"
        f"Тип извлечения: {extraction_type}\n"
        f"Смысловые карточки: {', '.join(input_cards)}\n"
        f"Заполняемые блоки схемы: {', '.join(target_sections)}\n\n"
        "Фрагменты карточек:\n"
        f"{json.dumps(card_payload, ensure_ascii=False, indent=2)}"
    ).strip()

# ВЫЗОВ FLOWISE И РАЗБОР ОТВЕТОВ
### вызов экстрактора структурированной схемы
def call_flowise_schema_extractor(*, extraction_type: str, input_text: str) -> Any:
    return query_flowise_form(
        task_type="schemaExtraction",
        input_text=input_text,
        extraction_type=extraction_type,
        timeout=300,
    )


### разбор структурированного ответа flowise
def parse_structured_flowise_answer(answer_text: str) -> tuple:
    # проверка пустого ответа flowise
    if not answer_text or not answer_text.strip():
        return None, None, "Пустой ответ Flowise."

    # разбор текстового json ответа
    try:
        parsed_answer = json.loads(answer_text)
    except json.JSONDecodeError as error:
        return None, None, f"Ответ Flowise не является валидным JSON: {error}"

    structured_answer = None

    # извлечение полезной части структурированного ответа
    if isinstance(parsed_answer, dict):
        result = parsed_answer.get("result")

        if isinstance(result, list):
            structured_answer = result[0] if result else None
        elif isinstance(result, dict):
            structured_answer = result
        else:
            structured_answer = parsed_answer

    elif isinstance(parsed_answer, list):
        structured_answer = parsed_answer[0] if parsed_answer else None

    if structured_answer is None:
        return parsed_answer, None, "JSON разобран, но полезная часть ответа не найдена."

    return parsed_answer, structured_answer, None

### выполнение одной задачи извлечения схемы
def extract_one_job(
    *,
    job_name: str, # имя задачи
    extractor_config: dict[str, Any], # настройки экстрактора
    merged_cards: dict[str, Any], # объединенные карточки
    sentence_index: dict[str, dict[str, Any]], # индекс предложений
    header: Any, # данные шапки акта
    case_context, # контекст дела
    entity_map: dict[str, Any], # карта участников
) -> dict[str, Any]:
    # получение настроек задачи экстрактора
    extraction_type = extractor_config["extraction_type"]
    target_sections = list(extractor_config["target_sections"])
    input_cards = list(extractor_config.get("input_cards") or [])

    # сбор входных фрагментов карточек
    card_payload: list[dict[str, Any]] = []
    for card_name in input_cards:
        card_payload.extend(
            build_card_source_payload(
                card_name=card_name,
                merged_cards=merged_cards,
                sentence_index=sentence_index,
                header=header,
                case_context=case_context,
            )
        )

    # пропуск задачи без входного материала
    if not card_payload:
        return {
            "job_name": job_name,
            "card_name": ", ".join(input_cards),
            "input_cards": input_cards,
            "extraction_type": extraction_type,
            "target_sections": target_sections,
            "input_fragments_count": 0,
            "answer_text": "",
            "answer_json": None,
            "structured_answer": None,
            "parse_error": None,
            "skipped": True,
            "skip_reason": "Нет фрагментов нужных карточек после evidenceAwareMerger.",
        }

    # формирование входного текста для экстрактора
    input_text = build_schema_extraction_input(
        extraction_type=extraction_type,
        input_cards=input_cards,
        target_sections=target_sections,
        header=header,
        case_context=case_context,
        entity_map=entity_map,
        card_payload=card_payload,
    )

    # вызов экстрактора через flowise
    response = call_flowise_schema_extractor(
        extraction_type=extraction_type,
        input_text=input_text,
    )

    # распаковка ответа экстрактора
    answer_text = extract_text_from_flowise_response(response)
    answer_json, structured_answer, parse_error = parse_structured_flowise_answer(answer_text)

    return {
        "job_name": job_name,
        "card_name": ", ".join(input_cards),
        "input_cards": input_cards,
        "extraction_type": extraction_type,
        "target_sections": target_sections,
        "input_fragments_count": len(card_payload),
        "answer_text": answer_text,
        "answer_json": answer_json,
        "structured_answer": structured_answer,
        "parse_error": parse_error,
        "source_payload": card_payload,
        "input_text_length": len(input_text),
    }

# ПАРАЛЛЕЛЬНОЕ ИЗВЛЕЧЕНИЕ СХЕМЫ
### выполнение батча извлечения схемы
def extract_schema_batch(
    *,
    batch_items: list[tuple[str, dict[str, Any]]], # задачи батча
    merged_cards: dict[str, Any], # объединенные карточки
    sentence_index: dict[str, dict[str, Any]], # индекс предложений
    header: Any, # данные шапки акта
    case_context, # контекст дела
    entity_map: dict[str, Any], # карта участников
    max_workers: int, # максимум потоков
) -> list[dict[str, Any]]:
    # отсутствие задач в батче
    if not batch_items:
        return []

    # расчет числа потоков для батча
    workers = max(1, min(max_workers, len(batch_items)))
    results_by_index: dict[int, dict[str, Any]] = {}

    # параллельный запуск аннотации окон
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(
                extract_one_job,
                job_name=job_name,
                extractor_config=config,
                merged_cards=merged_cards,
                sentence_index=sentence_index,
                header=header,
                case_context=case_context,
                entity_map=entity_map,
            ): index
            for index, (job_name, config) in enumerate(batch_items)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            job_name, config = batch_items[index]

            # сохранение результата с восстановлением исходного порядка
            try:
                results_by_index[index] = future.result()
            except Exception as error:
        # возврат ошибки анализа пользователю
                results_by_index[index] = {
                    "job_name": job_name,
                    "card_name": ", ".join(config.get("input_cards") or []),
                    "input_cards": list(config.get("input_cards") or []),
                    "extraction_type": config["extraction_type"],
                    "target_sections": config["target_sections"],
                    "answer_text": "",
                    "answer_json": None,
                    "structured_answer": None,
                    "parse_error": None,
                    "error": str(error),
                }

    return [results_by_index[index] for index in range(len(batch_items))]


### формирование батчей экстракторов
def build_extraction_batches() -> list[list[tuple[str, dict[str, Any]]]]:
    # фиксированный порядок двух волн экстракторов
    batch_order = [
        ["caseProcedure", "claimEconomics", "creditorProfile"],
        ["debtorCrisis", "proofEvidence", "courtQualification"],
    ]

    batches: list[list[tuple[str, dict[str, Any]]]] = []
    used_names: set[str] = set()

    # сбор основных батчей
    for names in batch_order:
        batch: list[tuple[str, dict[str, Any]]] = []
        for name in names:
            config = EXTRACTION_JOBS.get(name)
            if config is not None:
                batch.append((name, config))
                used_names.add(name)
        if batch:
            batches.append(batch)

    # добавление внеплановых задач экстракторов
    extra_items = [
        (name, config)
        for name, config in EXTRACTION_JOBS.items()
        if name not in used_names
    ]

    for index in range(0, len(extra_items), 3):
        batches.append(extra_items[index:index + 3])

    return batches


### параллельное извлечение схемы
def extract_schema_parallel(
    *,
    merged_cards: dict[str, Any], # объединенные карточки
    sentences: list[dict[str, Any]], # предложения документа
    header: Any, # данные шапки акта
    case_context, # контекст дела
    max_workers: int = MAX_EXTRACTION_WORKERS, # максимум потоков извлечения
) -> list[dict[str, Any]]:
    # подготовка индексов и карточек
    sentence_index = build_sentence_index(sentences)
    entity_map = merged_cards.get("entity_map") or {}
    batches = build_extraction_batches()
    batch_workers = max(1, min(max_workers, 3))

    results: list[dict[str, Any]] = []

    # последовательный запуск волн extraction
    for batch in batches:
        results.extend(
            extract_schema_batch(
                batch_items=batch,
                merged_cards=merged_cards,
                sentence_index=sentence_index,
                header=header,
                case_context=case_context,
                entity_map=entity_map,
                max_workers=batch_workers,
            )
        )

    return results

# HTML ПРЕДСТАВЛЕНИЕ РЕЗУЛЬТАТОВ
### html представление ответов экстракторов
def render_schema_extractions_html(extractions: list[dict[str, Any]]) -> str:
    parts: list[str] = []

    # построение карточек по ответам экстракторов
    for item in extractions:
        title = f"{item.get('card_name')} → {item.get('extraction_type')}"
        sections = ", ".join(item.get("target_sections") or [])

        if item.get("skipped"):
            body = f"Пропущено: {item.get('skip_reason')}"
        elif item.get("error"):
            body = f"ERROR: {item.get('error')}"
        elif item.get("structured_answer") is not None:
            body = json.dumps(
                item.get("structured_answer"),
                ensure_ascii=False,
                indent=2,
            )
        elif item.get("parse_error"):
            body = (
                f"PARSE ERROR: {item.get('parse_error')}\n\n"
                f"RAW ANSWER:\n{item.get('answer_text') or ''}"
            )
        else:
            body = item.get("answer_text") or ""

        parts.append(
            f"""
            <div class="raw-card">
                <h2>{html.escape(title)}</h2>
                <div class="source">blocks={html.escape(sections)}; fragments={html.escape(str(item.get('input_fragments_count', '')))}</div>
                <pre>{html.escape(body)}</pre>
            </div>
            """
        )

    return "\n".join(parts)


### html представление преамбулы
def render_header_html(result: dict[str, Any]) -> str:
    header = result.get("header") or {}
    case_context = result.get("case_context") or {}

    # подготовка строк преамбулы
    rows = [
        ("Номер дела", header.get("case_number")),
        ("Дата акта", header.get("act_date")),
        ("Суд", header.get("court_name")),
        ("Суд в составе", "; ".join(header.get("judges") or []) or "—"),
        ("Должник", case_context.get("debtor_name") or header.get("debtor_name")),
        ("Обособленный спор", case_context.get("dispute_context") or "—"),
    ]

    body = "".join(
        f"<div><b>{html.escape(str(key))}:</b> {html.escape(str(value or '—'))}</div>"
        for key, value in rows
    )
    return f"<div class='raw-card'><h2>Преамбула headerExtractor</h2><div class='summary'>{body}</div></div>"


### html представление правовых ссылок
def render_legal_references_html(report: dict[str, Any]) -> str:
    if not report:
        return "<div class='raw-card'><h2>Правовые ссылки referencesScanner</h2><p class='muted'>—</p></div>"

    # извлечение сгруппированных правовых ссылок
    legal_references = report.get("legal_references") or {}
    parts = [
        "<div class='raw-card'>",
        "<h2>Правовые ссылки referencesScanner</h2>",
        (
            "<div class='source'>"
            f"hits={html.escape(str(report.get('hits_count', 0)))}; "
            f"blacklist_sentences={html.escape(str(report.get('blacklisted_count', 0)))}"
            "</div>"
        ),
    ]

    if not legal_references:
        parts.append("<p class='muted'>Ссылки не найдены.</p>")
    else:
        for category, hits in legal_references.items():
            parts.append(f"<h3>{html.escape(str(category))} ({len(hits or [])})</h3>")
            for hit in hits or []:
                value = html.escape(str(hit.get("value") or ""))
                text = html.escape(str(hit.get("text") or ""))
                meta = html.escape(
                    f"sentence={hit.get('sentence_id')}; zone={hit.get('zone_type')}:{hit.get('zone_id')}; pages={hit.get('page_start')}-{hit.get('page_end')}"
                )
                parts.append(
                    "<div class='merged-fragment'>"
                    f"<div class='fragment-text'><b>{value}</b><br>{text}</div>"
                    f"<div class='source'>{meta}</div>"
                    "</div>"
                )

    parts.append("</div>")
    return "\n".join(parts)

### html представление сводки
def render_summary_html(result: dict[str, Any]) -> str:
    final_case = result.get("final_structured_case") or {}
    status = get_audit_status(final_case)

    # подготовка строк сводки
    rows = [
        ("Файл", result.get("filename")),
        ("Символов в документе", format_int(result.get("document_chars"))),
        ("Символов в первичном LLM-контексте", format_int(result.get("primary_llm_context_chars"))),
        ("Символов во вторичном LLM-контексте", format_int(result.get("secondary_llm_context_chars"))),
        ("Статус", status),
    ]

    items: list[str] = []
    for key, value in rows:
        value_text = str(value if value not in (None, "") else "—")
        css_class = "summary-value"
        if key == "Статус":
            css_class = f"summary-value status status-{html.escape(value_text)}"

        items.append(
            "<div class='summary-item'>"
            f"<b>{html.escape(str(key))}:</b> "
            f"<span class='{css_class}'>{html.escape(value_text)}</span>"
            "</div>"
        )

    return f"<div class='summary'>{''.join(items)}</div>"


### html представление аудита
def render_audit_html(final_case: dict[str, Any]) -> str:
    # извлечение блока аудита
    audit = (final_case or {}).get("audit") or {}
    status = str(audit.get("overall_status") or "unknown")

    # поиск счетчика в отчете дозаполнения
    fallback = audit.get("fallback") or {}
    # поиск счетчика в аудите дозаполнения
    refinement_audit = audit.get("refinement_audit") or {}

    # группировка причин статуса аудита
    reason_blocks = [
        ("Fallback", [fallback] if fallback else []),
        ("Принято после дозаполнения", refinement_audit.get("accepted") or []),
        ("Отклонено после дозаполнения", refinement_audit.get("rejected") or []),
        ("Конфликты полей", audit.get("field_conflicts") or audit.get("critical_conflicts") or []),
        ("Логические несостыковки", audit.get("logical_inconsistencies") or []),
        ("Поля для дозаполнения", audit.get("refill_tasks") or []),
        ("Требуется ручная проверка", audit.get("manual_review_required") or []),
        ("Предупреждения по доказательствам", audit.get("evidence_warnings") or []),
        ("Структурные предупреждения", audit.get("structural_warnings") or []),
        ("Ошибки экстракторов", audit.get("extractor_errors") or []),
    ]

    parts: list[str] = [
        "<div class='raw-card'>",
        "<h2>Audit</h2>",
        f"<div class='audit-status-line'><b>Итоговый статус:</b> <span class='status status-{html.escape(status)}'>{html.escape(status)}</span></div>",
    ]

    has_reasons = False
    # вывод непустых блоков причин
    for title, items in reason_blocks:
        if not items:
            continue
        has_reasons = True
        parts.append(f"<h3>{html.escape(title)}</h3>")
        parts.append("<div class='audit-list'>")
        for item in items:
            if isinstance(item, dict):
                item_text = json.dumps(item, ensure_ascii=False, indent=2)
            else:
                item_text = str(item)
            parts.append(f"<pre class='audit-item'>{html.escape(item_text)}</pre>")
        parts.append("</div>")

    if not has_reasons:
        parts.append("<p class='muted'>Причины для дополнительной проверки не зафиксированы.</p>")

    parts.append("</div>")
    return "\n".join(parts)

### html представление результата анализа
def render_analysis_html(result: dict[str, Any]) -> str:
    # извлечение основных блоков результата
    merged = result.get("merged_cards") or {}
    schema_extractions = result.get("schema_extractions") or []
    legal_references_report = result.get("legal_references_report") or {}
    final_case = result.get("final_structured_case") or {}

    return f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>Legal Act Analyzer — result</title>

        <style>
            body {{
                max-width: 1150px;
                margin: 30px auto;
                background: #f5f5f5;
                font-family: Arial, sans-serif;
                color: #222;
            }}

            h1 {{
                margin: 0 0 18px;
                font-size: 26px;
            }}

            h2 {{
                margin: 0 0 12px;
                font-size: 18px;
            }}

            .summary,
            .merged-section,
            .raw-card {{
                background: white;
                border-radius: 12px;
                padding: 18px 20px;
                margin-bottom: 18px;
                box-shadow: 0 2px 8px rgba(0,0,0,.08);
            }}

            .summary {{
                display: flex;
                flex-direction: column;
                gap: 10px;
            }}

            .summary-item {{
                line-height: 1.35;
            }}

            .status {{
                display: inline-block;
                padding: 3px 10px;
                border-radius: 999px;
                font-weight: 700;
                font-family: Consolas, monospace;
            }}

            .status-complete {{
                background: #e6f4ea;
                color: #137333;
            }}

            .status-needs_refill {{
                background: #fff4e5;
                color: #b06000;
            }}

            .status-needs_manual_review {{
                background: #fdeaea;
                color: #b3261e;
            }}

            .status-unknown {{
                background: #eeeeee;
                color: #555;
            }}

            .tabs {{
                display: flex;
                gap: 8px;
                margin: 20px 0;
            }}

            .tabs a {{
                display: inline-block;
                background: #fff;
                border-radius: 999px;
                padding: 9px 14px;
                text-decoration: none;
                color: #222;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}

            .tabs a:hover {{
                background: #eee;
            }}

            .section-anchor {{
                scroll-margin-top: 20px;
            }}

            .merge-stats {{
                background: #fff;
                border-radius: 12px;
                padding: 14px 18px;
                margin-bottom: 18px;
                box-shadow: 0 2px 8px rgba(0,0,0,.08);
                display: flex;
                flex-wrap: wrap;
                gap: 12px;
            }}

            .merged-fragment {{
                border-left: 4px solid #ddd;
                padding: 10px 12px;
                margin: 10px 0;
                background: #fafafa;
                border-radius: 8px;
            }}

            .fragment-text {{
                white-space: pre-wrap;
                line-height: 1.45;
            }}

            .source {{
                margin-top: 7px;
                color: #666;
                font-size: 13px;
                font-family: Consolas, monospace;
            }}

            .muted {{
                color: #777;
            }}


            details.collapsible-block {{
                margin-bottom: 18px;
            }}

            details.collapsible-block > summary {{
                list-style: none;
                cursor: pointer;
                background: white;
                border-radius: 12px;
                padding: 14px 18px;
                margin-bottom: 10px;
                box-shadow: 0 2px 8px rgba(0,0,0,.08);
                font-size: 22px;
                font-weight: 700;
            }}

            details.collapsible-block > summary::-webkit-details-marker {{
                display: none;
            }}

            details.collapsible-block > summary::before {{
                content: "▸";
                display: inline-block;
                margin-right: 8px;
                transition: transform .15s ease;
            }}

            details.collapsible-block[open] > summary::before {{
                transform: rotate(90deg);
            }}

            .audit-status-line {{
                margin-bottom: 14px;
                font-size: 16px;
            }}

            .audit-list {{
                display: flex;
                flex-direction: column;
                gap: 10px;
                margin-bottom: 14px;
            }}

            .audit-item {{
                background: #fafafa;
                border-left: 4px solid #ddd;
                border-radius: 8px;
                padding: 10px 12px;
            }}

            pre {{
                white-space: pre-wrap;
                word-wrap: break-word;
                margin: 0;
                font-size: 15px;
                line-height: 1.45;
                font-family: Consolas, monospace;
            }}
        </style>
    </head>

    <body>
        <h1>Результат анализа</h1>

        {render_summary_html(result)}

        <div class="tabs">
            <a href="#header">Преамбула</a>
            <a href="#references">Правовые ссылки</a>
            <a href="#merged">Объединенные карточки</a>
            <a href="#audit">Audit</a>
            <a href="#extractors">Ответы экстракторов</a>
        </div>

        <div id="header" class="section-anchor">
            {render_header_html(result)}
        </div>

        <details id="references" class="section-anchor collapsible-block">
            <summary>Правовые ссылки</summary>
            {render_legal_references_html(legal_references_report)}
        </details>

        <details id="merged" class="section-anchor collapsible-block">
            <summary>Объединенные карточки</summary>
            {render_merged_cards_html(merged)}
        </details>

        <details id="audit" class="section-anchor collapsible-block" open>
            <summary>Audit</summary>
            {render_audit_html(final_case)}
        </details>

        <details id="extractors" class="section-anchor collapsible-block">
            <summary>Ответы LLM-экстракторов</summary>
            {render_schema_extractions_html(schema_extractions)}
        </details>
    </body>
    </html>
    """


# МАРШРУТЫ FASTAPI
### отображение страницы загрузки
@app.get("/", response_class=HTMLResponse)
async def upload_page():
    return """
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>Legal Act Analyzer</title>

        <style>
            body{
                max-width:700px;
                margin:60px auto;
                background:#f5f5f5;
                font-family:Arial,sans-serif;
            }

            form{
                background:white;
                padding:24px;
                border-radius:12px;
                box-shadow:0 2px 8px rgba(0,0,0,.08);
            }

            input,textarea,button{
                width:100%;
                box-sizing:border-box;
                margin-top:12px;
                font-size:16px;
                padding:10px;
            }

            button{
                cursor:pointer;
            }
        </style>

    </head>

    <body>

        <form action="/analyze"
              method="post"
              enctype="multipart/form-data">

            <h2>Загрузить судебный акт</h2>

            <input
                type="file"
                name="file"
                accept=".pdf"
                required>

            <textarea
                name="question"
                rows="4"
                placeholder="Вопрос (необязательно)"></textarea>

            <button type="submit">
                Анализировать
            </button>

        </form>

    </body>
    </html>
    """


### анализ загруженного pdf файла
@app.post("/analyze")
async def analyze_pdf(
    file: UploadFile = File(...), # загруженный pdf файл
    question: str = Form(""), # вопрос пользователя
):
    # проверка типа загруженного файла
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse(
            status_code=400,
            content={"error": "Можно загрузить только PDF-файл."},
        )

    temp_path = None

    try:
        # сохранение pdf во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_path = temp_file.name
            temp_file.write(await file.read())

        # извлечение текста pdf
        parse_info = unpack_pdf_parse_result(extract_pdf_pages(temp_path))
        pages = parse_info["pages"]

        # деление документа на зоны
        zones = split_pages_into_zones(pages)
        integrity = check_zone_integrity(pages, zones)

        # остановка при нарушении сохранности текста
        if not integrity.get("ok"):
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Ошибка структурного разбиения.",
                    "zone_integrity": integrity,
                },
            )

        # извлечение шапки и контекста дела
        header = extract_header_metadata_from_zones(zones)
        case_context = extract_case_context_from_zones(zones)

        # разбиение зон на предложения
        sentences = extract_sentences_from_zones(
            zones,
            include_operative_part=False,
        )

        # поиск правовых ссылок и blacklist предложений
        references_report = scan_legal_references(sentences)
        blacklist = set(references_report.get("blacklisted_sentence_ids") or [])

        # построение окон по датам
        date_windows = build_date_windows(
            sentences,
            blacklisted_sentence_ids=blacklist,
        )

        # санитарная очистка окон
        sanitized_windows = sanitize_date_windows(date_windows)

        # параллельная аннотация окон языковой моделью
        annotations = annotate_windows_parallel(
            header=header,
            case_context=case_context,
            windows=sanitized_windows,
            question=question,
            max_workers=MAX_WINDOW_WORKERS,
        )

        # объединение аннотаций в карточки
        merged_cards = merge_window_annotations(annotations)

        # извлечение структурированных признаков
        schema_extractions = extract_schema_parallel(
            merged_cards=merged_cards,
            sentences=sentences,
            header=header,
            case_context=case_context,
            max_workers=MAX_EXTRACTION_WORKERS,
        )

        # первичный аудит извлеченных признаков
        first_pass_case = audit_schema_extractions(
            schema_extractions=schema_extractions,
            header=header,
            case_context=case_context,
            legal_references_report=references_report,
            merged_cards=merged_cards,
        )

        refill_report: dict[str, Any] = {}
        refinement_report: dict[str, Any] = {}
        final_structured_case = first_pass_case

        # запуск дозаполнения при необходимости
        if should_run_missing_field_fallback(first_pass_case):
            final_structured_case, refill_report, refinement_report = build_and_apply_missing_field_fallback(
                first_pass_case=first_pass_case,
                schema_extractions=schema_extractions,
                header=header,
                case_context=case_context,
                legal_references_report=references_report,
                merged_cards=merged_cards,
                sentences=sentences,
            )
        else:
            final_structured_case.setdefault("audit", {}).setdefault(
                "fallback",
                {
                    "triggered": False,
                    "initial_status": get_audit_status(first_pass_case),
                    "reason": "status_does_not_require_refill_or_fallback_disabled",
                },
            )

        # расчет сводных метрик объема
        document_chars = (
            parse_info.get("raw_total_length")
            or parse_info.get("cleaned_total_length")
            or count_pages_chars(pages)
        )
        primary_llm_context_chars = sum(
            int(item.get("input_text_length") or 0)
            for item in schema_extractions
            if isinstance(item, dict)
        )
        secondary_llm_context_chars = get_secondary_context_chars(final_structured_case)

        # сбор итогового результата для html страницы
        result = {
            "filename": file.filename,
            "main_case_number": parse_info.get("main_case_number"),
            "document_chars": document_chars,
            "primary_llm_context_chars": primary_llm_context_chars,
            "secondary_llm_context_chars": secondary_llm_context_chars,
            "header": {
                "case_number": header.case_number,
                "act_date": header.act_date,
                "court_name": header.court_name,
                "judges": header.judges,
                "debtor_name": header.debtor_name,
            },
            "case_context": {
                "debtor_name": getattr(case_context, "debtor_name", None),
                "dispute_context": getattr(case_context, "dispute_context", None),
            },
            "pages_count": len(pages),
            "zones_count": len(zones),
            "sentences_count": len(sentences),
            "blacklisted_sentences_count": len(blacklist),
            "date_windows_count": len(date_windows),
            "sanitized_windows_count": len(sanitized_windows),
            "window_annotation_workers": MAX_WINDOW_WORKERS,
            "schema_extraction_workers": MAX_EXTRACTION_WORKERS,
            "legal_references_report": references_report,
            "window_annotations": annotations,
            "merged_cards": merged_cards,
            "schema_extractions": schema_extractions,
            "final_structured_case": final_structured_case,
            "refill_report": refill_report,
            "refinement_report": refinement_report,
        }

        return HTMLResponse(render_analysis_html(result))

    except Exception as error:
        # возврат ошибки анализа пользователю
        return JSONResponse(status_code=500, content={"error": str(error)})

    finally:
        # удаление временного файла
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
