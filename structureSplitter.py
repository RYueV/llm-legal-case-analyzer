import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ZoneType(str, Enum):
    HEADER = "header"
    PROCEDURAL_HISTORY = "procedural_history"
    PARTY_POSITION = "party_position"
    COURT_REASONING = "court_reasoning"
    OPERATIVE_PART = "operative_part"


@dataclass
class Sentence:
    idx: int
    page: int
    text: str
    norm_text: str
    start_offset: int
    end_offset: int


@dataclass
class PageSpan:
    page: int
    start_offset: int
    end_offset: int


# РЕГУЛЯРНЫЕ ВЫРАЖЕНИЯ

_USTANOVIL_MARKER_RE = re.compile(
    r"""
    (?:
        у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)? |
        с\s*у\s*д\s+у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л |
        установил(?:а)? |
        суд\s+установил(?:а)? |
        судом\s+установлено |
        установлено\s+следующее
    )
    \s*:?
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


_POSTANOVIL_MARKER_RE = re.compile(
    r"""
    ^[ \t]*
    (?:
        п\s*о\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)? |
        постановил(?:а)? |
        определил(?:а)? |
        решил(?:а)?
    )
    [ \t]*:?[ \t]*$
    """,
    flags=re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)


_PARTY_POSITION_START_RE = re.compile(
    r"""
    (?:
        \bв\s+кассационн(?:ой|ых)\s+жалоб(?:е|ах)\b |
        \bне\s+согласившись\s+с\s+(?:принят(?:ым|ыми)|судебн(?:ым|ыми)|постановлени(?:ем|ями)|определени(?:ем|ями)|решени(?:ем|ями))\b |
        \bобратил(?:ся|ась|ись)\s+с\s+кассационн(?:ой|ыми)\s+жалоб(?:ой|ами)\b |
        \bпо\s+мнению\s+(?:заявителя|подателя|кассатора)\b |
        \bподатель\s+жалоб[ы]?\s+ссылается\b |
        \bкассатор\s+указывает\b |
        \bзаявитель\s+указывает\b
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


_STRONG_COURT_REASONING_START_RE = re.compile(
    r"""
    (?:
        \b
        (?:
            изучив |
            исследовав |
            заслушав |
            рассмотрев |
            проверив |
            обсудив
        )
        [\s\S]{0,1400}?
        (?:
            судебная\s+коллегия(?:\s+суда\s+кассационной\s+инстанции)? |
            коллегия\s+суда\s+кассационной\s+инстанции |
            арбитражный\s+суд\s+[а-яё\s]+округа |
            суд\s+кассационной\s+инстанции |
            суд\s+округа
        )
        [\s\S]{0,400}?
        (?:
            приход(?:ит|ят)\s+к\s+следующим\s+выводам |
            приш[её]л\s+к\s+следующим\s+выводам |
            пришла\s+к\s+следующим\s+выводам |
            приходит\s+к\s+выводу |
            приш[её]л\s+к\s+выводу |
            не\s+находит(?:\s+[а-яё]+){0,3}\s+основани[йя] |
            не\s+усматривает\s+оснований
        )
        \b |

        \bпо\s+результатам\s+рассмотрения\s+кассационн(?:ой|ых)\s+жалоб(?:ы)?\s+
        суд\s+(?:округа|кассационной\s+инстанции)\s+
        приш[её]л\s+к\s+следующим\s+выводам\b |

        \bкассационн(?:ая|ые)\s+жалоб(?:а|ы)\s+
        не\s+подлеж(?:ит|ат)\s+удовлетворению\b |

        \bсуд\s+кассационной\s+инстанции\s+
        (?:считает|полагает|приходит\s+к\s+выводу|приходит\s+к\s+следующим\s+выводам)\b |

        \bсуд\s+округа\s+
        (?:считает|полагает|приходит\s+к\s+выводу|приходит\s+к\s+следующим\s+выводам|приш[её]л\s+к\s+выводу)\b |

        \bсуд\s+округа\s+призна[её]т\s+выводы\s+судов\s+обоснованными\b |

        \b(?:судебная\s+коллегия(?:\s+суда\s+кассационной\s+инстанции)?|
             коллегия\s+суда\s+кассационной\s+инстанции)\s+
        (?:считает|полагает|приходит\s+к\s+выводу|приходит\s+к\s+следующим\s+выводам|
           не\s+находит(?:\s+[а-яё]+){0,3}\s+основани[йя]|не\s+усматривает\s+основани[йя])\b |

        \bнарушений\s+норм\s+материального\s+и\s+процессуального\s+права
        [\s\S]{0,300}?
        судом\s+кассационной\s+инстанции\s+не\s+установлено\b |

        \bпринимая\s+во\s+внимание\s*,?\s+что\s+фактические\s+обстоятельства
        [\s\S]{0,700}?
        суд\s+кассационной\s+инстанции\s+полагает\b
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


_WEAK_COURT_REASONING_START_RE = re.compile(
    r"""
    (?:
        \bкак\s+(?:видно|следует|усматривается)\s+из\s+материалов\s+дела\b |
        \bкак\s+установлено\s+судами\b |
        \bкак\s+установил(?:и)?\s+суд(?:ы)?\b |
        \bсудами\s+установлено\b |
        \bисследовав\s+и\s+оценив\s+доводы\s+сторон\b |
        \bисследовав\s+и\s+оценив[\s\S]{0,250}?судами\s+установлен[аоы]?\b
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


_COURT_REASONING_START_RE = _STRONG_COURT_REASONING_START_RE

_ABBREVIATION_RE = re.compile(
    r"""
    (?:
        \b(?:т|д|п|ч|ст|абз|рис|см|им|ул|г|гр|руб|коп|тыс|млн|млрд)\. |
        \b[А-ЯA-Z]\. |
        \b[А-ЯA-Z]\.\s*[А-ЯA-Z]\.
    )
    $""",
    flags=re.IGNORECASE | re.VERBOSE,
)


# НОРМАЛИЗАЦИЯ ТЕКСТА

### нормализация текста с сохранением содержательной структуры
def normalize_text_keep_content(text: Any) -> str:
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u202f", " ").replace("\u2007", " ")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    return text.strip("\n")


### нормализация текста для поиска маркеров
def normalize_for_matching(text: Any) -> str:
    text = normalize_text_keep_content(text).lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[ \t]+", " ", text)

    text = re.sub(r"(?<=\s)\d{1,4}(?=\s)", " ", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


### приведение таблицы к текстовому виду
def render_table(table: Any) -> str:
    if table is None:
        return ""

    if isinstance(table, str):
        body = table.strip()
    elif isinstance(table, dict):
        body = json.dumps(table, ensure_ascii=False, sort_keys=True)
    elif isinstance(table, (list, tuple)):
        rows: List[str] = []

        for row in table:
            if isinstance(row, (list, tuple)):
                rows.append(" | ".join("" if cell is None else str(cell) for cell in row))
            else:
                rows.append(str(row))

        body = "\n".join(rows)
    else:
        body = str(table)

    body = normalize_text_keep_content(body)

    if not body:
        return ""

    return f"[[TABLE]]\n{body}\n[[/TABLE]]"


# СБОРКА СТРАНИЦ И СМЕЩЕНИЙ

### преобразование страниц в текстовые блоки
def pages_to_page_texts(pages: Any) -> List[Dict[str, Any]]:
    if isinstance(pages, tuple):
        pages = pages[0]

    result: List[Dict[str, Any]] = []

    for page_index, page in enumerate(pages, start=1):
        page_no = int(page.get("page", page_index))

        parts: List[str] = []

        text = normalize_text_keep_content(page.get("text", "") or "")
        if text:
            parts.append(text)

        for table in page.get("tables") or []:
            table_text = render_table(table)
            if table_text:
                parts.append(table_text)

        page_text = "\n\n".join(parts).strip()

        if page_text:
            result.append(
                {
                    "page": page_no,
                    "text": page_text,
                }
            )

    return result


### сборка полного текста с диапазонами страниц
def pages_to_full_text_with_spans(
    pages: Any,
) -> Tuple[str, List[PageSpan]]:
    page_texts = pages_to_page_texts(pages)

    full_parts: List[str] = []
    spans: List[PageSpan] = []
    current_offset = 0

    for i, page in enumerate(page_texts):
        if i > 0:
            separator = "\n\n"
            full_parts.append(separator)
            current_offset += len(separator)

        page_text = page["text"]
        start_offset = current_offset

        full_parts.append(page_text)
        current_offset += len(page_text)

        end_offset = current_offset

        spans.append(
            PageSpan(
                page=int(page["page"]),
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )

    return "".join(full_parts).strip(), spans


### сборка полного текста без диапазонов страниц
def pages_to_full_text(pages: Any) -> str:
    full_text, _ = pages_to_full_text_with_spans(pages)
    return full_text


### определение страниц по диапазону символов
def get_pages_for_span(
    page_spans: Sequence[PageSpan],
    start_offset: int,
    end_offset: int,
) -> Tuple[Optional[int], Optional[int]]:

    touched_pages: List[int] = []

    for span in page_spans:
        if span.end_offset <= start_offset:
            continue

        if span.start_offset >= end_offset:
            continue

        touched_pages.append(span.page)

    if not touched_pages:
        return None, None

    return min(touched_pages), max(touched_pages)


# ЖЕСТКИЕ ГРАНИЦЫ ЗОН

### деление текста по жестким границам
def split_text_by_hard_boundaries(full_text: str) -> Dict[str, Any]:

    text = normalize_text_keep_content(full_text)

    ustanovil_match = _USTANOVIL_MARKER_RE.search(text)

    if ustanovil_match:
        header_start = 0
        header_end = ustanovil_match.start()

        body_start = ustanovil_match.start()
    else:
        header_start = 0
        header_end = len(text)

        body_start = len(text)

    postanovil_match = None

    for match in _POSTANOVIL_MARKER_RE.finditer(text):
        if match.start() > body_start:
            postanovil_match = match
            break

    if postanovil_match:
        body_end = postanovil_match.start()
        operative_start = postanovil_match.start()
        operative_end = len(text)
    else:
        body_end = len(text)
        operative_start = len(text)
        operative_end = len(text)

    return {
        "header": {
            "text": text[header_start:header_end].strip(),
            "start_offset": header_start,
            "end_offset": header_end,
        },
        "body": {
            "text": text[body_start:body_end].strip(),
            "start_offset": body_start,
            "end_offset": body_end,
        },
        "operative_part": {
            "text": text[operative_start:operative_end].strip(),
            "start_offset": operative_start,
            "end_offset": operative_end,
        },
    }


# ДЕЛЕНИЕ НА ПРЕДЛОЖЕНИЯ

### деление текста на предложения с диапазонами символов
def split_text_to_sentence_spans(
    text: str,
    *,
    global_start_offset: int = 0,
) -> List[Dict[str, Any]]:
    text = normalize_text_keep_content(text)

    if not text:
        return []

    sentences: List[Dict[str, Any]] = []
    buffer: List[str] = []
    buffer_start: Optional[int] = None

    i = 0

    def flush(end_index_exclusive: int) -> None:
        nonlocal buffer, buffer_start

        raw = "".join(buffer)
        stripped = raw.strip()

        if not stripped:
            buffer.clear()
            buffer_start = None
            return

        leading_spaces = len(raw) - len(raw.lstrip())
        trailing_spaces = len(raw) - len(raw.rstrip())

        local_start = (buffer_start or 0) + leading_spaces
        local_end = end_index_exclusive - trailing_spaces

        sentences.append(
            {
                "text": stripped,
                "start_offset": global_start_offset + local_start,
                "end_offset": global_start_offset + local_end,
            }
        )

        buffer.clear()
        buffer_start = None

    while i < len(text):
        char = text[i]

        if buffer_start is None:
            buffer_start = i

        buffer.append(char)

        if char in ".!?":
            current = "".join(buffer).strip()
            next_char = text[i + 1] if i + 1 < len(text) else ""
            prev_tail = current[-20:]

            if _ABBREVIATION_RE.search(prev_tail):
                i += 1
                continue

            if next_char and not next_char.isspace():
                i += 1
                continue

            flush(i + 1)

        elif char == "\n":
            current = "".join(buffer).strip()

            if not current:
                buffer.clear()
                buffer_start = None
                i += 1
                continue

            if current.endswith((".", "!", "?", ":")) and not _ABBREVIATION_RE.search(current[-20:]):
                flush(i + 1)

        i += 1

    if buffer:
        flush(len(text))

    return sentences


### преобразование текста в объекты предложений
def text_to_sentences(
    text: str,
    *,
    start_idx: int = 0,
    page_spans: Optional[Sequence[PageSpan]] = None,
    global_start_offset: int = 0,
) -> List[Sentence]:
    result: List[Sentence] = []

    sentence_spans = split_text_to_sentence_spans(
        text,
        global_start_offset=global_start_offset,
    )

    for offset, sentence_data in enumerate(sentence_spans):
        sentence_start = int(sentence_data["start_offset"])
        sentence_end = int(sentence_data["end_offset"])

        if page_spans:
            page_start, _ = get_pages_for_span(page_spans, sentence_start, sentence_end)
            page = page_start or 1
        else:
            page = 1

        sentence_text = sentence_data["text"]

        result.append(
            Sentence(
                idx=start_idx + offset,
                page=page,
                text=sentence_text,
                norm_text=normalize_for_matching(sentence_text),
                start_offset=sentence_start,
                end_offset=sentence_end,
            )
        )

    return result


# МЯГКОЕ ОПРЕДЕЛЕНИЕ ЗОН

### создание перехода между зонами
def _make_transition(
    *,
    sentence_idx: int,
    zone_type: ZoneType,
    confidence: str,
    reason: str,
) -> Dict[str, Any]:
    return {
        "sentence_idx": sentence_idx,
        "zone_type": zone_type,
        "confidence": confidence,
        "reason": reason,
    }


### поиск переходов между зонами основной части
def detect_body_zone_transitions(sentences: Sequence[Sentence]) -> List[Dict[str, Any]]:

    if not sentences:
        return []

    current_zone = ZoneType.PROCEDURAL_HISTORY
    party_position_start_idx: Optional[int] = None

    transitions: List[Dict[str, Any]] = [
        _make_transition(
            sentence_idx=sentences[0].idx,
            zone_type=ZoneType.PROCEDURAL_HISTORY,
            confidence="high",
            reason="body_starts_from_ustanovil_marker",
        )
    ]

    min_sentences_after_party_for_weak_reasoning = 2

    for sentence in sentences:
        text = sentence.norm_text

        strong_reasoning = _STRONG_COURT_REASONING_START_RE.search(text) is not None
        weak_reasoning = _WEAK_COURT_REASONING_START_RE.search(text) is not None
        party_position = _PARTY_POSITION_START_RE.search(text) is not None

        if current_zone == ZoneType.PROCEDURAL_HISTORY:
            if party_position:
                current_zone = ZoneType.PARTY_POSITION
                party_position_start_idx = sentence.idx
                transitions.append(
                    _make_transition(
                        sentence_idx=sentence.idx,
                        zone_type=ZoneType.PARTY_POSITION,
                        confidence="high",
                        reason="found_party_position_marker",
                    )
                )
                continue

            if strong_reasoning:
                current_zone = ZoneType.COURT_REASONING
                transitions.append(
                    _make_transition(
                        sentence_idx=sentence.idx,
                        zone_type=ZoneType.COURT_REASONING,
                        confidence="high",
                        reason="found_strong_court_reasoning_marker_before_party_position",
                    )
                )
                continue

            continue

        if current_zone == ZoneType.PARTY_POSITION:
            if strong_reasoning:
                current_zone = ZoneType.COURT_REASONING
                transitions.append(
                    _make_transition(
                        sentence_idx=sentence.idx,
                        zone_type=ZoneType.COURT_REASONING,
                        confidence="high",
                        reason="found_strong_court_reasoning_marker_after_party_position",
                    )
                )
                continue

            if weak_reasoning:
                sentences_after_party = (
                    sentence.idx - party_position_start_idx
                    if party_position_start_idx is not None
                    else 0
                )

                if sentences_after_party >= min_sentences_after_party_for_weak_reasoning:
                    current_zone = ZoneType.COURT_REASONING
                    transitions.append(
                        _make_transition(
                            sentence_idx=sentence.idx,
                            zone_type=ZoneType.COURT_REASONING,
                            confidence="medium",
                            reason="found_weak_court_reasoning_marker_after_party_position_buffer",
                        )
                    )
                    continue

    return _deduplicate_transitions(transitions)

### удаление повторных переходов между зонами
def _deduplicate_transitions(transitions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not transitions:
        return []

    result: List[Dict[str, Any]] = []

    for transition in sorted(transitions, key=lambda item: int(item["sentence_idx"])):
        if result and transition["sentence_idx"] == result[-1]["sentence_idx"]:
            result[-1] = transition
            continue

        if result and transition["zone_type"] == result[-1]["zone_type"]:
            continue

        result.append(transition)

    return result


# СБОРКА ЗОН

### создание словаря зоны
def _make_zone(
    *,
    zone_id: str,
    zone_type: ZoneType,
    text: str,
    confidence: str,
    boundary_reason: str,
    page_start: Optional[int],
    page_end: Optional[int],
    start_offset: Optional[int] = None,
    end_offset: Optional[int] = None,
    debug: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "zone_id": zone_id,
        "zone_type": zone_type.value,
        "page_start": page_start,
        "page_end": page_end,
        "text": text.strip(),
        "confidence": confidence,
        "boundary_reason": boundary_reason,
        "debug": debug or {},
        "start_offset": start_offset,
        "end_offset": end_offset,
    }


### сборка зон основной части из предложений
def assemble_body_zones_from_sentences(
    sentences: Sequence[Sentence],
    transitions: Sequence[Dict[str, Any]],
    *,
    start_zone_number: int,
) -> List[Dict[str, Any]]:
    if not sentences:
        return []

    if not transitions:
        transitions = [
            {
                "sentence_idx": sentences[0].idx,
                "zone_type": ZoneType.PROCEDURAL_HISTORY,
                "confidence": "low",
                "reason": "body_without_soft_markers",
            }
        ]

    transitions = sorted(transitions, key=lambda item: int(item["sentence_idx"]))

    zones: List[Dict[str, Any]] = []
    sentence_by_idx = {sentence.idx: pos for pos, sentence in enumerate(sentences)}

    for i, transition in enumerate(transitions):
        start_sentence_idx = int(transition["sentence_idx"])
        start_pos = sentence_by_idx.get(start_sentence_idx)

        if start_pos is None:
            continue

        if i + 1 < len(transitions):
            next_sentence_idx = int(transitions[i + 1]["sentence_idx"])
            end_pos = sentence_by_idx.get(next_sentence_idx, len(sentences))
        else:
            end_pos = len(sentences)

        if start_pos >= end_pos:
            continue

        zone_sentences = list(sentences[start_pos:end_pos])
        text = "\n\n".join(sentence.text for sentence in zone_sentences).strip()

        raw_zone_type = transition["zone_type"]
        zone_type = raw_zone_type if isinstance(raw_zone_type, ZoneType) else ZoneType(str(raw_zone_type))

        start_offset = zone_sentences[0].start_offset
        end_offset = zone_sentences[-1].end_offset

        zones.append(
            _make_zone(
                zone_id=f"z{start_zone_number + len(zones):03d}",
                zone_type=zone_type,
                page_start=min(sentence.page for sentence in zone_sentences),
                page_end=max(sentence.page for sentence in zone_sentences),
                text=text,
                confidence=transition.get("confidence", "unknown"),
                boundary_reason=transition.get("reason", ""),
                start_offset=start_offset,
                end_offset=end_offset,
                debug={
                    "sentence_start_idx": zone_sentences[0].idx,
                    "sentence_end_idx": zone_sentences[-1].idx,
                    "num_sentences": len(zone_sentences),
                    "start_preview": zone_sentences[0].text[:240],
                    "end_preview": zone_sentences[-1].text[-240:],
                },
            )
        )

    return zones


### деление страниц на зоны судебного акта
def split_pages_into_zones(pages: Any) -> List[Dict[str, Any]]:
    full_text, page_spans = pages_to_full_text_with_spans(pages)
    parts = split_text_by_hard_boundaries(full_text)

    zones: List[Dict[str, Any]] = []

    header = parts["header"]
    if header["text"]:
        page_start, page_end = get_pages_for_span(
            page_spans,
            header["start_offset"],
            header["end_offset"],
        )

        zones.append(
            _make_zone(
                zone_id=f"z{len(zones) + 1:03d}",
                zone_type=ZoneType.HEADER,
                text=header["text"],
                confidence="high",
                boundary_reason="text_before_ustanovil_marker",
                page_start=page_start,
                page_end=page_end,
                start_offset=header["start_offset"],
                end_offset=header["end_offset"],
            )
        )

    body = parts["body"]
    if body["text"]:
        body_sentences = text_to_sentences(
            body["text"],
            start_idx=0,
            page_spans=page_spans,
            global_start_offset=body["start_offset"],
        )

        body_transitions = detect_body_zone_transitions(body_sentences)

        body_zones = assemble_body_zones_from_sentences(
            body_sentences,
            body_transitions,
            start_zone_number=len(zones) + 1,
        )

        zones.extend(body_zones)

    operative = parts["operative_part"]
    if operative["text"]:
        page_start, page_end = get_pages_for_span(
            page_spans,
            operative["start_offset"],
            operative["end_offset"],
        )

        zones.append(
            _make_zone(
                zone_id=f"z{len(zones) + 1:03d}",
                zone_type=ZoneType.OPERATIVE_PART,
                text=operative["text"],
                confidence="high",
                boundary_reason="text_from_postanovil_marker",
                page_start=page_start,
                page_end=page_end,
                start_offset=operative["start_offset"],
                end_offset=operative["end_offset"],
            )
        )

    return zones


### совместимость с прежним названием деления на секции
def split_pages_into_sections(pages: Any) -> List[Dict[str, Any]]:
    return split_pages_into_zones(pages)


### преобразование зон в формат страниц для чанкинга
def zones_to_chunker_pages(zones: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []

    for zone in zones:
        result.append(
            {
                "page": int(zone.get("page_start") or 1),
                "text": zone.get("text", ""),
                "tables": [],
                "section_type": zone.get("zone_type"),
                "page_start": zone.get("page_start"),
                "page_end": zone.get("page_end"),
            }
        )

    return result


# ПРОВЕРКА СОХРАННОСТИ ТЕКСТА

### нормализация текста для проверки сохранности
def _normalize_for_integrity_check(text: str) -> str:
    if not text:
        return ""

    text = str(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


### сборка текста из зон
def _build_text_from_zones(zones: Sequence[Dict[str, Any]]) -> str:
    return "\n\n".join(zone.get("text", "") for zone in zones).strip()


### проверка сохранности текста после деления на зоны
def check_zone_integrity(
    pages: Any,
    zones: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    source_text = pages_to_full_text(pages)
    zones_text = _build_text_from_zones(zones)

    source_norm = _normalize_for_integrity_check(source_text)
    zones_norm = _normalize_for_integrity_check(zones_text)

    source_len = len(source_norm)
    zones_len = len(zones_norm)

    length_ratio = 1.0 if source_len == 0 and zones_len == 0 else zones_len / max(1, source_len)

    return {
        "zones_count": len(zones),
        "source_norm_chars": source_len,
        "zones_norm_chars": zones_len,
        "length_ratio": round(length_ratio, 6),
        "text_equal_after_normalization": source_norm == zones_norm,
        "ok": source_norm == zones_norm,
    }


# РУЧНОЙ ЗАПУСК

### ручная проверка работы модуля
def main() -> None:
    from pdfParser import extract_pdf_pages

    pdf_path = "data/cassation_docs/кассация/А03-20799-2023__20250123.pdf"

    parsed = extract_pdf_pages(pdf_path)
    zones = split_pages_into_zones(parsed)
    integrity = check_zone_integrity(parsed, zones)

    if isinstance(parsed, tuple):
        pages = parsed[0]
    else:
        pages = parsed

    print(f"PDF: {pdf_path}")
    print(f"Страниц извлечено: {len(pages)}")
    print(f"Секций найдено: {len(zones)}")

    print("\n--- Проверка сохранности текста ---")
    print(f"Секций: {integrity['zones_count']}")
    print(f"Символов в исходном тексте: {integrity['source_norm_chars']}")
    print(f"Символов в секциях: {integrity['zones_norm_chars']}")
    print(f"Соотношение длины: {integrity['length_ratio']}")
    print(f"Текст совпадает после нормализации: {integrity['text_equal_after_normalization']}")
    print(f"Итог проверки: {'OK' if integrity['ok'] else 'ПРОБЛЕМА'}")

    print("\n--- Найденные секции ---")

    for zone in zones:
        text = zone.get("text", "")

        print("\n" + "-" * 120)
        print(
            f"{zone['zone_id']} | {zone['zone_type']} | "
            f"pages {zone['page_start']}-{zone['page_end']} | "
            f"chars={len(text)} | "
            f"confidence={zone['confidence']}"
        )
        print(f"boundary_reason: {zone['boundary_reason']}")
        print(f"offsets={zone.get('start_offset')}-{zone.get('end_offset')}")
        print("\n--- TEXT ---")
        print(text)


if __name__ == "__main__":
    main()
