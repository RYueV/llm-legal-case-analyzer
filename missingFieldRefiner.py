import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Sequence

from flowiseClient import extract_text_from_flowise_response, query_flowise_form


# КОНСТАНТЫ

MAX_REFINE_WORKERS = int(os.getenv("MISSING_FIELD_REFINE_WORKERS", "1"))
REFINE_RETRIES = int(os.getenv("MISSING_FIELD_REFINE_RETRIES", "1"))
REFINE_RETRY_SLEEP_SECONDS = float(os.getenv("MISSING_FIELD_REFINE_RETRY_SLEEP_SECONDS", "1.0"))
REFINE_CALL_DELAY_SECONDS = float(os.getenv("MISSING_FIELD_REFINE_CALL_DELAY_SECONDS", "0.3"))

# ограничение количества контекстов для одного extractor
MAX_CONTEXTS_PER_EXTRACTOR = int(os.getenv("MISSING_FIELD_MAX_CONTEXTS_PER_EXTRACTOR", "8"))
MAX_INPUT_CHARS_PER_EXTRACTOR = int(os.getenv("MISSING_FIELD_MAX_INPUT_CHARS_PER_EXTRACTOR", "9000"))


SECTION_TO_EXTRACTOR: dict[str, Any] = {
    "metadata": "caseProcedure",
    "procedure": "caseProcedure",
    "claim": "claimEconomics",
    "market_terms": "claimEconomics",
    "creditor_profile": "creditorProfile",
    "behavior": "creditorProfile",
    "crisis": "debtorCrisis",
    "proof": "proofEvidence",
    "proof_burden": "proofEvidence",
    "qualification": "courtQualification",
    "motivation": "courtQualification",
    # исключение детерминированного блока economic_dispute
    "economic_dispute": None,
}

EXTRACTOR_TARGET_SECTIONS: dict[str, list[str]] = {
    "caseProcedure": ["metadata", "procedure"],
    "claimEconomics": ["claim", "market_terms"],
    "creditorProfile": ["creditor_profile", "behavior"],
    "debtorCrisis": ["crisis"],
    "proofEvidence": ["proof", "proof_burden"],
    "courtQualification": ["qualification", "motivation"],
}

EXTRACTOR_HINTS: dict[str, str] = {
    "caseProcedure": "Дозаполни только реквизиты акта и процедурные признаки, если они прямо следуют из контекста.",
    "claimEconomics": "Дозаполни только claim и market_terms. Не заполняй economic_dispute: его выводит аудитор.",
    "creditorProfile": "Дозаполни только creditor_profile и behavior. Не делай вывод о квалификации требования.",
    "debtorCrisis": "Дозаполни только crisis. Не выводи кризис из самого факта банкротства без прямой опоры.",
    "proofEvidence": "Дозаполни только proof и proof_burden. Не превращай позицию стороны в вывод суда.",
    "courtQualification": "Дозаполни только qualification и motivation. Используй только выводы суда, а не доводы сторон.",
}


# НОРМАЛИЗАЦИЯ И РАСПАКОВКА


### преобразование dataclass в обычные структуры данных
def to_plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): to_plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value


### удаление префикса annotation из пути поля
def strip_annotation_prefix(path: str) -> str:
    value = str(path or "").strip()
    while value.startswith("annotation."):
        value = value[len("annotation."):]
    return value


### извлечение раздела из пути поля
def section_from_path(path: str) -> str:
    clean = strip_annotation_prefix(path)
    return clean.split(".", 1)[0] if clean else ""


### выбор extractor по пути поля
def extractor_for_path(path: str):
    return SECTION_TO_EXTRACTOR.get(section_from_path(path))


### нормализация отчета с контекстами дозаполнения
def normalize_refill_report(refill_report) -> list[dict[str, Any]]:
    data = to_plain_data(refill_report)

    if isinstance(data, dict):
        contexts = data.get("refill_contexts") or []
    elif isinstance(data, list):
        contexts = data
    else:
        contexts = []

    return [dict(item) for item in contexts if isinstance(item, dict)]


### проверка пригодности контекста для дозаполнения
def has_usable_context(item: dict[str, Any]) -> bool:
    if item.get("skipped"):
        return False
    fragments = item.get("context_fragments") or []
    if not isinstance(fragments, list) or not fragments:
        return False
    return any(str(fragment.get("text") or "").strip() for fragment in fragments if isinstance(fragment, dict))


