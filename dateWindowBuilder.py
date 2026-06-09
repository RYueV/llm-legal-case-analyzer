import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from referencesScanner import build_reference_blacklist


# КОНСТАНТЫ

ALLOWED_ZONE_TYPES = {
    "court_reasoning",
}


# МОДЕЛИ ДАННЫХ

@dataclass(frozen=True)
class DateWindow:
    window_id: str
    kind: str
    zone_id: str
    zone_type: str
    page_start: Any
    page_end: Any
    sentence_start_global_index: int
    sentence_end_global_index: int
    sentence_start_zone_index: int
    sentence_end_zone_index: int
    sentence_ids: list[str]
    date_sentence_ids: list[str]
    dates: list[str]
    weak_dates: list[str]
    has_strong_date: bool
    has_weak_date: bool
    text: str
    sentences: list[dict[str, Any]]


# СЛУЖЕБНЫЕ ФУНКЦИИ

### приведение значения к целому числу
def _as_int(
    value: Any, # исходное значение
    default: int = 0, # значение по умолчанию
) -> int:
    # обработка пустого значения
    try:
        if value is None:
            return default

        return int(value)
    except (TypeError, ValueError):
        return default


### нормализация типа зоны
def _normalize_zone_type(
    value: Any, # исходное значение типа зоны
) -> str:
    # приведение к строковому виду
    return str(value or "unknown").strip()


### построение ключа сортировки предложения
def _sentence_sort_key(
    sentence: dict[str, Any], # данные предложения
) -> tuple[int, int]:
    # сортировка по глобальному и локальному индексу
    return (
        _as_int(sentence.get("global_index"), 10**9),
        _as_int(sentence.get("zone_sentence_index"), 10**9),
    )


### получение текста предложения
def _sentence_text(
    sentence: dict[str, Any], # данные предложения
) -> str:
    # безопасное чтение текстового поля
    return str(sentence.get("text") or "").strip()


