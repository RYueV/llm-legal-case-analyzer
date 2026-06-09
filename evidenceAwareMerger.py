import argparse
import difflib
import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


# КОНСТАНТЫ

CARD_ORDER = [
    "caseSkeletonCard",
    "claimAndDealCard",
    "creditorStatusCard",
    "debtorCrisisCard",
    "proofAndEvidenceCard",
    "courtAssessmentCard",
]

EMPTY_MARKERS = {"", "—", "-", "none", "null", "нет", "нет данных"}

SEND_TO_RE = re.compile(
    r"^\s*\[\s*send_to\s*:\s*([A-Za-zА-Яа-яЁё0-9_]+)\s*\]\s*$",
    flags=re.IGNORECASE,
)

SECTION_RE = re.compile(
    r"^\s*(WINDOW_ID|ENTITY_MAP|CARD_BLOCKS)\s*:\s*(.*)$",
    flags=re.IGNORECASE,
)


# МОДЕЛИ ДАННЫХ

@dataclass
class ParsedAnnotation:
    window_id: str
    entity_map: dict[str, list[str]] = field(default_factory=dict)
    cards: dict[str, list[str]] = field(default_factory=dict)
    raw_text: str = ""
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class MergedCardFragment:
    text: str
    source_window_ids: list[str]
    source_zone_ids: list[str] = field(default_factory=list)
    source_zone_types: list[str] = field(default_factory=list)
    source_dates: list[str] = field(default_factory=list)
    source_sentence_ids: list[str] = field(default_factory=list)
    source_date_sentence_ids: list[str] = field(default_factory=list)


@dataclass
class MergedCards:
    entity_map: dict[str, list[str]]
    cards: dict[str, list[MergedCardFragment]]
    stats: dict[str, Any]


# НОРМАЛИЗАЦИЯ

### нормализация пробелов и базовых символов
def normalize_space(
    text: Any, # исходное значение
) -> str:
    value = str(text or "")
    value = value.replace("\xa0", " ")
    value = value.replace("\u202f", " ")
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    value = value.replace("«", '"').replace("»", '"')
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


### нормализация текста для поиска дублей
def normalize_for_dedup(
    text: Any, # исходное значение
) -> str:
    value = normalize_space(text).lower().replace("ё", "е")

    # удаление маркера списка
    value = re.sub(r"^[*\-\u2022]\s*", "", value)

    # унификация пробелов и кавычек
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[\"'`]", "", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)

    return value.strip(" .;,:")