### группировка контекстов по extractor
def group_refill_contexts_by_extractor(refill_report) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}

    for item in normalize_refill_report(refill_report):
        path = str(item.get("path") or "").strip()
        extraction_type = extractor_for_path(path)

        if extraction_type is None:
            # исключение секций без дозаполнения
            continue

        if not has_usable_context(item):
            continue

        grouped.setdefault(extraction_type, []).append(item)

    for extraction_type, contexts in list(grouped.items()):
        grouped[extraction_type] = contexts[:MAX_CONTEXTS_PER_EXTRACTOR]

    return grouped


### формирование общего контекста дела
def build_case_context_text(*, header: Any = None, case_context: Any = None) -> str:
    header_data = to_plain_data(header) or {}
    context_data = to_plain_data(case_context) or {}

    def get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    debtor_name = get(context_data, "debtor_name") or get(header_data, "debtor_name")

    lines = [
        "CASE_CONTEXT:",
        f"Номер дела: {get(header_data, 'case_number') or 'unknown'}",
        f"Дата акта: {get(header_data, 'act_date') or 'unknown'}",
        f"Суд: {get(header_data, 'court_name') or 'unknown'}",
        f"Судьи: {get(header_data, 'judges') or []}",
        f"Должник: {debtor_name or 'unknown'}",
        f"Обособленный спор: {get(context_data, 'dispute_context') or 'unknown'}",
    ]
    return "\n".join(lines).strip()


### формирование текста карты участников
def build_entity_map_text(entity_map: dict[str, Any] = None) -> str:
    if not entity_map:
        return "—"

    lines: list[str] = []
    for name, roles in entity_map.items():
        if isinstance(roles, list):
            role_text = "; ".join(str(role) for role in roles if str(role).strip())
        else:
            role_text = str(roles or "").strip()
        lines.append(f"{name} — {role_text}" if role_text else str(name))

    return "\n".join(lines) if lines else "—"


### подготовка компактного контекста для fallback extractor
def compact_context_for_llm(context: dict[str, Any]) -> dict[str, Any]:
    fragments = []
    for index, fragment in enumerate(context.get("context_fragments") or [], start=1):
        if not isinstance(fragment, dict):
            continue
        text = str(fragment.get("text") or "").strip()
        if not text:
            continue
        fragments.append(
            {
                "fragment_id": f"refill#{index:02d}",
                "source": fragment.get("source") or "none",
                "matched_keyword": fragment.get("matched_keyword") or "",
                "search_stage": fragment.get("search_stage") or "",
                "text": text,
            }
        )

    return {
        "path": context.get("path"),
        "reason": context.get("reason"),
        "source_audit_block": context.get("source_audit_block"),
        "context_fragments": fragments,
    }


# ФОРМИРОВАНИЕ INPUT_TEXT ДЛЯ FLOWISE


### формирование входного текста для extractor
def build_refinement_input(
    *,
    extraction_type: str,
    contexts: Sequence[dict[str, Any]],
    header: Any = None,
    case_context: Any = None,
    entity_map: dict[str, Any] = None,
) -> str:
    target_paths = [str(item.get("path") or "").strip() for item in contexts if str(item.get("path") or "").strip()]
    target_sections = EXTRACTOR_TARGET_SECTIONS.get(extraction_type, [])
    compact_contexts = [compact_context_for_llm(item) for item in contexts]

    input_data = {
        "mode": "missing_field_refinement",
        "extraction_type": extraction_type,
        "target_sections": target_sections,
        "target_paths": target_paths,
        "instruction": (
            "Это повторное дозаполнение. Используй только REFILL_CONTEXTS. "
            "Не меняй смысл уже заполненных полей. Если прямой опоры нет, ставь unknown. "
            "Верни результат в том же structured output формате, который требуется этому extractor."
        ),
        "refill_contexts": compact_contexts,
    }

    text = (
        f"{build_case_context_text(header=header, case_context=case_context)}\n\n"
        "КАРТА УЧАСТНИКОВ:\n"
        f"{build_entity_map_text(entity_map)}\n\n"
        "РЕЖИМ РАБОТЫ:\n"
        "Это повторное дозаполнение только проблемных полей.\n"
        f"Тип извлечения: {extraction_type}\n"
        f"Заполняемые блоки extractor: {', '.join(target_sections)}\n"
        f"Проблемные поля: {', '.join(target_paths)}\n"
        f"Подсказка: {EXTRACTOR_HINTS.get(extraction_type, '')}\n\n"
        "ВАЖНО:\n"
        "- Используй только переданные REFILL_CONTEXTS.\n"
        "- Не придумывай недостающие факты.\n"
        "- Если по полю нет прямой опоры, оставь unknown.\n"
        "- Даже если extractor вернет весь блок, аудитор потом возьмет только target_paths.\n"
        "- Не заполняй economic_dispute: этот блок выводится аудитором детерминированно.\n\n"
        "REFILL_PAYLOAD_JSON:\n"
        f"{json.dumps(input_data, ensure_ascii=False, indent=2)}"
    ).strip()

    if len(text) > MAX_INPUT_CHARS_PER_EXTRACTOR:
        text = text[:MAX_INPUT_CHARS_PER_EXTRACTOR].rstrip()
        text += "\n\n[TRUNCATED_BY_missingFieldRefiner]"

    return text


