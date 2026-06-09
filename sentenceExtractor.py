import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


# КОНСТАНТЫ

_NUMERIC_DATE_RE = re.compile(
    r"""
    (?<!\d)
    (?P<day>0?[1-9]|[12]\d|3[01])
    [./-]
    (?P<month>0?[1-9]|1[0-2])
    [./-]
    (?P<year>\d{2}|\d{4})
    (?!\d)
    """,
    flags=re.VERBOSE,
)

_MONTH_NAMES = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
}

_TEXT_DATE_RE = re.compile(
    rf"""
    (?<!\d)
    (?P<day>0?[1-9]|[12]\d|3[01])
    \s+
    (?P<month>{'|'.join(_MONTH_NAMES.keys())})
    \s+
    (?P<year>\d{{4}})
    (?:\s*г(?:ода|\.)?)?
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

_YEAR_ONLY_RE = re.compile(
    r"""
    \b
    (?P<year>19\d{2}|20\d{2})
    \s*
    (?:года|г\.)
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

_ABBREVIATION_RE = re.compile(
    r"""
    (?:
        \b(?:т|д|п|ч|ст|абз|рис|см|им|ул|г|гр|руб|коп|тыс|млн|млрд|№)\. |
        \b[А-ЯA-Z]\. |
        \b[А-ЯA-Z]\.\s*[А-ЯA-Z]\.
    )
    $""",
    flags=re.IGNORECASE | re.VERBOSE,
)


# МОДЕЛИ ДАННЫХ

@dataclass(frozen=True)
class SentenceRecord:
    sentence_id: str
    global_index: int
    zone_sentence_index: int
    zone_id: str
    zone_type: str
    page_start: Any
    page_end: Any
    zone_start_offset: Any
    zone_end_offset: Any
    sentence_start_offset: Any
    sentence_end_offset: Any
    text: str
    norm_text: str
    dates: list[str]
    weak_dates: list[str]
    has_date: bool


# НОРМАЛИЗАЦИЯ ТЕКСТА

### сохранение содержимого текста при базовой нормализации
def normalize_text_keep_content(
    text: Any,  # исходное значение
) -> str:
    # обработка пустого значения
    if text is None:
        return ""

    # приведение переносов и невидимых символов
    text = str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u202f", " ").replace("\u2007", " ")
    text = text.replace("\u200b", "").replace("\ufeff", "")

    # удаление хвостовых пробелов в строках
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    return text.strip("\n")


### подготовка текста для поиска и сопоставления
def normalize_for_matching(
    text: Any,  # исходное значение
) -> str:
    # базовая нормализация регистра и буквы е
    text = normalize_text_keep_content(text).lower().replace("ё", "е")

    # уплотнение пробелов и лишних переносов
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


### нормализация пробелов без изменения смысла
def normalize_spaces(
    text: Any,  # исходное значение
) -> str:
    # базовая нормализация содержимого
    text = normalize_text_keep_content(text)

    # замена любых пробельных последовательностей одним пробелом
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ПОИСК ДАТ

### поиск дат с днем и месяцем
def find_strong_dates(
    text: str,  # текст предложения
) -> list[str]:
    # поиск цифровых дат
    result: list[str] = []

    for match in _NUMERIC_DATE_RE.finditer(text):
        result.append(match.group(0))

    # поиск текстовых дат
    for match in _TEXT_DATE_RE.finditer(text):
        result.append(match.group(0))

    return result


### поиск слабых дат в виде года
def find_weak_dates(
    text: str,  # текст предложения
) -> list[str]:
    # исключение предложений с полноценной датой
    if find_strong_dates(text):
        return []

    # выбор годов без дня и месяца
    return [match.group(0) for match in _YEAR_ONLY_RE.finditer(text)]


### нормализация даты для сортировки
def normalize_date_for_sort(
    raw_date: str,  # исходная дата
) -> str:
    # подготовка даты к сопоставлению
    raw = normalize_spaces(raw_date).lower().replace("ё", "е")

    # обработка цифрового формата
    numeric = _NUMERIC_DATE_RE.search(raw)
    if numeric:
        day = int(numeric.group("day"))
        month = int(numeric.group("month"))
        year = numeric.group("year")

        if len(year) == 2:
            year_int = int(year)
            year = f"20{year}" if year_int < 50 else f"19{year}"

        return f"{int(year):04d}-{month:02d}-{day:02d}"

    # обработка текстового формата
    text_date = _TEXT_DATE_RE.search(raw)
    if text_date:
        day = int(text_date.group("day"))
        month_name = text_date.group("month").lower().replace("ё", "е")
        month = _MONTH_NAMES[month_name]
        year = int(text_date.group("year"))

        return f"{year:04d}-{month}-{day:02d}"

    # обработка года без дня и месяца
    year_only = _YEAR_ONLY_RE.search(raw)
    if year_only:
        return f"{int(year_only.group('year')):04d}-00-00"

    return "9999-99-99"


# РАЗБИЕНИЕ НА ПРЕДЛОЖЕНИЯ

### добавление накопленного предложения в результат
def _flush_sentence_buffer(
    sentences: list[dict[str, Any]],  # список найденных предложений
    buffer: list[str],  # накопленный текст предложения
    buffer_start: Any,  # старт накопленного текста
    end_index_exclusive: int,  # позиция конца предложения
    global_start_offset: int,  # смещение зоны в полном документе
) -> Any:
    # подготовка накопленного текста
    raw = "".join(buffer)
    stripped = raw.strip()

    if not stripped:
        buffer.clear()
        return None

    # расчет локальных границ предложения
    leading_spaces = len(raw) - len(raw.lstrip())
    trailing_spaces = len(raw) - len(raw.rstrip())
    local_start = (buffer_start or 0) + leading_spaces
    local_end = end_index_exclusive - trailing_spaces

    # сохранение предложения со смещениями
    sentences.append(
        {
            "text": stripped,
            "start_offset": global_start_offset + local_start,
            "end_offset": global_start_offset + local_end,
        }
    )

    buffer.clear()
    return None


### разбиение текста на предложения со смещениями
def split_text_to_sentence_spans(
    text: str,  # исходный текст
    *,
    global_start_offset: int = 0,  # смещение текста в полном документе
) -> list[dict[str, Any]]:
    # базовая нормализация текста
    text = normalize_text_keep_content(text)

    if not text:
        return []

    # подготовка накопителей
    sentences: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_start = None
    i = 0

    while i < len(text):
        char = text[i]

        if buffer_start is None:
            buffer_start = i

        buffer.append(char)

        # проверка конца предложения по точке и похожим знакам
        if char in ".!?":
            current = "".join(buffer).strip()
            next_char = text[i + 1] if i + 1 < len(text) else ""
            prev_tail = current[-30:]

            if _ABBREVIATION_RE.search(prev_tail):
                i += 1
                continue

            if next_char and not next_char.isspace():
                i += 1
                continue

            buffer_start = _flush_sentence_buffer(
                sentences,
                buffer,
                buffer_start,
                i + 1,
                global_start_offset,
            )

        # проверка конца предложения по переносу строки
        elif char == "\n":
            current = "".join(buffer).strip()

            if not current:
                buffer.clear()
                buffer_start = None
                i += 1
                continue

            if current.endswith((".", "!", "?", ":")) and not _ABBREVIATION_RE.search(current[-30:]):
                buffer_start = _flush_sentence_buffer(
                    sentences,
                    buffer,
                    buffer_start,
                    i + 1,
                    global_start_offset,
                )

        i += 1

    # сохранение хвоста текста
    if buffer:
        _flush_sentence_buffer(
            sentences,
            buffer,
            buffer_start,
            len(text),
            global_start_offset,
        )

    return sentences


# ОСНОВНОЙ API

### извлечение предложений из зон документа
def extract_sentences_from_zones(
    zones: Sequence[dict[str, Any]],  # зоны из structureSplitter
    *,
    include_operative_part: bool = False,  # признак включения резолютивной части
) -> list[dict[str, Any]]:
    # подготовка результата и глобального счетчика
    records: list[SentenceRecord] = []
    global_index = 0

    for zone in zones:
        zone_type = str(zone.get("zone_type") or "unknown")

        # пропуск резолютивной части по умолчанию
        if zone_type == "operative_part" and not include_operative_part:
            continue

        zone_id = str(zone.get("zone_id") or f"z_unknown_{len(records)}")
        zone_text = zone.get("text", "") or ""
        zone_start_offset = zone.get("start_offset")
        zone_end_offset = zone.get("end_offset")

        # определение стартового смещения зоны
        try:
            zone_global_start = int(zone_start_offset) if zone_start_offset is not None else 0
        except (TypeError, ValueError):
            zone_global_start = 0

        sentence_spans = split_text_to_sentence_spans(
            zone_text,
            global_start_offset=zone_global_start,
        )

        # формирование записей предложений
        for local_index, sentence_data in enumerate(sentence_spans, start=1):
            text = normalize_spaces(sentence_data["text"])

            if not text:
                continue

            dates = find_strong_dates(text)
            weak_dates = find_weak_dates(text)
            global_index += 1

            record = SentenceRecord(
                sentence_id=f"s{global_index:05d}",
                global_index=global_index,
                zone_sentence_index=local_index,
                zone_id=zone_id,
                zone_type=zone_type,
                page_start=zone.get("page_start"),
                page_end=zone.get("page_end"),
                zone_start_offset=zone_start_offset,
                zone_end_offset=zone_end_offset,
                sentence_start_offset=sentence_data.get("start_offset"),
                sentence_end_offset=sentence_data.get("end_offset"),
                text=text,
                norm_text=normalize_for_matching(text),
                dates=dates,
                weak_dates=weak_dates,
                has_date=bool(dates or weak_dates),
            )
            records.append(record)

    return [asdict(record) for record in records]


### фильтрация предложений с датами
def filter_sentences_with_dates(
    sentences: Sequence[dict[str, Any]],  # список предложений
    *,
    include_weak_dates: bool = False,  # признак включения слабых дат
    allowed_zone_types: Any = None,  # допустимые типы зон
) -> list[dict[str, Any]]:
    # подготовка результата
    result: list[dict[str, Any]] = []

    for sentence in sentences:
        zone_type = str(sentence.get("zone_type") or "unknown")

        # проверка допустимой зоны
        if allowed_zone_types is not None and zone_type not in allowed_zone_types:
            continue

        strong_dates = sentence.get("dates") or []
        weak_dates = sentence.get("weak_dates") or []

        # выбор предложений с подходящими датами
        if strong_dates or (include_weak_dates and weak_dates):
            result.append(dict(sentence))

    return result


### группировка предложений по зонам
def group_sentences_by_zone(
    sentences: Sequence[dict[str, Any]],  # список предложений
) -> dict[str, list[dict[str, Any]]]:
    # подготовка словаря групп
    grouped: dict[str, list[dict[str, Any]]] = {}

    for sentence in sentences:
        # добавление предложения в группу зоны
        zone_id = str(sentence.get("zone_id") or "unknown")
        grouped.setdefault(zone_id, []).append(dict(sentence))

    return grouped


### сохранение предложений в json
def save_sentences_json(
    sentences: Sequence[dict[str, Any]],  # список предложений
    output_path: Any,  # путь для сохранения
) -> None:
    # подготовка пути и директории
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # запись json файла
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(list(sentences), file, ensure_ascii=False, indent=2)


### загрузка предложений из json
def load_sentences_json(
    input_path: Any,  # путь к json файлу
) -> list[dict[str, Any]]:
    # чтение json файла
    input_path = Path(input_path)

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    # проверка формата данных
    if not isinstance(data, list):
        raise ValueError("Файл с предложениями должен содержать JSON-массив.")

    return data


# ОТЛАДКА И РУЧНОЙ ЗАПУСК

### вывод статистики по предложениям
def print_sentence_stats(
    sentences: Sequence[dict[str, Any]],  # список предложений
) -> None:
    # подсчет общих показателей
    total = len(sentences)
    with_dates = sum(1 for item in sentences if item.get("dates"))
    with_weak_dates = sum(1 for item in sentences if item.get("weak_dates"))

    by_zone: dict[str, int] = {}
    by_zone_dates: dict[str, int] = {}

    # подсчет показателей по зонам
    for item in sentences:
        zone_type = str(item.get("zone_type") or "unknown")
        by_zone[zone_type] = by_zone.get(zone_type, 0) + 1

        if item.get("dates") or item.get("weak_dates"):
            by_zone_dates[zone_type] = by_zone_dates.get(zone_type, 0) + 1

    # вывод статистики
    print("\n" + "=" * 120)
    print("SENTENCE EXTRACTOR STATS")
    print("=" * 120)
    print(f"Всего предложений: {total}")
    print(f"С полноценными датами: {with_dates}")
    print(f"Со слабыми датами-годами: {with_weak_dates}")

    print("\nПо зонам:")
    for zone_type, count in sorted(by_zone.items()):
        print(f"- {zone_type}: {count} предложений, с датами: {by_zone_dates.get(zone_type, 0)}")


### вывод предложений с датами
def print_date_sentences(
    sentences: Sequence[dict[str, Any]],  # список предложений
    *,
    limit: int = 100,  # ограничение количества строк вывода
) -> None:
    # выбор предложений с датами
    date_sentences = filter_sentences_with_dates(sentences, include_weak_dates=True)

    # вывод заголовка
    print("\n" + "=" * 120)
    print("SENTENCES WITH DATES")
    print("=" * 120)

    # вывод найденных предложений
    for item in date_sentences[:limit]:
        print("\n" + "-" * 120)
        print(
            f"{item.get('sentence_id')} | "
            f"zone={item.get('zone_type')}:{item.get('zone_id')} | "
            f"zone_sentence_index={item.get('zone_sentence_index')} | "
            f"pages={item.get('page_start')}-{item.get('page_end')} | "
            f"dates={item.get('dates')} | weak_dates={item.get('weak_dates')}"
        )
        print(item.get("text"))

    # вывод информации об усечении
    if len(date_sentences) > limit:
        print(f"\n... показано {limit} из {len(date_sentences)} предложений с датами")


### сквозное извлечение предложений из pdf
def extract_sentences_from_pdf(
    pdf_path: Any,  # путь к pdf файлу
    *,
    include_operative_part: bool = False,  # признак включения резолютивной части
) -> list[dict[str, Any]]:
    # импорт этапов пайплайна
    from pdfParser import extract_pdf_pages
    from structureSplitter import split_pages_into_zones

    # извлечение страниц и зон
    parsed = extract_pdf_pages(str(pdf_path))
    zones = split_pages_into_zones(parsed)

    return extract_sentences_from_zones(
        zones,
        include_operative_part=include_operative_part,
    )


### ручной запуск проверки файла
def main() -> None:
    # подготовка путей
    pdf_path = Path("test.pdf")
    output_path = Path("debug_sentences.json")

    # проверка наличия тестового pdf
    if not pdf_path.exists():
        print(f"Не найден файл {pdf_path.resolve()}")
        print("Положи рядом test.pdf или импортируй extract_sentences_from_zones() в app.py.")
        return

    # извлечение и сохранение предложений
    sentences = extract_sentences_from_pdf(pdf_path)
    save_sentences_json(sentences, output_path)

    print(f"PDF: {pdf_path}")
    print(f"JSON сохранен: {output_path.resolve()}")
    print_sentence_stats(sentences)
    print_date_sentences(sentences, limit=120)


if __name__ == "__main__":
    main()