### удаление дублей с сохранением порядка
def _unique_keep_order(
    values: Sequence[Any], # исходные значения
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    # последовательный обход значений
    for value in values:
        if value is None:
            continue

        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


### проверка наличия сильной даты
def _has_strong_date(
    sentence: dict[str, Any], # данные предложения
) -> bool:
    # проверка поля dates
    return bool(sentence.get("dates"))


### проверка наличия слабой даты
def _has_weak_date(
    sentence: dict[str, Any], # данные предложения
) -> bool:
    # проверка поля weak_dates
    return bool(sentence.get("weak_dates"))


### проверка наличия только слабой даты
def _has_weak_date_only(
    sentence: dict[str, Any], # данные предложения
) -> bool:
    # исключение дубля при наличии сильной даты
    return _has_weak_date(sentence) and not _has_strong_date(sentence)


# ФИЛЬТРАЦИЯ ПРЕДЛОЖЕНИЙ

### отбор предложений для построения окон
def filter_sentences_for_date_windows(
    sentences: Sequence[dict[str, Any]], # предложения после sentenceExtractor
    *,
    blacklisted_sentence_ids=None, # идентификаторы предложений из blacklist
) -> list[dict[str, Any]]:
    blacklist = blacklisted_sentence_ids or set()
    result: list[dict[str, Any]] = []

    # отбор по blacklist и допустимым зонам
    for sentence in sentences:
        sentence_id = str(sentence.get("sentence_id"))
        zone_type = _normalize_zone_type(sentence.get("zone_type"))

        if sentence_id in blacklist:
            continue

        if zone_type not in ALLOWED_ZONE_TYPES:
            continue

        result.append(dict(sentence))

    return sorted(result, key=_sentence_sort_key)


### группировка предложений по зонам
def group_sentences_by_zone(
    sentences: Sequence[dict[str, Any]], # отфильтрованные предложения
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}

    # накопление предложений внутри каждой зоны
    for sentence in sorted(sentences, key=_sentence_sort_key):
        zone_id = str(sentence.get("zone_id") or "unknown")
        grouped.setdefault(zone_id, []).append(dict(sentence))

    return grouped


# ПОСТРОЕНИЕ ГРУПП ДАТ

### построение групп предложений с сильными датами
def _build_date_groups(
    zone_sentences: Sequence[dict[str, Any]], # предложения одной зоны
) -> list[list[int]]:
    date_positions = [
        position
        for position, sentence in enumerate(zone_sentences)
        if _has_strong_date(sentence)
    ]

    if not date_positions:
        return []

    groups: list[list[int]] = []
    current_group: list[int] = [date_positions[0]]

    # объединение близких предложений с датами
    for position in date_positions[1:]:
        previous_position = current_group[-1]

        if position - previous_position <= 2:
            current_group.append(position)
        else:
            groups.append(current_group)
            current_group = [position]

    groups.append(current_group)

    return groups


### построение позиций предложений со слабыми датами
def _build_weak_date_single_positions(
    zone_sentences: Sequence[dict[str, Any]], # предложения одной зоны
    *,
    strong_date_positions: set[int], # позиции сильных дат
) -> list[int]:
    result: list[int] = []

    # отбор слабых дат без дублей с сильными окнами
    for position, sentence in enumerate(zone_sentences):
        if position in strong_date_positions:
            continue

        if _has_weak_date_only(sentence):
            result.append(position)

    return result


### построение диапазона для группы дат
def _make_span_for_date_group(
    *,
    date_group: list[int], # позиции предложений с датами
    total_sentences: int, # общее количество предложений в зоне
    context_before: int = 1, # количество предложений до группы
    context_after: int = 1, # количество предложений после группы
) -> dict[str, Any]:
    first_date_pos = min(date_group)
    last_date_pos = max(date_group)

    # расширение диапазона на контекст
    return {
        "start_pos": max(0, first_date_pos - context_before),
        "end_pos": min(total_sentences - 1, last_date_pos + context_after),
        "date_positions": date_group,
    }


# ПОСТРОЕНИЕ ОКОН

### создание записи окна
def _make_window_record(
    *,
    window_number: int, # номер окна
    zone_id: str, # идентификатор зоны
    zone_sentences: Sequence[dict[str, Any]], # предложения одной зоны
    start_pos: int, # начальная позиция окна
    end_pos: int, # конечная позиция окна
    date_positions: Sequence[int], # позиции предложений с датами
) -> DateWindow:
    selected_sentences = [dict(item) for item in zone_sentences[start_pos:end_pos + 1]]
    date_sentences = [
        dict(zone_sentences[pos])
        for pos in date_positions
        if start_pos <= pos <= end_pos
    ]

    first = selected_sentences[0]
    last = selected_sentences[-1]

    # определение предложений с датами
    date_sentence_id_set = {
        str(sentence.get("sentence_id"))
        for sentence in date_sentences
    }

    dates = _unique_keep_order(
        date
        for sentence in date_sentences
        for date in (sentence.get("dates") or [])
    )

    weak_dates = _unique_keep_order(
        date
        for sentence in date_sentences
        for date in (sentence.get("weak_dates") or [])
    )

    # расчет диапазона страниц
    page_values: list[int] = []
    for sentence in selected_sentences:
        if sentence.get("page_start") is not None:
            page_values.append(_as_int(sentence.get("page_start")))
        if sentence.get("page_end") is not None:
            page_values.append(_as_int(sentence.get("page_end")))

    # сборка текста окна с маркерами
    text_lines: list[str] = []
    for sentence in selected_sentences:
        sentence_id = str(sentence.get("sentence_id"))
        marker = "*" if sentence_id in date_sentence_id_set else "-"
        text_lines.append(f"{marker} {_sentence_text(sentence)}")

    return DateWindow(
        window_id=f"w{window_number:05d}",
        kind="date_window",
        zone_id=zone_id,
        zone_type=_normalize_zone_type(first.get("zone_type")),
        page_start=min(page_values) if page_values else first.get("page_start"),
        page_end=max(page_values) if page_values else last.get("page_end"),
        sentence_start_global_index=_as_int(first.get("global_index")),
        sentence_end_global_index=_as_int(last.get("global_index")),
        sentence_start_zone_index=_as_int(first.get("zone_sentence_index")),
        sentence_end_zone_index=_as_int(last.get("zone_sentence_index")),
        sentence_ids=[str(sentence.get("sentence_id")) for sentence in selected_sentences],
        date_sentence_ids=[str(sentence.get("sentence_id")) for sentence in date_sentences],
        dates=dates,
        weak_dates=weak_dates,
        has_strong_date=bool(dates),
        has_weak_date=bool(weak_dates),
        text="\n".join(text_lines).strip(),
        sentences=selected_sentences,
    )


### построение окон по датам
def build_date_windows(
    sentences: Sequence[dict[str, Any]], # предложения после sentenceExtractor
    *,
    blacklisted_sentence_ids=None, # идентификаторы предложений из blacklist
) -> list[dict[str, Any]]:
    filtered = filter_sentences_for_date_windows(
        sentences,
        blacklisted_sentence_ids=blacklisted_sentence_ids,
    )

    grouped = group_sentences_by_zone(filtered)
    windows: list[DateWindow] = []

    # обработка каждой зоны отдельно
    for zone_id, zone_sentences in grouped.items():
        if not zone_sentences:
            continue

        date_groups = _build_date_groups(zone_sentences)
        strong_date_positions: set[int] = set()

        # построение окон для сильных дат
        for date_group in date_groups:
            strong_date_positions.update(date_group)

            span = _make_span_for_date_group(
                date_group=date_group,
                total_sentences=len(zone_sentences),
                context_before=1,
                context_after=1,
            )

            windows.append(
                _make_window_record(
                    window_number=len(windows) + 1,
                    zone_id=zone_id,
                    zone_sentences=zone_sentences,
                    start_pos=int(span["start_pos"]),
                    end_pos=int(span["end_pos"]),
                    date_positions=span["date_positions"],
                )
            )

        weak_date_positions = _build_weak_date_single_positions(
            zone_sentences,
            strong_date_positions=strong_date_positions,
        )

        # построение отдельных окон для слабых дат
        for weak_position in weak_date_positions:
            windows.append(
                _make_window_record(
                    window_number=len(windows) + 1,
                    zone_id=zone_id,
                    zone_sentences=zone_sentences,
                    start_pos=weak_position,
                    end_pos=weak_position,
                    date_positions=[weak_position],
                )
            )

    windows = sorted(
        windows,
        key=lambda item: (
            item.page_start if item.page_start is not None else 10**9,
            item.sentence_start_global_index,
        ),
    )

    # перенумерация после сортировки
    renumbered: list[DateWindow] = []
    for index, window in enumerate(windows, start=1):
        renumbered.append(
            DateWindow(
                **{
                    **asdict(window),
                    "window_id": f"w{index:05d}",
                }
            )
        )

    return [asdict(window) for window in renumbered]


# JSON И ТЕКСТОВЫЙ ВЫВОД

### сохранение окон в json
def save_date_windows_json(
    windows: Sequence[dict[str, Any]], # окна по датам
    output_path: Any, # путь сохранения
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(list(windows), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


### загрузка окон из json
def load_date_windows_json(
    input_path: Any, # путь к json
) -> list[dict[str, Any]]:
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))

    # проверка формата json
    if not isinstance(data, list):
        raise ValueError("Файл с date windows должен содержать JSON-массив.")

    return data


### сборка компактного текста окон
def build_compact_windows_text(
    windows: Sequence[dict[str, Any]], # окна по датам
) -> str:
    blocks: list[str] = []

    # сборка одного блока на каждое окно
    for window in windows:
        blocks.append(
            "\n".join(
                [
                    (
                        f"[{window.get('window_id')}] "
                        f"kind={window.get('kind', 'date_window')} "
                        f"zone={window.get('zone_type')}:{window.get('zone_id')} "
                        f"pages={window.get('page_start')}-{window.get('page_end')} "
                        f"sentences={window.get('sentence_start_zone_index')}-{window.get('sentence_end_zone_index')} "
                        f"dates={window.get('dates')} "
                        f"weak_dates={window.get('weak_dates')}"
                    ),
                    str(window.get("text") or ""),
                ]
            ).strip()
        )

    return "\n\n".join(blocks).strip()


# ОТЛАДОЧНЫЙ ВЫВОД

### печать статистики окон
def print_date_window_stats(
    windows: Sequence[dict[str, Any]], # окна по датам
    *,
    blacklisted_count: int = 0, # количество предложений из blacklist
) -> None:
    by_zone: dict[str, int] = {}

    # подсчет окон по зонам
    for window in windows:
        zone_type = str(window.get("zone_type") or "unknown")
        by_zone[zone_type] = by_zone.get(zone_type, 0) + 1

    print("\n" + "=" * 120)
    print("СТАТИСТИКА ОКОН ПО ДАТАМ")
    print("=" * 120)
    print(f"Предложений в blacklist referencesScanner: {blacklisted_count}")
    print(f"Всего окон: {len(windows)}")

    print("По зонам:")
    for key, count in sorted(by_zone.items()):
        print(f"- {key}: {count}")


### печать окон по датам
def print_date_windows(
    windows: Sequence[dict[str, Any]], # окна по датам
    *,
    limit: int = 120, # максимальное количество окон
) -> None:
    print("\n" + "=" * 120)
    print("ОКНА ПО ДАТАМ")
    print("=" * 120)

    # вывод ограниченного списка окон
    for window in list(windows)[:limit]:
        print("\n" + "-" * 120)
        print(
            f"{window.get('window_id')} | "
            f"zone={window.get('zone_type')}:{window.get('zone_id')} | "
            f"pages={window.get('page_start')}-{window.get('page_end')} | "
            f"dates={window.get('dates')}"
        )
        print(window.get("text"))

    if len(windows) > limit:
        print(f"\n... показано {limit} из {len(windows)} date windows")


# ЗАПУСК PIPELINE

### определение пути к pdf
def resolve_pdf_path(
    case_number: str, # номер дела и дата в имени файла
) -> Path:
    local_pdf = Path(f"{case_number}.pdf")
    if local_pdf.exists():
        return local_pdf

    project_pdf = Path("data/cassation_docs/кассация") / f"{case_number}.pdf"
    if project_pdf.exists():
        return project_pdf

    raise FileNotFoundError(
        "Не найден PDF.\n"
        f"Проверено:\n"
        f"1) {local_pdf.resolve()}\n"
        f"2) {project_pdf.resolve()}"
    )


### построение окон по pdf
def build_date_windows_from_pdf(
    pdf_path: Any, # путь к pdf
) -> list[dict[str, Any]]:
    from headerExtractor import extract_header_metadata_from_zones
    from pdfParser import extract_pdf_pages
    from sentenceExtractor import extract_sentences_from_zones
    from structureSplitter import split_pages_into_zones

    # последовательное выполнение этапов pipeline
    parsed = extract_pdf_pages(str(pdf_path))
    zones = split_pages_into_zones(parsed)
    sentences = extract_sentences_from_zones(zones, include_operative_part=False)

    header_metadata = extract_header_metadata_from_zones(zones)
    blacklisted_sentence_ids = build_reference_blacklist(sentences)

    windows = build_date_windows(
        sentences,
        blacklisted_sentence_ids=blacklisted_sentence_ids,
    )

    print("\n" + "=" * 120)
    print("ШАПКА АКТА")
    print("=" * 120)
    print(f"Номер дела: {header_metadata.case_number}")
    print(f"Дата акта: {header_metadata.act_date}")
    print(f"Суд: {header_metadata.court_name}")
    print(f"Судьи: {'; '.join(header_metadata.judges) if header_metadata.judges else None}")
    print(f"Должник: {header_metadata.debtor_name}")

    print_date_window_stats(
        windows,
        blacklisted_count=len(blacklisted_sentence_ids),
    )

    return windows


### запуск построения окон для дела
def run_date_window_builder_pipeline_for_case(
    case_number: str, # номер дела и дата в имени файла
) -> list[dict[str, Any]]:
    pdf_path = resolve_pdf_path(case_number)
    windows = build_date_windows_from_pdf(pdf_path)

    # сохранение компактного txt
    output_txt = Path(f"{case_number}_date_windows.txt")
    output_txt.write_text(build_compact_windows_text(windows), encoding="utf-8")

    print(f"\nTXT сохранен: {output_txt.resolve()}")
    print_date_windows(windows, limit=120)

    return windows


### ручной запуск модуля
def main() -> None:
    # выбор тестового дела
    case_number = "А40-172759-2021__20221115"

    try:
        run_date_window_builder_pipeline_for_case(case_number)
    except FileNotFoundError as error:
        print(str(error))
        print("\nПроверь case_number или положи PDF по ожидаемому пути.")


if __name__ == "__main__":
    main()
