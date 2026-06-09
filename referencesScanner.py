import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


# МОДЕЛИ ДАННЫХ

@dataclass(frozen=True)
class LegalReferenceHit:
    sentence_id: str
    zone_id: str
    zone_type: str
    page_start: Any
    page_end: Any
    category: str
    value: str
    text: str


# КОНСТАНТЫ

SCANNED_ZONE_TYPES = {
    "procedural_history",
    "party_position",
    "court_reasoning",
}

ARTICLE_WORD_RE = r"(?:стат(?:ья|ьи|ей|е|ями|ях)|ст\.|част(?:ь|и|ью|ей|ями|ях)|ч\.|пункт[а-я]*|п\.)"

ARTICLE_LIST_RE = (
    r"\d+(?:\.\d+)?"
    r"(?:\s*(?:,|и|-)\s*\d+(?:\.\d+)?)*"
)

CODE_NAME_RE = (
    r"(?:"
    r"АПК\s*РФ|"
    r"ГК\s*РФ|"
    r"Арбитражного\s+процессуального\s+кодекса\s+Российской\s+Федерации|"
    r"Гражданского\s+кодекса\s+Российской\s+Федерации|"
    r"Кодекса"
    r")"
)

REFERENCE_PATTERNS = [
    (
        "code_articles",
        re.compile(
            rf"\b{ARTICLE_WORD_RE}\s*{ARTICLE_LIST_RE}"
            rf".{{0,140}}?\b{CODE_NAME_RE}",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "bankruptcy_law_articles",
        re.compile(
            rf"\b{ARTICLE_WORD_RE}\s*{ARTICLE_LIST_RE}"
            r".{0,180}?"
            r"(?:Закона\s+о\s+банкротстве|"
            r"Федерального\s+закона\s+[^.]{0,120}?О\s+несостоятельности\s*\(\s*банкротстве\s*\))",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "federal_laws",
        re.compile(
            r"\bФедеральн(?:ый|ого)\s+закон[а-я]*\s+от\s+\d{2}\.\d{2}\.\d{4}"
            r"\s*№\s*[\w\-ФЗ]+",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "plenum_points",
        re.compile(
            r"\bпункт[а-я]*\s+\d+(?:\.\d+)?\s+"
            r"постановлени[ея]\s+Пленума\s+"
            r"(?:Высшего\s+Арбитражного\s+Суда|Верховного\s+Суда)"
            r"\s+Российской\s+Федерации\s+"
            r"от\s+\d{2}\.\d{2}\.\d{4}"
            r"(?:\s*года|\s*г\.)?"
            r"\s*№\s*\d+",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "plenum_resolutions",
        re.compile(
            r"\bпостановлени[еяию]\s+Пленума\s+"
            r"(?:Высшего\s+Арбитражного\s+Суда|Верховного\s+Суда)"
            r"\s+Российской\s+Федерации\s+"
            r"от\s+\d{2}\.\d{2}\.\d{4}"
            r"(?:\s*года|\s*г\.)?"
            r"\s*№\s*\d+",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "supreme_court_reviews",
        re.compile(
            r"\bОбзор[а-я\s]+судебной\s+практики\s+"
            r"(?:Верховного\s+Суда\s+Российской\s+Федерации|Президиума\s+Верховного\s+Суда)"
            r".{0,160}?(?:утвержден[а-я\s]+)?\d{2}\.\d{2}\.\d{4}",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "supreme_court_reviews_short",
        re.compile(
            r"\bпункт[а-я]*\s+\d+(?:\.\d+)?\s+Обзор[а-я\s]+"
            r"судебной\s+практики\s+от\s+\d{2}\.\d{2}\.\d{4}",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "supreme_court_definitions",
        re.compile(
            r"\bопредел\w{0,12}\s+"
            r"(?:Судебной\s+коллегии\s+по\s+экономическим\s+спорам\s+)?"
            r"(?:"
            r"Верховн\w+\s+Суд\w+\s+Российск\w+\s+Федерац\w+|"
            r"ВС\s*РФ"
            r")"
            r"(?:.{0,80}?"
            r"(?:от\s+\d{2}\.\d{2}\.\d{4})?"
            r"(?:.{0,40}?№\s*[\wА-Яа-яЁё\-()/]+)?"
            r")?",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "lower_court_cases",
        re.compile(
            r"\b(?:решени[ея]|определени[ея]|постановлени[ея])\s+"
            r"(?:Арбитражного\s+суда|[А-ЯЁа-яё\s]+суда)"
            r".{0,160}?от\s+\d{2}\.\d{2}\.\d{4}"
            r".{0,120}?\bпо\s+делу\s*№\s*[АA]\d{1,4}\s*-\s*\d+[/\-]\d{2,4}",
            flags=re.IGNORECASE,
        ),
    ),
]


# НОРМАЛИЗАЦИЯ

### нормализация строки для поиска правовых ссылок
def _norm(
    text: Any, # исходное значение
) -> str:
    value = str(text or "")

    # замена специальных пробелов и кавычек
    value = value.replace("\xa0", " ")
    value = value.replace("«", '"').replace("»", '"')
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")

    # схлопывание пробельных символов
    value = re.sub(r"\s+", " ", value)

    return value.strip()


# ПОИСК ПРАВОВЫХ ССЫЛОК

### поиск правовых ссылок в одном предложении
def scan_sentence_for_references(
    sentence: dict, # предложение с метаданными
) -> list:
    text = _norm(sentence.get("text"))

    # пропуск пустого предложения
    if not text:
        return []

    hits = []

    # применение набора регулярных шаблонов
    for category, pattern in REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            hits.append(
                LegalReferenceHit(
                    sentence_id=str(sentence.get("sentence_id")),
                    zone_id=str(sentence.get("zone_id")),
                    zone_type=str(sentence.get("zone_type")),
                    page_start=sentence.get("page_start"),
                    page_end=sentence.get("page_end"),
                    category=category,
                    value=_norm(match.group(0)),
                    text=text,
                )
            )

    return hits


### сканирование предложений и сбор отчета по правовым ссылкам
def scan_legal_references(
    sentences: Sequence, # список предложений
) -> dict:
    hits = []
    scanned_sentences_count = 0
    skipped_sentences_count = 0

    # фильтрация зон для сканирования
    for sentence in sentences:
        zone_type = str(sentence.get("zone_type") or "unknown").strip()

        if zone_type not in SCANNED_ZONE_TYPES:
            skipped_sentences_count += 1
            continue

        scanned_sentences_count += 1
        hits.extend(scan_sentence_for_references(sentence))

    # формирование blacklist по идентификаторам предложений
    blacklisted_sentence_ids = sorted({hit.sentence_id for hit in hits})

    by_category = {}
    for hit in hits:
        by_category.setdefault(hit.category, []).append(asdict(hit))

    return {
        "blacklisted_sentence_ids": blacklisted_sentence_ids,
        "blacklisted_count": len(blacklisted_sentence_ids),
        "hits_count": len(hits),
        "scanned_sentences_count": scanned_sentences_count,
        "skipped_sentences_count": skipped_sentences_count,
        "scanned_zone_types": sorted(SCANNED_ZONE_TYPES),
        "legal_references": by_category,
    }


### построение набора предложений для исключения из окон
def build_reference_blacklist(
    sentences: Sequence, # список предложений
) -> set:
    report = scan_legal_references(sentences)
    return set(report["blacklisted_sentence_ids"])


# СЛУЖЕБНЫЕ ФУНКЦИИ

### определение пути к pdf по номеру дела
def _resolve_pdf_path(
    case_number: str, # номер дела и дата в имени файла
) -> Path:
    local_pdf = Path(f"{case_number}.pdf")

    # поиск pdf рядом со скриптом
    if local_pdf.exists():
        return local_pdf

    project_pdf = Path("data/cassation_docs/кассация") / f"{case_number}.pdf"

    # поиск pdf в проектной директории
    if project_pdf.exists():
        return project_pdf

    raise FileNotFoundError(
        "Не найден PDF.\n"
        f"Проверено:\n"
        f"1) {local_pdf.resolve()}\n"
        f"2) {project_pdf.resolve()}"
    )


### печать отчета по найденным правовым ссылкам
def print_references_report(
    report: dict, # отчет сканера правовых ссылок
) -> None:
    print("\n" + "=" * 120)
    print("ОТЧЕТ СКАНЕРА ПРАВОВЫХ ССЫЛОК")
    print("=" * 120)

    print(f"Найдено ссылок: {report['hits_count']}")
    print(f"Предложений в blacklist: {report['blacklisted_count']}")

    print("\nBLACKLIST SENTENCE IDS:")
    print(", ".join(report["blacklisted_sentence_ids"]) or "—")

    legal_references = report.get("legal_references") or {}

    # вывод ссылок по категориям
    for category, hits in legal_references.items():
        print("\n" + "-" * 120)
        print(f"{category}: {len(hits)}")

        for hit in hits:
            print("\n" + f"[{hit['sentence_id']}] zone={hit['zone_type']}:{hit['zone_id']} pages={hit['page_start']}-{hit['page_end']}")
            print(f"VALUE: {hit['value']}")
            print(f"TEXT: {hit['text']}")

    print(f"Просканировано предложений: {report.get('scanned_sentences_count')}")
    print(f"Пропущено предложений по зонам: {report.get('skipped_sentences_count')}")
    print(f"Сканируемые зоны: {report.get('scanned_zone_types')}")


### запуск полного пайплайна поиска правовых ссылок
def run_references_scanner_pipeline(
    case_number: str, # номер дела и дата в имени файла
) -> dict:
    from pdfParser import extract_pdf_pages
    from structureSplitter import split_pages_into_zones
    from sentenceExtractor import extract_sentences_from_zones

    pdf_path = _resolve_pdf_path(case_number)

    # извлечение страниц и зон
    parsed = extract_pdf_pages(str(pdf_path))
    zones = split_pages_into_zones(parsed)
    sentences = extract_sentences_from_zones(zones, include_operative_part=False)

    # поиск правовых ссылок
    report = scan_legal_references(sentences)

    print("\n" + "=" * 120)
    print("СТАТИСТИКА ПАЙПЛАЙНА")
    print("=" * 120)
    print(f"PDF: {pdf_path.resolve()}")
    print(f"Зон: {len(zones)}")
    print(f"Предложений: {len(sentences)}")

    print_references_report(report)

    return report


# РУЧНОЙ ЗАПУСК

### ручной запуск сканера правовых ссылок
def main() -> None:
    case_number = "А40-172759-2021__20221115"

    # запуск пайплайна с обработкой отсутствующего pdf
    try:
        run_references_scanner_pipeline(case_number)
    except FileNotFoundError as error:
        print(str(error))
        print("\nПроверь case_number или положи PDF по ожидаемому пути")


if __name__ == "__main__":
    main()