### сохранение уникальных значений в исходном порядке
def unique_keep_order(
    values: Iterable[Any], # исходные значения
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    # последовательная фильтрация повторов
    for value in values:
        item = normalize_space(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)

    return result


# РАБОТА С ENTITY MAP

### разбор строки сущности и ролей
def split_entity_line(
    line: str, # строка entity map
) -> Any:
    line = normalize_space(line)
    if not line or line.lower() in EMPTY_MARKERS:
        return None

    # выбор разделителя имени и ролей
    if "—" in line:
        name, roles_text = line.split("—", 1)
    elif " - " in line:
        name, roles_text = line.split(" - ", 1)
    else:
        return None

    name = normalize_space(name).strip(":-— ")
    roles_text = normalize_space(roles_text).strip(":-— ")

    if not name or not roles_text:
        return None

    # разбор ролей через точку с запятой
    roles = [
        normalize_space(role).strip(" .;,")
        for role in re.split(r"\s*;\s*", roles_text)
        if normalize_space(role).strip(" .;,")
    ]

    if not roles:
        return None

    return name, roles


### проверка общей роли без содержательной связи
def is_generic_role(
    role: str, # роль сущности
) -> bool:
    norm = normalize_for_dedup(role)
    return norm in {
        "физическое лицо",
        "юридическое лицо",
        "индивидуальный предприниматель",
        "организация",
        "лицо",
    }


### оценка содержательности роли
def role_score(
    role: str, # роль сущности
) -> int:
    norm = normalize_for_dedup(role)
    score = 0

    # понижение общих ролей
    if is_generic_role(role):
        score -= 10

    important_markers = [
        "кредитор",
        "должник",
        "заявител",
        "заимодав",
        "займодав",
        "заемщик",
        "цедент",
        "цессионар",
        "поручител",
        "залог",
        "участник",
        "учредител",
        "директор",
        "руководител",
        "управляющ",
        "супруг",
        "аффилирован",
        "контролир",
        "банкрот",
        "покупател",
        "продав",
        "правопреем",
    ]

    # повышение ролей с юридически значимыми маркерами
    for marker in important_markers:
        if marker in norm:
            score += 5

    # учет длины описания роли
    score += min(len(norm.split()), 6)

    return score


### объединение ролей одной сущности
def merge_roles(
    existing: list[str], # уже сохраненные роли
    incoming: list[str], # новые роли
) -> list[str]:
    roles = unique_keep_order([*existing, *incoming])

    # удаление общих ролей при наличии содержательных
    has_specific = any(not is_generic_role(role) for role in roles)
    if has_specific:
        roles = [role for role in roles if not is_generic_role(role)]

    return sorted(roles, key=lambda item: (-role_score(item), normalize_for_dedup(item)))


# РАЗБОР ANNOTATION

### разбор текстовой аннотации окна
def parse_annotation_text(
    text: str, # текст аннотации
    fallback_window_id: str = "", # запасной идентификатор окна
) -> ParsedAnnotation:
    raw_text = normalize_space(text)
    parsed = ParsedAnnotation(window_id=fallback_window_id or "", raw_text=raw_text)

    current_section = None
    current_card = None
    current_card_lines: list[str] = []

    # сохранение накопленного текста карточки
    def flush_card() -> None:
        nonlocal current_card_lines, current_card

        if current_card:
            body = normalize_space("\n".join(current_card_lines))
            if body and normalize_for_dedup(body) not in EMPTY_MARKERS:
                parsed.cards.setdefault(current_card, []).append(body)

        current_card_lines = []

    # построчный разбор секций
    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip()

        section_match = SECTION_RE.match(line)
        if section_match:
            section_name = section_match.group(1).upper()
            section_tail = normalize_space(section_match.group(2))

            if current_section == "CARD_BLOCKS":
                flush_card()
                current_card = None

            current_section = section_name

            if section_name == "WINDOW_ID":
                if section_tail:
                    parsed.window_id = section_tail
            elif section_name == "ENTITY_MAP":
                if section_tail and section_tail.lower() not in EMPTY_MARKERS:
                    entity = split_entity_line(section_tail)
                    if entity:
                        name, roles = entity
                        parsed.entity_map[name] = merge_roles(parsed.entity_map.get(name, []), roles)
            continue

        if current_section == "WINDOW_ID":
            value = normalize_space(line)
            if value and value.lower() not in EMPTY_MARKERS and not parsed.window_id:
                parsed.window_id = value
            continue

        if current_section == "ENTITY_MAP":
            entity = split_entity_line(line)
            if entity:
                name, roles = entity
                parsed.entity_map[name] = merge_roles(parsed.entity_map.get(name, []), roles)
            continue

        if current_section == "CARD_BLOCKS":
            send_to_match = SEND_TO_RE.match(line)
            if send_to_match:
                flush_card()
                current_card = send_to_match.group(1)
                current_card_lines = []
                continue

            if current_card:
                current_card_lines.append(line)

    if current_section == "CARD_BLOCKS":
        flush_card()

    # запасной идентификатор при отсутствии window id
    if not parsed.window_id:
        parsed.window_id = fallback_window_id or "unknown_window"
        parsed.parse_warnings.append("Не найден WINDOW_ID, использован fallback.")

    return parsed


### разбиение карточки на фрагменты
def split_card_text_into_fragments(
    text: str, # текст карточки
) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []

    # сохранение абзацной структуры
    paragraph_parts = [
        normalize_space(part)
        for part in re.split(r"\n\s*\n+", text)
        if normalize_space(part)
    ]

    if len(paragraph_parts) > 1:
        return paragraph_parts

    # разбиение маркированного списка по строкам
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    bullet_lines = [
        re.sub(r"^[*\-\u2022]\s*", "", line).strip()
        for line in lines
        if re.match(r"^\s*[*\-\u2022]\s+", line)
    ]

    if bullet_lines and len(bullet_lines) == len(lines):
        return [line for line in bullet_lines if line]

    return [text]


# ОБЪЕДИНЕНИЕ КАРТОЧЕК

### проверка фрагментов на дублирование
def looks_like_duplicate(
    a: str, # первый фрагмент
    b: str, # второй фрагмент
    *,
    threshold: float = 0.92, # порог сходства
) -> bool:
    first = normalize_for_dedup(a)
    second = normalize_for_dedup(b)

    if not first or not second:
        return False

    if first == second:
        return True

    # проверка вложения длинных фрагментов
    if len(first) > 80 and len(second) > 80:
        if first in second or second in first:
            shorter = min(len(first), len(second))
            longer = max(len(first), len(second))
            if shorter / longer >= 0.72:
                return True

    return difflib.SequenceMatcher(None, first, second).ratio() >= threshold


### выбор более информативного фрагмента
def choose_better_fragment(
    existing: str, # сохраненный фрагмент
    candidate: str, # новый фрагмент
) -> str:
    # оценка информативности фрагмента
    def score(text: str) -> int:
        norm = normalize_space(text)
        lower = norm.lower()

        result = min(len(norm), 1200) // 20
        result += len(re.findall(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", norm)) * 3
        result += len(re.findall(r"\d[\d\s]*(?:,\d+)?\s*(?:руб|%)", lower)) * 3

        relation_markers = [
            "подтвержда",
            "свидетельств",
            "установ",
            "опроверга",
            "следует",
            "исходил",
            "пришел к выводу",
            "признал",
            "отказал",
            "не подтвержда",
            "доказыва",
        ]

        # учет маркеров доказательственной связи
        result += sum(4 for marker in relation_markers if marker in lower)

        return result

    return candidate if score(candidate) > score(existing) else existing


### извлечение метаданных аннотации
def _annotation_meta(
    annotation: dict[str, Any], # исходная аннотация
    parsed: ParsedAnnotation, # результат разбора аннотации
) -> dict[str, Any]:
    return {
        "window_id": str(annotation.get("window_id") or parsed.window_id or "unknown_window"),
        "zone_id": str(annotation.get("zone_id") or ""),
        "zone_type": str(annotation.get("zone_type") or ""),
        "dates": [str(item) for item in (annotation.get("dates") or [])],
        "sentence_ids": [str(item) for item in (annotation.get("sentence_ids") or [])],
        "date_sentence_ids": [str(item) for item in (annotation.get("date_sentence_ids") or [])],
    }


### объединение аннотаций окон в общие карточки
def merge_window_annotations(
    annotations: Sequence[dict[str, Any]], # аннотации окон из app.py
    *,
    duplicate_threshold: float = 0.92, # порог удаления дублей
) -> dict[str, Any]:
    merged_entities: dict[str, list[str]] = {}
    merged_cards: dict[str, list[MergedCardFragment]] = {card: [] for card in CARD_ORDER}

    parsed_count = 0
    empty_count = 0
    warnings: list[str] = []

    # последовательный разбор аннотаций
    for annotation in annotations:
        raw_text = normalize_space(annotation.get("annotation_text", ""))

        if not raw_text:
            empty_count += 1
            continue

        fallback_window_id = str(annotation.get("window_id") or "")
        parsed = parse_annotation_text(raw_text, fallback_window_id=fallback_window_id)
        parsed_count += 1

        if parsed.parse_warnings:
            warnings.extend(f"{parsed.window_id}: {warning}" for warning in parsed.parse_warnings)

        meta = _annotation_meta(annotation, parsed)

        # объединение карты сущностей
        for name, roles in parsed.entity_map.items():
            merged_entities[name] = merge_roles(merged_entities.get(name, []), roles)

        # объединение карточек с дедупликацией
        for card_name, card_texts in parsed.cards.items():
            if card_name not in merged_cards:
                merged_cards[card_name] = []

            for card_text in card_texts:
                for fragment_text in split_card_text_into_fragments(card_text):
                    fragment_text = normalize_space(fragment_text)
                    if not fragment_text:
                        continue

                    target_list = merged_cards[card_name]
                    duplicate_index = None

                    for index, existing in enumerate(target_list):
                        if looks_like_duplicate(existing.text, fragment_text, threshold=duplicate_threshold):
                            duplicate_index = index
                            break

                    if duplicate_index is None:
                        target_list.append(
                            MergedCardFragment(
                                text=fragment_text,
                                source_window_ids=[meta["window_id"]],
                                source_zone_ids=[meta["zone_id"]] if meta["zone_id"] else [],
                                source_zone_types=[meta["zone_type"]] if meta["zone_type"] else [],
                                source_dates=meta["dates"],
                                source_sentence_ids=meta["sentence_ids"],
                                source_date_sentence_ids=meta["date_sentence_ids"],
                            )
                        )
                    else:
                        existing = target_list[duplicate_index]
                        existing.text = choose_better_fragment(existing.text, fragment_text)
                        existing.source_window_ids = unique_keep_order([*existing.source_window_ids, meta["window_id"]])
                        existing.source_zone_ids = unique_keep_order([*existing.source_zone_ids, meta["zone_id"]])
                        existing.source_zone_types = unique_keep_order([*existing.source_zone_types, meta["zone_type"]])
                        existing.source_dates = unique_keep_order([*existing.source_dates, *meta["dates"]])
                        existing.source_sentence_ids = unique_keep_order([*existing.source_sentence_ids, *meta["sentence_ids"]])
                        existing.source_date_sentence_ids = unique_keep_order([*existing.source_date_sentence_ids, *meta["date_sentence_ids"]])

    # сохранение порядка карточек
    ordered_cards: dict[str, list[MergedCardFragment]] = {}
    for card_name in CARD_ORDER:
        fragments = merged_cards.get(card_name) or []
        if fragments:
            ordered_cards[card_name] = fragments

    # добавление нестандартных карточек
    for card_name in sorted(set(merged_cards) - set(CARD_ORDER)):
        fragments = merged_cards.get(card_name) or []
        if fragments:
            ordered_cards[card_name] = fragments

    stats = {
        "input_annotations_count": len(annotations),
        "parsed_annotations_count": parsed_count,
        "empty_annotations_count": empty_count,
        "entity_count": len(merged_entities),
        "card_count": len(ordered_cards),
        "fragment_count": sum(len(items) for items in ordered_cards.values()),
        "warnings": warnings,
    }

    result = MergedCards(
        entity_map=merged_entities,
        cards=ordered_cards,
        stats=stats,
    )

    return merged_cards_to_dict(result)


### преобразование объединенных карточек в словарь
def merged_cards_to_dict(
    merged: MergedCards, # объединенные карточки
) -> dict[str, Any]:
    return {
        "entity_map": {
            name: roles
            for name, roles in merged.entity_map.items()
        },
        "cards": {
            card_name: [asdict(fragment) for fragment in fragments]
            for card_name, fragments in merged.cards.items()
        },
        "stats": merged.stats,
    }


# ФОРМАТИРОВАНИЕ

### форматирование источника фрагмента
def format_fragment_source(
    fragment: dict[str, Any], # фрагмент карточки
) -> str:
    zone_types = ", ".join(fragment.get("source_zone_types") or [])
    windows = ", ".join(fragment.get("source_window_ids") or [])
    zone_ids = ", ".join(fragment.get("source_zone_ids") or [])
    sentence_ids = ", ".join(fragment.get("source_sentence_ids") or [])
    dates = ", ".join(fragment.get("source_dates") or [])

    parts: list[str] = []

    # сбор доступных частей источника
    if zone_types:
        parts.append(f"zone={zone_types}")
    if windows:
        parts.append(f"window={windows}")
    if zone_ids:
        parts.append(f"zone_id={zone_ids}")
    if sentence_ids:
        parts.append(f"sentences={sentence_ids}")
    if dates:
        parts.append(f"dates={dates}")

    return "; ".join(parts)


### рендеринг объединенных карточек в текст
def render_merged_cards_text(
    merged: dict[str, Any], # объединенные карточки
    *,
    include_sources: bool = True, # добавление строк источников
) -> str:
    lines: list[str] = []

    # рендеринг карты сущностей
    lines.append("ENTITY_MAP:")
    entity_map = merged.get("entity_map") or {}

    if entity_map:
        for name, roles in entity_map.items():
            role_text = "; ".join(str(role) for role in roles if str(role).strip())
            lines.append(f"{name} — {role_text}")
    else:
        lines.append("")

    lines.append("")
    lines.append("CARD_BLOCKS:")

    # рендеринг блоков карточек
    cards = merged.get("cards") or {}
    for card_name, fragments in cards.items():
        lines.append("")
        lines.append(f"[send_to: {card_name}]")

        for fragment in fragments:
            text = normalize_space(fragment.get("text", ""))
            if not text:
                continue

            lines.append(text)

            if include_sources:
                source_line = fragment.get("source_ref") or format_fragment_source(fragment)

                if source_line:
                    lines.append(f"[source: {source_line}]")

            lines.append("")

    return "\n".join(lines).strip()


### рендеринг объединенных карточек в html
def render_merged_cards_html(
    merged: dict[str, Any], # объединенные карточки
) -> str:
    parts: list[str] = []

    # рендеринг статистики объединения
    stats = merged.get("stats") or {}
    if stats:
        parts.append(
            """
            <div class="merge-stats">
                <b>Статистика объединения:</b>
                <span>окон: {input_count}</span>
                <span>разобрано: {parsed_count}</span>
                <span>сущностей: {entity_count}</span>
                <span>карточек: {card_count}</span>
                <span>фрагментов: {fragment_count}</span>
            </div>
            """.format(
                input_count=html.escape(str(stats.get("input_annotations_count", ""))),
                parsed_count=html.escape(str(stats.get("parsed_annotations_count", ""))),
                entity_count=html.escape(str(stats.get("entity_count", ""))),
                card_count=html.escape(str(stats.get("card_count", ""))),
                fragment_count=html.escape(str(stats.get("fragment_count", ""))),
            )
        )

    # рендеринг карты сущностей
    entity_map = merged.get("entity_map") or {}
    entity_lines = []
    for name, roles in entity_map.items():
        role_text = "; ".join(str(role) for role in roles if str(role).strip())
        entity_lines.append(
            f"<div><b>{html.escape(name)}</b> — {html.escape(role_text)}</div>"
        )

    parts.append(
        f"""
        <div class="merged-section">
            <h2>ENTITY_MAP</h2>
            {''.join(entity_lines) if entity_lines else '<p class="muted">Нет сущностей.</p>'}
        </div>
        """
    )

    # рендеринг карточек и источников
    cards = merged.get("cards") or {}
    for card_name, fragments in cards.items():
        fragment_html: list[str] = []

        for fragment in fragments:
            text = html.escape(normalize_space(fragment.get("text", "")))
            if not text:
                continue

            source_line = fragment.get("source_ref") or format_fragment_source(fragment)

            source_html = ""
            if source_line:
                source_html = f"<div class='source'>{html.escape(source_line)}</div>"

            fragment_html.append(
                f"""
                <div class="merged-fragment">
                    <div class="fragment-text">{text}</div>
                    {source_html}
                </div>
                """
            )

        parts.append(
            f"""
            <div class="merged-section">
                <h2>{html.escape(card_name)}</h2>
                {''.join(fragment_html)}
            </div>
            """
        )

    return "\n".join(parts)


# СЕРИАЛИЗАЦИЯ

### сохранение объединенных карточек в json
def save_merged_cards_json(
    merged: dict[str, Any], # объединенные карточки
    output_path: str, # путь сохранения
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


### загрузка объединенных карточек из json
def load_merged_cards_json(
    input_path: str, # путь к json
) -> dict[str, Any]:
    return json.loads(Path(input_path).read_text(encoding="utf-8"))


### объединение аннотаций из json файла
def merge_annotations_json_file(
    input_path: str, # путь к входному json
    output_path: str, # путь к выходному json
) -> dict[str, Any]:
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))

    # выбор формата входных данных
    if isinstance(data, dict):
        annotations = data.get("window_annotations") or []
    elif isinstance(data, list):
        annotations = data
    else:
        raise ValueError("Ожидался JSON-объект или JSON-массив.")

    if not isinstance(annotations, list):
        raise ValueError("window_annotations должен быть массивом.")

    merged = merge_window_annotations(annotations)
    save_merged_cards_json(merged, output_path)

    return merged


# ОТЛАДКА

### запуск объединения из командной строки
def main() -> None:
    parser = argparse.ArgumentParser(description="Merge windowAnnotation results")
    parser.add_argument("input", help="JSON with window_annotations or annotation list")
    parser.add_argument("output", help="Output JSON path")
    args = parser.parse_args()

    merge_annotations_json_file(args.input, args.output)


if __name__ == "__main__":
    main()
