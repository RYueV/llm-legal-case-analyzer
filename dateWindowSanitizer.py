import re
from pathlib import Path
from typing import Any, Sequence


# КОНСТАНТЫ

SERVICE_PREFIX_PATTERNS = [
    r"^\s*с\s*у\s*д\s*у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л\s*:?\s*",
    r"^\s*у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л\s*:?\s*",
    r"^\s*как\s+(?:видно|следует|усматривается)\s+из\s+материалов\s+дела\s*,?\s*",
    r"^\s*как\s+установлено\s+судами\s*,?\s*",
    r"^\s*судами\s+установлено\s+и\s+следует\s+из\s+материалов\s+дела\s*,?\s*что\s*",
]

HEARING_PREFIX_PATTERNS = [
    r"^\s*в\s+судебном\s+заседании\s+",
    r"^\s*в\s+судебных\s+заседаниях\s+",
    r"^\s*после\s+перерыва\s+",
]

ADDRESS_AND_REQUISITES_PATTERNS = [
    r"\bадрес\s*:\s*[^.;]*",
    r"\bИНН\s*\d+",
    r"\bОГРН\s*\d+",
    r"\bКПП\s*\d+",
    r"\(\s*,?\s*\)",
]

LEGAL_FRAGMENT_PATTERNS = [
    r"\s*,?\s*руководствуясь\s+статьями\s+[^.]*?(?:Российской\s+Федерации|АПК\s*РФ|ГК\s*РФ)\s*,?",
    r"\s*,?\s*в\s+соответствии\s+с\s+положениями\s+статей\s+[^.]*?(?:Российской\s+Федерации|АПК\s*РФ|ГК\s*РФ)\s*,?",
    r"\s*,?\s*в\s+соответствии\s+со\s+статьей\s+\d+(?:\.\d+)?\s+(?:Кодекса|АПК\s*РФ|ГК\s*РФ)\s*,?",
    r"\s*,?\s*с\s+учетом\s+положений\s+статьи\s+\d+(?:\.\d+)?\s+(?:Кодекса|АПК\s*РФ|ГК\s*РФ)\s*,?",
]


# НОРМАЛИЗАЦИЯ

### базовая нормализация текста
def _norm(
    text: Any, # исходное значение
) -> str:
    value = str(text or "")

    # унификация пробелов и символов
    value = value.replace("\xa0", " ")
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    value = value.replace("«", '"').replace("»", '"')

    # сжатие пробелов
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)

    return value.strip()


# ОЧИСТКА ПРЕДЛОЖЕНИЙ

### очистка текста одного предложения
def clean_sentence_text(
    text: str, # текст предложения
) -> str:
    cleaned = _norm(text)

    # удаление служебных префиксов
    for pattern in SERVICE_PREFIX_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    # удаление префиксов судебного заседания
    for pattern in HEARING_PREFIX_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    # удаление адресов и реквизитов
    for pattern in ADDRESS_AND_REQUISITES_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    # удаление хвостов нормативных ссылок
    for pattern in LEGAL_FRAGMENT_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE | re.DOTALL)

    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    cleaned = cleaned.strip(" ,;:-")

    return cleaned


# ОЧИСТКА ОКОН

### очистка одного окна по датам
def sanitize_date_window(
    window: dict[str, Any], # окно по датам
):
    sentences = list(window.get("sentences") or [])
    date_sentence_ids = {str(item) for item in window.get("date_sentence_ids") or []}
    cleaned_sentences: list[dict[str, Any]] = []

    # очистка предложений внутри окна
    for sentence in sentences:
        original_text = str(sentence.get("text") or "")
        cleaned_text = clean_sentence_text(original_text)

        if not cleaned_text:
            continue

        cleaned_sentence = dict(sentence)
        cleaned_sentence["text"] = cleaned_text
        cleaned_sentences.append(cleaned_sentence)

    if not cleaned_sentences:
        return None

    kept_sentence_ids = {str(sentence.get("sentence_id")) for sentence in cleaned_sentences}
    kept_date_sentence_ids = [
        sentence_id
        for sentence_id in date_sentence_ids
        if sentence_id in kept_sentence_ids
    ]

    if not kept_date_sentence_ids:
        return None

    # пересборка текста окна
    text_lines: list[str] = []
    for sentence in cleaned_sentences:
        sentence_id = str(sentence.get("sentence_id"))
        marker = "*" if sentence_id in kept_date_sentence_ids else "-"
        text_lines.append(f"{marker} {sentence.get('text')}")

    result = dict(window)
    result["sentences"] = cleaned_sentences
    result["sentence_ids"] = [str(sentence.get("sentence_id")) for sentence in cleaned_sentences]
    result["date_sentence_ids"] = kept_date_sentence_ids
    result["text"] = "\n".join(text_lines).strip()

    return result


