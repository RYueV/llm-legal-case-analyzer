import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


# КОНСТАНТЫ

NEAR_SEARCH_RADIUS = 3
CONTEXT_RADIUS = 1
MAX_CONTEXT_WINDOWS_PER_FIELD = 2
MAX_CHARS_PER_FIELD = 2200
MAX_TOTAL_REFILL_CHARS = 8000


# ПРОФИЛИ ПОИСКА

DEFAULT_PROFILE = {
    "cards": [],
    "zones": ["court_reasoning"],
    "keywords": [],
    "max_windows": MAX_CONTEXT_WINDOWS_PER_FIELD,
}

FIELD_SEARCH_PROFILES: dict[str, dict[str, Any]] = {
    "metadata.case_number": {
        "cards": ["caseSkeletonCard"],
        "zones": ["header"],
        "keywords": ["дело", "номер дела"],
    },
    "metadata.act_date": {
        "cards": ["caseSkeletonCard"],
        "zones": ["header"],
        "keywords": ["постановление", "определение", "решение", "дата"],
    },
    "metadata.court_name": {
        "cards": ["caseSkeletonCard"],
        "zones": ["header"],
        "keywords": ["арбитражный суд", "суд"],
    },
    "procedure.dispute_object": {
        "cards": ["caseSkeletonCard"],
        "zones": ["header", "procedural_history", "court_reasoning"],
        "keywords": ["включении", "реестр", "требован", "очередност", "субординац", "правопреемств", "жалоб"],
    },
    "claim.claim_type": {
        "cards": ["claimAndDealCard"],
        "zones": ["procedural_history", "court_reasoning", "quoted_document"],
        "keywords": ["договор", "заем", "займ", "поставка", "аренд", "подряд", "услуг", "поручител", "гарант", "суброгац", "цесс", "уступк", "реституц", "оплат", "долг"],
    },
    "claim.claim_origin": {
        "cards": ["claimAndDealCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["реальн", "финансирован", "компенсацион", "корпоративн", "обычн", "искусствен", "внутригрупп", "аффилирован"],
    },
    "claim.emergence_timing": {
        "cards": ["claimAndDealCard", "debtorCrisisCard"],
        "zones": ["court_reasoning", "procedural_history", "quoted_document"],
        "keywords": ["возник", "заключ", "перечисл", "передан", "дата", "до", "после", "период", "банкрот", "кризис"],
    },
    "creditor_profile.creditor_status": {
        "cards": ["creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["аффилирован", "контролир", "связан", "заинтересован", "группа лиц", "бенефициар", "независим", "общий интерес"],
    },
    "creditor_profile.formal_affiliation_found": {
        "cards": ["creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["участник", "учредител", "доля", "акци", "директор", "руководител", "супруг", "родствен", "группа лиц", "аффилирован"],
    },
    "creditor_profile.factual_affiliation_found": {
        "cards": ["creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["фактическ", "связан", "согласован", "общий интерес", "экономическ", "зависим", "номинальн", "совместн", "аффилирован"],
    },
    "creditor_profile.control_found": {
        "cards": ["creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["контролир", "контроль", "определять", "решающее влияние", "фактическое управление", "бенефициар", "кдл", "контролирующее должника лицо"],
    },
    "creditor_profile.connection_basis_primary": {
        "cards": ["creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["участник", "доля", "директор", "руководител", "группа", "бенефициар", "супруг", "зависим", "согласован", "контрол"],
    },
    "crisis.crisis_status": {
        "cards": ["debtorCrisisCard"],
        "zones": ["court_reasoning"],
        "keywords": ["кризис", "неплатежеспособ", "недостаточность имущества", "признаки банкротства", "объективное банкротство", "имущественное положение", "финансовое состояние"],
    },
    "crisis.creditor_knew_crisis": {
        "cards": ["debtorCrisisCard", "creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["знал", "известно", "осведомлен", "понимал", "кризис", "неплатежеспособ", "банкротств"],
    },
    "crisis.creditor_should_have_known_crisis": {
        "cards": ["debtorCrisisCard", "creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["должен был знать", "мог знать", "не мог не знать", "осмотрительн", "аффилирован", "кризис", "неплатежеспособ"],
    },
    "behavior.new_financing_provided": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["финансирован", "предоставил", "перечислил", "заем", "займ", "денежные средства", "имущество", "ресурс"],
    },
    "behavior.left_funds_in_debtor": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning"],
        "keywords": ["оставил", "не взыскивал", "не требовал", "сохранил", "моратор", "отсроч", "ресурс", "денежные средства"],
    },
    "behavior.did_not_demand_repayment": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning"],
        "keywords": ["не требовал", "не взыскивал", "не предъявлял", "воздерж", "длительное время", "просроч"],
    },
    "behavior.changed_contract_terms": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["изменил", "изменение условий", "дополнительное соглашение", "новая редакция", "условия договора"],
    },
    "behavior.extended_maturity": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["продлил", "пролонг", "отсроч", "срок возврата", "срок исполнения", "перенес"],
    },
    "behavior.obtained_security": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["залог", "ипотек", "поручител", "обеспеч", "гарант", "получил обеспечение"],
    },
    "behavior.obtained_preference": {
        "cards": ["creditorStatusCard", "claimAndDealCard"],
        "zones": ["court_reasoning"],
        "keywords": ["преимуществен", "предпочтение", "приоритет", "лучшее положение", "перед другими кредиторами", "контроль над процедурой"],
    },
    "behavior.participated_in_management": {
        "cards": ["creditorStatusCard"],
        "zones": ["court_reasoning"],
        "keywords": ["управлен", "руководил", "директор", "орган управления", "фактическое управление", "оперативное управление", "решения должника"],
    },
    "behavior.acted_as_market_creditor": {
        "cards": ["creditorStatusCard", "claimAndDealCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["рыночн", "обычн", "коммерческ", "независимый кредитор", "разумн", "экономический смысл", "деловая цель"],
    },
    "market_terms.interest_terms": {
        "cards": ["claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["процент", "ставк", "беспроцент", "рыночн", "ниже", "выше"],
    },
    "market_terms.maturity_terms": {
        "cards": ["claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["срок", "возврат", "исполнен", "продл", "отсроч", "аномальн", "краткосроч"],
    },
    "market_terms.security_terms": {
        "cards": ["claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["залог", "ипотек", "поручител", "гарант", "обеспечен", "необеспечен"],
    },
    "market_terms.economic_sense_present": {
        "cards": ["claimAndDealCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["экономический смысл", "деловая цель", "разумн", "рыночн", "коммерческ", "обычн", "целесообразн"],
    },
    "market_terms.market_conditions_overall": {
        "cards": ["claimAndDealCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["рыночн", "нерыночн", "обычн", "отклон", "условия", "проценты", "срок", "обеспеч"],
    },
    "proof.performance_fact_proven": {
        "cards": ["proofAndEvidenceCard", "claimAndDealCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["исполнение", "передач", "перечисл", "поставк", "оказан", "выполнен", "подтвержден", "доказан"],
    },
    "proof.documents_present": {
        "cards": ["proofAndEvidenceCard"],
        "zones": ["court_reasoning", "quoted_document"],
        "keywords": ["документ", "договор", "акт", "накладн", "платеж", "поручение", "первичн", "подтвержд"],
    },
    "proof.documents_contradictory": {
        "cards": ["proofAndEvidenceCard"],
        "zones": ["court_reasoning"],
        "keywords": ["противореч", "несоглас", "сомнен", "расхожд", "не соответствует", "недостовер"],
    },
    "proof.primary_docs_absent": {
        "cards": ["proofAndEvidenceCard"],
        "zones": ["court_reasoning"],
        "keywords": ["первичные документы", "отсутств", "не представлен", "не приложен", "не подтвержден"],
    },
    "proof.court_recognized_debt_real": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["реальн", "существовал", "подтвержден", "доказан", "не является мним", "не искусствен"],
    },
    "proof.court_doubted_reality": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["сомнен", "нереальн", "не подтвержден", "недоказан", "искусствен", "мним", "фиктив"],
    },
    "proof.court_found_sham_or_artificial": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["мним", "притвор", "фиктив", "искусствен", "создание задолженности", "нереальн", "формальн"],
    },
    "proof_burden.heightened_standard_applied": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["повышенный стандарт", "бремя доказывания", "разумные сомнения", "раскрыть", "опровергнуть", "аффилирован"],
    },
    "proof_burden.burden_to_remove_doubts_on_creditor": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["снять сомнения", "разумные сомнения", "бремя", "кредитор обязан", "доказать"],
    },
    "proof_burden.presumption_comp_financing_applied": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["компенсационное финансирование", "презумпц", "субординац", "контролир", "аффилирован"],
    },
    "proof_burden.creditor_rebutted_doubts": {
        "cards": ["proofAndEvidenceCard", "courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["сомнения", "устран", "снят", "опроверг", "не опроверг", "доказал", "не доказал"],
    },
    "qualification.legal_qualification": {
        "cards": ["courtAssessmentCard"],
        "zones": ["court_reasoning", "operative_part"],
        "keywords": ["включить", "отказать", "понизить", "субординац", "очередност", "текущ", "корпоративн", "направить", "отменить", "оставить"],
    },
    "qualification.key_reason_category": {
        "cards": ["courtAssessmentCard"],
        "zones": ["court_reasoning"],
        "keywords": ["компенсацион", "корпоративн", "мним", "искусствен", "недоказан", "кризис", "рыночн", "аффилирован", "контрол", "формальн", "новое рассмотрение"],
    },
}


# МОДЕЛИ ДАННЫХ


@dataclass
class ContextWindow:
    source: str
    text: str
    sentence_ids: list[str] = field(default_factory=list)
    zone_type: str = ""
    zone_id: str = ""
    page_start: Any = None
    page_end: Any = None
    matched_keyword: str = ""
    search_stage: str = ""


# НОРМАЛИЗАЦИЯ

### нормализация текста
def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\xa0", " ").replace("\u202f", " ")
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


### нормализация ключа для сопоставления
def normalize_key(value: Any) -> str:
    return normalize_text(value).lower().replace("ё", "е")


### удаление префикса annotation из пути
def strip_annotation_prefix(path: str) -> str:
    path = str(path or "").strip()
    if path.startswith("annotation."):
        return path[len("annotation."):]
    return path


### выбор профиля поиска для поля
def get_profile(path: str) -> dict[str, Any]:
    clean_path = strip_annotation_prefix(path)
    exact = FIELD_SEARCH_PROFILES.get(clean_path)
    if exact:
        return {**DEFAULT_PROFILE, **exact}

    # поиск профиля по разделу
    section = clean_path.split(".", 1)[0]
    for key, profile in FIELD_SEARCH_PROFILES.items():
        if key.startswith(section + "."):
            return {**DEFAULT_PROFILE, **profile}

    return dict(DEFAULT_PROFILE)


### построение регулярного выражения для ключевого слова
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(normalize_key(keyword))
    return re.compile(escaped, flags=re.IGNORECASE)


### поиск первого подходящего ключевого слова
def find_keyword(text: str, keywords: Sequence[str]) -> str:
    normalized = normalize_key(text)
    for keyword in keywords:
        if not keyword:
            continue
        if keyword_pattern(keyword).search(normalized):
            return keyword
    return ""


# РАБОТА С ПРЕДЛОЖЕНИЯМИ

### получение идентификатора предложения
def sentence_id_value(sentence: dict[str, Any]) -> str:
    return str(sentence.get("sentence_id") or sentence.get("id") or "")


### получение типа зоны предложения
def sentence_zone_type(sentence: dict[str, Any]) -> str:
    return str(sentence.get("zone_type") or sentence.get("zone") or "")


### получение идентификатора зоны предложения
def sentence_zone_id(sentence: dict[str, Any]) -> str:
    return str(sentence.get("zone_id") or "")


### получение текста предложения
def sentence_text(sentence: dict[str, Any]) -> str:
    for key in ("text", "sentence", "content"):
        if sentence.get(key):
            return str(sentence.get(key) or "")
    return ""


### построение индексов предложений
def build_sentence_indexes(sentences: Sequence[dict[str, Any]]) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    id_to_index: dict[str, int] = {}
    id_to_sentence: dict[str, dict[str, Any]] = {}

    for index, sentence in enumerate(sentences or []):
        sid = sentence_id_value(sentence)
        if not sid:
            continue
        id_to_index[sid] = index
        id_to_sentence[sid] = dict(sentence)

    return id_to_index, id_to_sentence


### проверка допустимости зоны предложения
def zone_allowed(sentence: dict[str, Any], allowed_zones: Sequence[str]) -> bool:
    if not allowed_zones:
        return True
    zone = normalize_key(sentence_zone_type(sentence))
    return zone in {normalize_key(item) for item in allowed_zones}


### формирование источника контекстного окна
def build_source_for_window(window_sentences: Sequence[dict[str, Any]]) -> str:
    if not window_sentences:
        return "none"

    zone_types = unique_keep_order(sentence_zone_type(item) for item in window_sentences if sentence_zone_type(item))
    zone_ids = unique_keep_order(sentence_zone_id(item) for item in window_sentences if sentence_zone_id(item))
    sentence_ids = unique_keep_order(sentence_id_value(item) for item in window_sentences if sentence_id_value(item))

    page_starts = [item.get("page_start") for item in window_sentences if item.get("page_start") is not None]
    page_ends = [item.get("page_end") for item in window_sentences if item.get("page_end") is not None]

    parts: list[str] = []
    if zone_types:
        parts.append(f"zone={', '.join(zone_types)}")
    if zone_ids:
        parts.append(f"zone_id={', '.join(zone_ids)}")
    if sentence_ids:
        parts.append(f"sentences={', '.join(sentence_ids)}")
    if page_starts or page_ends:
        start = min(page_starts) if page_starts else "?"
        end = max(page_ends) if page_ends else start
        parts.append(f"pages={start}-{end}")

    return "; ".join(parts) if parts else "none"


### удаление дублей с сохранением порядка
def unique_keep_order(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = normalize_text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


### построение контекстного окна вокруг предложения
def build_context_window(
    *,
    sentences: Sequence[dict[str, Any]],
    center_index: int,
    radius: int,
    matched_keyword: str,
    search_stage: str,
) -> ContextWindow:
    if center_index < 0 or center_index >= len(sentences):
        return None

    start = max(0, center_index - radius)
    end = min(len(sentences), center_index + radius + 1)
    window_sentences = [dict(item) for item in sentences[start:end]]
    texts = [sentence_text(item) for item in window_sentences if sentence_text(item).strip()]
    text = "\n".join(texts).strip()

    if not text:
        return None

    center_sentence = dict(sentences[center_index])
    return ContextWindow(
        source=build_source_for_window(window_sentences),
        text=text,
        sentence_ids=[sentence_id_value(item) for item in window_sentences if sentence_id_value(item)],
        zone_type=sentence_zone_type(center_sentence),
        zone_id=sentence_zone_id(center_sentence),
        page_start=center_sentence.get("page_start"),
        page_end=center_sentence.get("page_end"),
        matched_keyword=matched_keyword,
        search_stage=search_stage,
    )


# КООРДИНАТЫ ИЗ MERGED_CARDS

### перебор фрагментов выбранных карточек
def iter_card_fragments(merged_cards: dict[str, Any], card_names: Sequence[str]) -> Iterable[dict[str, Any]]:
    cards = (merged_cards or {}).get("cards") or {}
    for card_name in card_names:
        for fragment in cards.get(card_name) or []:
            if isinstance(fragment, dict):
                yield fragment


### сбор идентификаторов опорных предложений
def collect_anchor_sentence_ids(merged_cards: dict[str, Any], card_names: Sequence[str]) -> list[str]:
    ids: list[str] = []
    for fragment in iter_card_fragments(merged_cards, card_names):
        ids.extend(str(item) for item in fragment.get("source_sentence_ids") or [] if str(item).strip())
    return unique_keep_order(ids)


### построение индексов для поиска рядом с опорами
def nearby_search_indexes(
    *,
    anchor_ids: Sequence[str],
    id_to_index: dict[str, int],
    total_sentences: int,
    radius: int,
) -> list[int]:
    indexes: set[int] = set()
    for sentence_id in anchor_ids:
        if sentence_id not in id_to_index:
            continue
        anchor_index = id_to_index[sentence_id]
        for index in range(max(0, anchor_index - radius), min(total_sentences, anchor_index + radius + 1)):
            # расширение области поиска вокруг координаты карточки
            indexes.add(index)
    return sorted(indexes)


# ПОИСК КАНДИДАТОВ

### удаление дублей контекстных окон
def deduplicate_windows(windows: Sequence[ContextWindow]) -> list[ContextWindow]:
    result: list[ContextWindow] = []
    seen_sentence_sets: set[tuple[str, ...]] = set()
    seen_texts: set[str] = set()

    for window in windows:
        sentence_marker = tuple(window.sentence_ids)
        text_marker = normalize_key(window.text)

        if sentence_marker and sentence_marker in seen_sentence_sets:
            continue
        if text_marker and text_marker in seen_texts:
            continue

        if sentence_marker:
            seen_sentence_sets.add(sentence_marker)
        if text_marker:
            seen_texts.add(text_marker)

        result.append(window)

    return result


### ограничение количества и длины контекстных окон
def trim_windows_by_limits(
    windows: Sequence[ContextWindow],
    *,
    max_windows: int,
    max_chars: int,
) -> list[ContextWindow]:
    result: list[ContextWindow] = []
    chars = 0

    for window in windows:
        if len(result) >= max_windows:
            break
        length = len(window.text)
        if result and chars + length > max_chars:
            break
        if not result and length > max_chars:
            shortened = window.text[:max_chars].rstrip()
            window = ContextWindow(**{**asdict(window), "text": shortened})
            length = len(shortened)
        result.append(window)
        chars += length

    return result


### поиск контекста рядом с координатами карточек
def search_near_anchors(
    *,
    path: str,
    profile: dict[str, Any],
    merged_cards: dict[str, Any],
    sentences: Sequence[dict[str, Any]],
    id_to_index: dict[str, int],
) -> list[ContextWindow]:
    anchor_ids = collect_anchor_sentence_ids(merged_cards, profile.get("cards") or [])
    if not anchor_ids:
        return []

    candidate_indexes = nearby_search_indexes(
        anchor_ids=anchor_ids,
        id_to_index=id_to_index,
        total_sentences=len(sentences),
        radius=NEAR_SEARCH_RADIUS,
    )

    windows: list[ContextWindow] = []
    keywords = profile.get("keywords") or []
    allowed_zones = profile.get("zones") or []

    for index in candidate_indexes:
        sentence = sentences[index]
        if not zone_allowed(sentence, allowed_zones):
            continue

        keyword = find_keyword(sentence_text(sentence), keywords)
        if not keyword:
            continue

        window = build_context_window(
            sentences=sentences,
            center_index=index,
            radius=CONTEXT_RADIUS,
            matched_keyword=keyword,
            search_stage="near_card_coordinates",
        )
        if window:
            windows.append(window)

    return deduplicate_windows(windows)


### глобальный поиск контекста по ключевым словам
def search_globally_by_keywords(
    *,
    profile: dict[str, Any],
    sentences: Sequence[dict[str, Any]],
) -> list[ContextWindow]:
    windows: list[ContextWindow] = []
    keywords = profile.get("keywords") or []
    allowed_zones = profile.get("zones") or []

    if not keywords:
        return []

    for index, sentence in enumerate(sentences or []):
        if not zone_allowed(sentence, allowed_zones):
            continue

        keyword = find_keyword(sentence_text(sentence), keywords)
        if not keyword:
            continue

        window = build_context_window(
            sentences=sentences,
            center_index=index,
            radius=CONTEXT_RADIUS,
            matched_keyword=keyword,
            search_stage="global_keyword_search",
        )
        if window:
            windows.append(window)

    return deduplicate_windows(windows)


# ЗАДАЧИ ДОЗАПОЛНЕНИЯ

### сбор задач дозаполнения
def collect_refill_tasks(final_case=None, explicit_tasks=None) -> list[dict[str, Any]]:
    if explicit_tasks is not None:
        return [dict(item) for item in explicit_tasks if isinstance(item, dict)]

    audit = (final_case or {}).get("audit") or {}
    tasks: list[dict[str, Any]] = []

    for key in ("refill_tasks", "field_conflicts", "critical_conflicts", "logical_inconsistencies"):
        for item in audit.get(key) or []:
            if isinstance(item, dict):
                task = dict(item)
                task.setdefault("source_audit_block", key)
                tasks.append(task)

    return tasks


### получение пути поля из задачи дозаполнения
def task_path(task: dict[str, Any]) -> str:
    return str(task.get("path") or task.get("field_path") or "").strip()


### построение контекста для одной задачи дозаполнения
def build_refill_context_for_task(
    *,
    task: dict[str, Any],
    merged_cards: dict[str, Any],
    sentences: Sequence[dict[str, Any]],
    id_to_index: dict[str, int],
) -> dict[str, Any]:
    path = task_path(task)
    profile = get_profile(path)

    windows = search_near_anchors(
        path=path,
        profile=profile,
        merged_cards=merged_cards,
        sentences=sentences,
        id_to_index=id_to_index,
    )

    if len(windows) < int(profile.get("max_windows") or MAX_CONTEXT_WINDOWS_PER_FIELD):
        global_windows = search_globally_by_keywords(profile=profile, sentences=sentences)
        windows = deduplicate_windows([*windows, *global_windows])

    windows = trim_windows_by_limits(
        windows,
        max_windows=int(profile.get("max_windows") or MAX_CONTEXT_WINDOWS_PER_FIELD),
        max_chars=MAX_CHARS_PER_FIELD,
    )

    return {
        "path": path,
        "reason": task.get("reason") or task.get("source_audit_block") or "refill_required",
        "source_audit_block": task.get("source_audit_block"),
        "profile": {
            "cards_as_coordinates": profile.get("cards") or [],
            "zones": profile.get("zones") or [],
            "keywords": profile.get("keywords") or [],
            "near_search_radius": NEAR_SEARCH_RADIUS,
            "context_radius": CONTEXT_RADIUS,
            "max_windows": profile.get("max_windows") or MAX_CONTEXT_WINDOWS_PER_FIELD,
        },
        "context_fragments": [asdict(window) for window in windows],
        "context_chars": sum(len(window.text) for window in windows),
        "fragments_count": len(windows),
    }


### построение контекстов для дозаполнения
def build_refill_contexts(
    *,
    final_case=None,
    refill_tasks=None,
    merged_cards: dict[str, Any],
    sentences: Sequence[dict[str, Any]],
    max_total_chars: int = MAX_TOTAL_REFILL_CHARS,
) -> dict[str, Any]:
    tasks = collect_refill_tasks(final_case=final_case, explicit_tasks=refill_tasks)
    id_to_index, _ = build_sentence_indexes(sentences)

    contexts: list[dict[str, Any]] = []
    total_chars = 0

    for task in tasks:
        context = build_refill_context_for_task(
            task=task,
            merged_cards=merged_cards,
            sentences=sentences,
            id_to_index=id_to_index,
        )

        context_chars = int(context.get("context_chars") or 0)
        if contexts and total_chars + context_chars > max_total_chars:
            context["skipped"] = True
            context["skip_reason"] = "max_total_refill_chars_exceeded"
            contexts.append(context)
            continue

        total_chars += context_chars
        contexts.append(context)

    return {
        "refill_contexts": contexts,
        "stats": {
            "tasks_count": len(tasks),
            "contexts_count": len(contexts),
            "total_context_chars": total_chars,
            "max_total_chars": max_total_chars,
        },
    }


# ФОРМАТИРОВАНИЕ ДЛЯ LLM FALLBACK

### формирование текстового отчета для fallback
def render_refill_contexts_text(refill_report: dict[str, Any]) -> str:
    lines: list[str] = []
    contexts = refill_report.get("refill_contexts") or []

    for index, item in enumerate(contexts, start=1):
        lines.append(f"REFILL_TASK #{index}")
        lines.append(f"PATH: {item.get('path')}")
        lines.append(f"REASON: {item.get('reason')}")

        fragments = item.get("context_fragments") or []
        if not fragments:
            lines.append("CONTEXT: —")
            lines.append("")
            continue

        lines.append("CONTEXT_FRAGMENTS:")
        for frag_index, fragment in enumerate(fragments, start=1):
            lines.append(f"[{frag_index}] source: {fragment.get('source') or 'none'}")
            if fragment.get("matched_keyword"):
                lines.append(f"matched_keyword: {fragment.get('matched_keyword')}")
            lines.append(str(fragment.get("text") or ""))
            lines.append("")

    return "\n".join(lines).strip()


# РАБОТА С ФАЙЛАМИ ДЛЯ ОТЛАДКИ

### загрузка json из файла
def load_json(path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


### сохранение json в файл
def save_json(data: Any, path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


### построение контекстов дозаполнения из файлов
def build_refill_contexts_from_files(
    *,
    final_case_path,
    merged_cards_path,
    sentences_path,
    output_path,
) -> dict[str, Any]:
    final_case = load_json(final_case_path)
    merged_cards = load_json(merged_cards_path)
    sentences = load_json(sentences_path)

    report = build_refill_contexts(
        final_case=final_case,
        merged_cards=merged_cards,
        sentences=sentences,
    )
    save_json(report, output_path)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build targeted refill contexts from audit tasks.")
    parser.add_argument("--final-case", required=True, help="Path to finalStructuredCase JSON")
    parser.add_argument("--merged-cards", required=True, help="Path to merged_cards JSON")
    parser.add_argument("--sentences", required=True, help="Path to sentences JSON")
    parser.add_argument("--output", required=True, help="Output refill context JSON")
    args = parser.parse_args()

    build_refill_contexts_from_files(
        final_case_path=args.final_case,
        merged_cards_path=args.merged_cards,
        sentences_path=args.sentences,
        output_path=args.output,
    )