# ВЫЗОВ FLOWISE И ПАРСИНГ


### разбор структурированного ответа flowise
def parse_structured_flowise_answer(answer_text: str) -> tuple:
    if not answer_text or not answer_text.strip():
        return None, None, "Пустой ответ Flowise."

    try:
        parsed_answer = json.loads(answer_text)
    except json.JSONDecodeError as error:
        return None, None, f"Ответ Flowise не является валидным JSON: {error}"

    structured_answer = None

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


### вызов extractor через flowise
def call_refinement_extractor(*, extraction_type: str, input_text: str) -> Any:
    return query_flowise_form(
        task_type="schemaExtraction",
        input_text=input_text,
        extraction_type=extraction_type,
        timeout=300,
    )


### выполнение дозаполнения через один extractor
def refine_one_extractor(
    *,
    extraction_type: str,
    contexts: Sequence[dict[str, Any]],
    header: Any = None,
    case_context: Any = None,
    entity_map: dict[str, Any] = None,
    retries: int = REFINE_RETRIES,
) -> dict[str, Any]:
    target_paths = [str(item.get("path") or "").strip() for item in contexts if str(item.get("path") or "").strip()]
    input_text = build_refinement_input(
        extraction_type=extraction_type,
        contexts=contexts,
        header=header,
        case_context=case_context,
        entity_map=entity_map,
    )

    last_error = None

    for attempt in range(1, max(1, retries) + 1):
        if REFINE_CALL_DELAY_SECONDS > 0:
            time.sleep(REFINE_CALL_DELAY_SECONDS)

        try:
            response = call_refinement_extractor(extraction_type=extraction_type, input_text=input_text)
            answer_text = extract_text_from_flowise_response(response)
            answer_json, structured_answer, parse_error = parse_structured_flowise_answer(answer_text)

            return {
                "extraction_type": extraction_type,
                "target_sections": EXTRACTOR_TARGET_SECTIONS.get(extraction_type, []),
                "target_paths": target_paths,
                "refill_contexts_count": len(contexts),
                "input_text_length": len(input_text),
                "answer_text": answer_text,
                "answer_json": answer_json,
                "structured_answer": structured_answer,
                "parse_error": parse_error,
                "error": None,
                "attempts": attempt,
                "source_contexts": [compact_context_for_llm(item) for item in contexts],
            }
        except Exception as error:
            # обработка ошибки вызова extractor
            last_error = str(error)
            if attempt < retries:
                time.sleep(REFINE_RETRY_SLEEP_SECONDS)

    return {
        "extraction_type": extraction_type,
        "target_sections": EXTRACTOR_TARGET_SECTIONS.get(extraction_type, []),
        "target_paths": target_paths,
        "refill_contexts_count": len(contexts),
        "input_text_length": len(input_text),
        "answer_text": "",
        "answer_json": None,
        "structured_answer": None,
        "parse_error": None,
        "error": last_error or "unknown refinement error",
        "attempts": max(1, retries),
        "source_contexts": [compact_context_for_llm(item) for item in contexts],
    }