### очистка списка окон по датам
def sanitize_date_windows(
    windows: Sequence[dict[str, Any]], # окна по датам
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    # очистка каждого окна
    for window in windows:
        cleaned = sanitize_date_window(dict(window))
        if cleaned is not None:
            result.append(cleaned)

    # перенумерация очищенных окон
    for index, window in enumerate(result, start=1):
        window["window_id"] = f"sw{index:05d}"

    return result


# ТЕКСТОВЫЙ ВЫВОД

### сборка компактного текста очищенных окон
def build_compact_sanitized_windows_text(
    windows: Sequence[dict[str, Any]], # очищенные окна по датам
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


### запуск очистки окон для дела
def run_sanitizer_pipeline_for_case(
    case_number: str, # номер дела и дата в имени файла
) -> list[dict[str, Any]]:
    from dateWindowBuilder import build_compact_windows_text, build_date_windows
    from headerExtractor import extract_header_metadata_from_zones
    from pdfParser import extract_pdf_pages
    from referencesScanner import build_reference_blacklist
    from sentenceExtractor import extract_sentences_from_zones
    from structureSplitter import split_pages_into_zones

    pdf_path = resolve_pdf_path(case_number)

    # последовательное выполнение этапов pipeline
    parsed = extract_pdf_pages(str(pdf_path))
    zones = split_pages_into_zones(parsed)
    sentences = extract_sentences_from_zones(zones, include_operative_part=False)

    header = extract_header_metadata_from_zones(zones)
    blacklist = build_reference_blacklist(sentences)

    windows = build_date_windows(
        sentences,
        blacklisted_sentence_ids=blacklist,
    )

    original_text = build_compact_windows_text(windows)
    sanitized_windows = sanitize_date_windows(windows)
    sanitized_text = build_compact_sanitized_windows_text(sanitized_windows)

    # сохранение очищенного txt
    output_txt = Path(f"{case_number}_sanitized_date_windows.txt")
    output_txt.write_text(sanitized_text, encoding="utf-8")

    original_len = len(original_text)
    sanitized_len = len(sanitized_text)
    reduction = 0 if original_len == 0 else round((1 - sanitized_len / original_len) * 100, 2)

    print("\n" + "=" * 120)
    print("ОТЧЕТ ОБ ОЧИСТКЕ ОКОН ПО ДАТАМ")
    print("=" * 120)
    print(f"PDF: {pdf_path.resolve()}")
    print(f"Номер дела: {header.case_number}")
    print(f"Дата акта: {header.act_date}")
    print(f"Суд: {header.court_name}")
    print(f"Должник: {header.debtor_name}")
    print("-" * 120)
    print(f"Предложений в blacklist referencesScanner: {len(blacklist)}")
    print(f"Окон до очистки: {len(windows)}")
    print(f"Окон после очистки: {len(sanitized_windows)}")
    print(f"Длина до очистки: {original_len}")
    print(f"Длина после очистки: {sanitized_len}")
    print(f"Сокращение: {reduction}%")
    print(f"TXT сохранен: {output_txt.resolve()}")

    return sanitized_windows


### ручной запуск модуля
def main() -> None:
    # выбор тестового дела
    case_number = "А56-21124-2019__20210318"

    try:
        run_sanitizer_pipeline_for_case(case_number)
    except FileNotFoundError as error:
        print(str(error))
        print("\nПроверь case_number или положи PDF по ожидаемому пути.")


if __name__ == "__main__":
    main()