# ПУБЛИЧНЫЙ API


### дозаполнение проблемных полей
def refine_missing_fields(
    *,
    refill_report,
    header: Any = None,
    case_context: Any = None,
    entity_map: dict[str, Any] = None,
    max_workers: int = MAX_REFINE_WORKERS,
    retries: int = REFINE_RETRIES,
) -> dict[str, Any]:
    grouped = group_refill_contexts_by_extractor(refill_report)

    if not grouped:
        return {
            "refinement_results": [],
            "stats": {
                "extractors_called": 0,
                "input_contexts_count": len(normalize_refill_report(refill_report)),
                "usable_contexts_count": 0,
                "total_input_chars": 0,
                "errors_count": 0,
            },
        }

    items = list(grouped.items())
    workers = max(1, min(max_workers, len(items)))
    results_by_index: dict[int, dict[str, Any]] = {}

    if workers == 1:
        for index, (extraction_type, contexts) in enumerate(items):
            results_by_index[index] = refine_one_extractor(
                extraction_type=extraction_type,
                contexts=contexts,
                header=header,
                case_context=case_context,
                entity_map=entity_map,
                retries=retries,
            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_index = {
                executor.submit(
                    refine_one_extractor,
                    extraction_type=extraction_type,
                    contexts=contexts,
                    header=header,
                    case_context=case_context,
                    entity_map=entity_map,
                    retries=retries,
                ): index
                for index, (extraction_type, contexts) in enumerate(items)
            }

            for future in as_completed(future_to_index):
                index = future_to_index[future]
                extraction_type, contexts = items[index]
                try:
                    results_by_index[index] = future.result()
                except Exception as error:
                    results_by_index[index] = {
                        "extraction_type": extraction_type,
                        "target_sections": EXTRACTOR_TARGET_SECTIONS.get(extraction_type, []),
                        "target_paths": [str(item.get("path") or "") for item in contexts],
                        "refill_contexts_count": len(contexts),
                        "answer_text": "",
                        "answer_json": None,
                        "structured_answer": None,
                        "parse_error": None,
                        "error": str(error),
                        "source_contexts": [compact_context_for_llm(item) for item in contexts],
                    }

    results = [results_by_index[index] for index in range(len(items))]

    return {
        "refinement_results": results,
        "stats": {
            "extractors_called": len(results),
            "input_contexts_count": len(normalize_refill_report(refill_report)),
            "usable_contexts_count": sum(len(contexts) for contexts in grouped.values()),
            "total_input_chars": sum(int(item.get("input_text_length") or 0) for item in results),
            "errors_count": sum(1 for item in results if item.get("error") or item.get("parse_error")),
        },
    }


# РАБОТА С ФАЙЛАМИ


### загрузка json
def load_json(path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


### сохранение json
def save_json(data: Any, path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


### дозаполнение проблемных полей из файлов
def refine_missing_fields_from_file(
    *,
    refill_report_path,
    output_path,
    header_path=None,
    case_context_path=None,
    entity_map_path=None,
    max_workers: int = MAX_REFINE_WORKERS,
) -> dict[str, Any]:
    refill_report = load_json(refill_report_path)
    header = load_json(header_path) if header_path else None
    case_context = load_json(case_context_path) if case_context_path else None
    entity_map = load_json(entity_map_path) if entity_map_path else None

    report = refine_missing_fields(
        refill_report=refill_report,
        header=header,
        case_context=case_context,
        entity_map=entity_map,
        max_workers=max_workers,
    )
    save_json(report, output_path)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Call Flowise extractors to refine missing fields using targeted context.")
    parser.add_argument("--refill-report", required=True, help="JSON from evidenceContextExpander/build_refill_contexts")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--header", help="Optional header JSON path")
    parser.add_argument("--case-context", help="Optional case_context JSON path")
    parser.add_argument("--entity-map", help="Optional entity_map JSON path")
    parser.add_argument("--workers", type=int, default=MAX_REFINE_WORKERS)
    args = parser.parse_args()

    refine_missing_fields_from_file(
        refill_report_path=args.refill_report,
        output_path=args.output,
        header_path=args.header,
        case_context_path=args.case_context,
        entity_map_path=args.entity_map,
        max_workers=args.workers,
    )
