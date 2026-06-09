import re
import fitz


# КОНСТАНТЫ

_HEADINGS = re.compile(
    r"""
    ^\s*(?:
        установил(?:а)? |
        с\s*у\s*д\s+у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)? |
        у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)? |
        решил(?:а)? |
        постановил(?:а)? |
        п\s*о\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)? |
        определил(?:а)? |
        о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л(?:а)? |
        вводная\s+часть |
        описательная\s+часть |
        мотивировочная\s+часть |
        резолютивная\s+часть
    )\s*:?\s*$
    """,
    flags=re.IGNORECASE | re.VERBOSE
)

_CASE_NUMBER_PATTERN = r"[АA]\d{1,4}\s*-\s*\d+(?:\s*/\s*\d{2,4}|\s*-\s*\d{2,4})"


# РАБОТА С НОМЕРОМ ДЕЛА

### нормализация номера дела
def _normalize_case_number(
    case_number: str # номер дела
) -> str:
    # удаление пробельных символов
    normalized = re.sub(r"\s+", "", case_number)

    # приведение к верхнему регистру и кириллической букве
    return normalized.upper().replace("A", "А")


### поиск основного номера дела
def _find_main_case_number(
    text: str # текст первой страницы
) -> str:
    # поиск реквизита дела в шапке
    match = re.search(
        rf"(?i)\bДело\s*№\s*({_CASE_NUMBER_PATTERN})",
        text
    )

    # обработка отсутствующего номера
    if not match:
        return None

    # нормализация найденного номера
    return _normalize_case_number(match.group(1))


### удаление служебных строк с основным номером дела
def _remove_main_case_number_artifacts(
    text: str, # исходный текст
    main_case_number # основной номер дела
) -> str:
    # пропуск удаления при отсутствии основного номера
    if not main_case_number:
        return text

    escaped = re.escape(main_case_number)
    lines = text.splitlines()
    result = []

    # проверка каждой строки на колонтитул
    for line in lines:
        if re.fullmatch(rf"\s*\d{{1,4}}\s+{escaped}\s*", line, flags=re.IGNORECASE):
            continue

        # удаление одиночного номера дела с сохранением реквизита после строки дело №
        if re.fullmatch(rf"\s*{escaped}\s*", line, flags=re.IGNORECASE):
            previous_nonempty = ""

            for previous in reversed(result):
                if previous.strip():
                    previous_nonempty = previous.strip()
                    break

            if re.search(r"(?i)\bдело\s*№\s*$", previous_nonempty):
                result.append(line)

            continue

        result.append(line)

    # сборка текста без служебных строк
    return "\n".join(result)


# БАЗОВАЯ НОРМАЛИЗАЦИЯ

### нормализация базовых символов
def _normalize_basic_chars(
    text: str # исходный текст
) -> str:
    # нормализация переносов строк
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\v", "\n").replace("\f", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")

    # нормализация пробелов и невидимых символов
    text = text.replace("\xa0", " ").replace("\u202f", " ").replace("\u2007", " ")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\ufffe", "").replace("\uFFFD", "")

    # нормализация лигатур
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")

    # нормализация дефисов
    for dash in ["‐", "‒", "–", "—", "−"]:
        text = text.replace(dash, "-")

    # нормализация кавычек
    for quote in ["«", "»", "“", "”", "„"]:
        text = text.replace(quote, '"')

    return text


### удаление ссылок почты и телефонов
def _remove_links_emails_phones(
    text: str # исходный текст
) -> str:
    # удаление ссылок
    text = re.sub(r"(?i)\bhttps?://[^\s]+", "", text)
    text = re.sub(r"(?i)\bwww\.[^\s]+", "", text)

    # удаление адресов электронной почты
    text = re.sub(
        r"(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b",
        "",
        text
    )

    # удаление телефонов с явной меткой
    text = re.sub(
        r"(?i)\b(?:тел\.?|телефон|факс)\s*:?\s*(?:\+?7|8)?\s*\(?\d{3,5}\)?[\s-]*\d[\d\s().-]{5,}",
        "",
        text
    )

    # удаление длинных телефонных номеров без явной метки
    text = re.sub(
        r"(?<![\w№/-])(?:\+7|8)\s*\(?\d{3,5}\)?[\s-]*\d[\d\s().-]{6,}",
        "",
        text
    )

    # удаление пустых контактных меток
    text = re.sub(r"(?i)\b(?:e-mail|email|эл\.?\s*почта)\s*: ?", "", text)
    text = re.sub(r"(?i)\b(?:тел\.?|телефон|факс)\s*: ?", "", text)

    return text


### удаление адресных фрагментов
def _remove_addresses(
    text: str # исходный текст
) -> str:
    address_label = r"(?:адрес|юридический\s+адрес|почтовый\s+адрес|место\s+нахождения|местонахождение)"

    # удаление адреса до технических идентификаторов
    text = re.sub(
        rf"(?is),?\s*\b{address_label}\s*:\s*.*?"
        rf"(?:\b(?:ОГРН|ОГРНИП|ИНН|КПП)\s*№?\s*\d+\s*,?\s*)+"
        rf"(?=\s*(?:\(|,|\.|;|$))",
        "",
        text
    )

    # удаление адреса до безопасного стоп-сигнала
    text = re.sub(
        rf"(?is),?\s*\b{address_label}\s*:\s*.*?"
        rf"(?=\s*(?:\(\s*далее\b|далее\s*[-–—]|,\s*\d{{2}}\.\d{{2}}\.\d{{4}}\b|$))",
        "",
        text
    )

    street_words = (
        r"ул\.|улица|пр\.|просп\.|проспект|пер\.|переулок|"
        r"пл\.|площадь|наб\.|набережная|ш\.|шоссе|б-р|бульвар|"
        r"д\.|дом\s+\d+|корп\.|корпус|стр\.|строение|лит\.|пом\.|офис"
    )

    # удаление отдельных адресных строк
    text = re.sub(
        rf"(?mi)^\s*(?:\d{{6}},\s*)?(?=[^\n]*\b(?:{street_words})\b)[^\n]*$",
        "",
        text
    )

    return text


### удаление технических идентификаторов
def _remove_technical_identifiers(
    text: str # исходный текст
) -> str:
    # удаление регистрационных номеров
    text = re.sub(r"(?i)\bОГРН(?:ИП)?\s*№?\s*\d+\b", "", text)
    text = re.sub(r"(?i)\bИНН\s*№?\s*\d+\b", "", text)
    text = re.sub(r"(?i)\bКПП\s*№?\s*\d+\b", "", text)

    # удаление пустых скобок
    text = re.sub(r"\(\s*,\s*\)", "", text)
    text = re.sub(r"\(\s*\)", "", text)

    return text


# УДАЛЕНИЕ ТЕХНИЧЕСКОГО ШУМА

### проверка табличной строки
def _is_table_like_line(
    line: str # строка текста
) -> bool:
    stripped = line.strip()

    # отбрасывание пустой строки
    if not stripped:
        return False

    # сохранение длинной текстовой строки
    if len(stripped) > 80 and re.search(r"[а-яёa-z]{3,}", stripped, flags=re.IGNORECASE):
        return False

    # расчет табличных признаков
    many_spaces = len(re.findall(r" {2,}", stripped))
    has_tabs = "\t" in stripped
    has_pipe_separator = " | " in stripped
    has_several_dates = len(re.findall(r"\d{1,2}\.\d{1,2}\.\d{2,4}", stripped)) >= 2
    numeric_groups = len(re.findall(r"\b\d[\d\s.,/-]*\b", stripped))

    # оценка похожести строки на таблицу
    return (
        many_spaces >= 3
        or has_tabs
        or has_pipe_separator
        or has_several_dates
        or (numeric_groups >= 5 and len(stripped) < 120)
    )


### удаление постраничных артефактов
def _remove_page_artifacts(
    text: str, # исходный текст
    main_case_number: str # основной номер дела
) -> str:
    # удаление служебного номера дела
    text = _remove_main_case_number_artifacts(text, main_case_number)

    # удаление номеров страниц
    text = re.sub(r"(?mi)^\s*\d{1,4}\s*$", "", text)
    text = re.sub(r"(?mi)^\s*-+\s*\d{1,4}\s*-+\s*$", "", text)
    text = re.sub(r"(?mi)^\s*стр\.?\s*\d{1,4}\s*$", "", text)
    text = re.sub(r"(?mi)^\s*страница\s*\d{1,4}\s*$", "", text)
    text = re.sub(r"(?mi)^\s*\d{1,4}\s*/\s*\d{1,4}\s*$", "", text)

    return text


### удаление технической шапки первой страницы
def _remove_first_page_technical_header(
    text: str, # исходный текст
    *,
    is_first_page: bool # признак первой страницы
) -> str:
    # пропуск не первой страницы
    if not is_first_page:
        return text

    # удаление внутреннего номера документа
    text = re.sub(
        r"(?mi)^\s*\d+/\d+-\d+(?:\(\d+\))?\s*$",
        "",
        text
    )

    # удаление адресной строки шапки
    text = re.sub(
        r"(?mi)^\s*\d{6},.*$",
        "",
        text
    )

    return text


### удаление служебных строк
def _remove_service_lines(
    text: str # исходный текст
) -> str:
    service_line_patterns = [
        r"^\s*электронная подпись.*$",
        r"^\s*документ подписан.*$",
        r"^\s*копия верна.*$",
        r"^\s*файл сформирован.*$",
        r"^\s*kad\.arbitr\.ru.*$",
        r"^\s*мой арбитр.*$",
    ]

    # последовательное применение служебных шаблонов
    for pattern in service_line_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)

    return text


### удаление процессуального шума
def _remove_procedural_noise(
    text: str # исходный текст
) -> str:
    patterns = [
        r"(?is)Присутствующий\s+в\s+судебном\s+заседании\s+представитель.*?судебное\s+заседание\s+не\s+обеспечили\.",
        r"(?is)Согласно\s+части\s+3\s+статьи\s+284.*?проведения\s+судебного\s+заседания\.",
        r"(?is)Иные\s+доводы\s+заявителя\s+кассационной\s+жалобы.*?процессуального\s+права\.",
        r"(?is)Поскольку\s+нарушений\s+норм\s+процессуального\s+права.*?кассационной\s+жалобы\s+отсутствуют\.",
    ]

    # последовательное удаление типовых процессуальных фрагментов
    for pattern in patterns:
        text = re.sub(pattern, "", text)

    return text


### удаление хвоста обжалования
def _remove_appeal_tail(
    text: str # исходный текст
) -> str:
    # удаление стандартного разъяснения о вступлении в силу
    return re.sub(
        r"(?is)Постановление\s+вступает\s+в\s+законную\s+силу.*?Арбитражного\s+процессуального\s+кодекса\s+Российской\s+Федерации\.",
        "",
        text
    )


### удаление подписей судей
def _remove_judge_signatures(
    text: str # исходный текст
) -> str:
    # удаление подписи председательствующего судьи
    text = re.sub(
        r"(?is)\n?\s*Председательствующий\s+судья\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]+.*$",
        "",
        text
    )

    # удаление блока подписей состава суда
    text = re.sub(
        r"(?is)\n?\s*Председательствующий\s+судья\s*\n?.*?\n\s*Судьи\s*\n?.*$",
        "",
        text
    )

    return text


# РАБОТА С ТАБЛИЦАМИ

### очистка ячейки таблицы
def _clean_table_cell(
    value # значение ячейки
) -> str:
    # обработка пустой ячейки
    if value is None:
        return ""

    # нормализация пробелов в ячейке
    value = str(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


### форматирование простой таблицы
def _format_simple_table(
    table: list[list] # таблица из pdf
) -> str:
    formatted_rows = []

    # очистка строк таблицы
    for row in table:
        cleaned_cells = [_clean_table_cell(cell) for cell in row]
        cleaned_cells = [cell for cell in cleaned_cells if cell]

        # сборка непустой строки таблицы
        if cleaned_cells:
            formatted_rows.append(" | ".join(cleaned_cells))

    return "\n".join(formatted_rows).strip()


### извлечение простых таблиц со страницы
def _extract_simple_tables(
    page # страница pdf
) -> list[str]:
    # проверка поддержки извлечения таблиц
    if not hasattr(page, "find_tables"):
        return []

    # безопасный поиск таблиц
    try:
        tables = page.find_tables()
    except Exception:
        return []

    result = []

    # извлечение и форматирование таблиц
    for table in tables:
        try:
            extracted = table.extract()
        except Exception:
            continue

        formatted = _format_simple_table(extracted)

        if formatted:
            result.append(formatted)

    return result


# ВОССТАНОВЛЕНИЕ И ФИНАЛЬНАЯ ОЧИСТКА ТЕКСТА

### восстановление абзацев
def _restore_paragraphs(
    text: str # исходный текст
) -> str:
    # нормализация пустых строк
    text = re.sub(r"\n\s*\n+", "\n\n", text)

    lines = text.splitlines()
    rebuilt_lines = []
    current = ""

    # сборка строк в абзацы
    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            if current:
                rebuilt_lines.append(current.strip())
                current = ""

            rebuilt_lines.append("")
            continue

        if _HEADINGS.match(line):
            if current:
                rebuilt_lines.append(current.strip())
                current = ""

            rebuilt_lines.append(line)
            continue

        if _is_table_like_line(line):
            if current:
                rebuilt_lines.append(current.strip())
                current = ""

            rebuilt_lines.append(line)
            continue

        # присоединение обычной строки к текущему абзацу
        if not current:
            current = line
        else:
            current += " " + line

    # добавление последнего абзаца
    if current:
        rebuilt_lines.append(current.strip())

    return "\n".join(rebuilt_lines)


### исправление типовых разрывов pdf
def _fix_common_pdf_breaks(
    text: str # исходный текст
) -> str:
    # восстановление номера дела
    text = re.sub(
        r"([АA]\d{1,4})\s*-\s*(\d+/\d{2,4})",
        r"\1-\2",
        text
    )

    # восстановление разорванных составных номеров
    text = re.sub(
        r"(\d+)\s*-\s*([А-ЯЁA-Z]{1,4}\d+)",
        r"\1-\2",
        text
    )

    text = re.sub(
        r"([A-Za-zА-Яа-яЁё0-9]+/\d+)\s*-\s*(\d+)",
        r"\1-\2",
        text
    )

    # исправление частых склеек слов
    text = text.replace("счетафактуры", "счета-фактуры")

    # восстановление кавычек после организационно-правовой формы
    text = re.sub(
        r'\b(ООО|АО|ПАО|ЗАО)\s+([А-ЯЁA-Z][^"\n]{1,80})"',
        r'\1 "\2"',
        text
    )

    return text


### финальная нормализация текста
def _final_normalize(
    text: str, # исходный текст
    main_case_number=None # основной номер дела
) -> str:
    # повторная очистка технических фрагментов
    text = _remove_links_emails_phones(text)
    text = _remove_addresses(text)
    text = _remove_technical_identifiers(text)
    text = _fix_common_pdf_breaks(text)
    text = _remove_main_case_number_artifacts(text, main_case_number)

    # нормализация пробелов и пунктуации
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])([А-Яа-яЁёA-Za-z])", r"\1 \2", text)

    # исправление частых склеек
    text = text.replace(
        "информационнотелекоммуникационной",
        "информационно-телекоммуникационной"
    )
    text = text.replace(
        "гражданскоправовой",
        "гражданско-правовой"
    )

    # нормализация кавычек и пустых конструкций
    text = re.sub(r'"\s+', '" ', text)
    text = re.sub(r'\s+"', ' "', text)
    text = re.sub(r'"\s+([,.;:)])', r'"\1', text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\(\s*,?\s*\)", "", text)

    # нормализация пустых строк
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" *\n\n *", "\n\n", text)

    return text.strip()


# ОСНОВНАЯ ОЧИСТКА И ИЗВЛЕЧЕНИЕ

### очистка текста страницы pdf
def clean_pdf_text(
    text: str, # исходный текст страницы
    *,
    is_first_page: bool = False, # признак первой страницы
    main_case_number=None # основной номер дела
) -> str:
    # обработка пустого текста
    if not text:
        return ""

    # базовая нормализация символов
    text = _normalize_basic_chars(text)

    # удаление постраничных и технических артефактов
    text = _remove_page_artifacts(text, main_case_number)
    text = _remove_first_page_technical_header(text, is_first_page=is_first_page)
    text = _remove_links_emails_phones(text)
    text = _remove_addresses(text)
    text = _remove_technical_identifiers(text)
    text = _remove_service_lines(text)

    # склейка переносов внутри слов
    text = re.sub(
        r"([А-Яа-яЁёA-Za-z])-\s*\n\s*([А-Яа-яЁёA-Za-z])",
        r"\1\2",
        text
    )

    # восстановление абзацев и финальная нормализация
    text = _restore_paragraphs(text)
    text = _final_normalize(text, main_case_number)

    return text


### извлечение страниц pdf
def extract_pdf_pages(
    file_path: str, # путь к pdf-файлу
    extract_tables: bool = True # признак извлечения таблиц
) -> tuple[list[dict], int, int, str]:
    pages = []
    raw_total_length = 0
    cleaned_total_length = 0

    # открытие pdf-документа
    doc = fitz.open(file_path)

    # определение основного номера дела по первой странице
    first_page_raw = doc[0].get_text("text") if len(doc) > 0 else ""
    first_page_raw = _normalize_basic_chars(first_page_raw)
    main_case_number = _find_main_case_number(first_page_raw)

    # постраничное извлечение и очистка текста
    for page_index, page in enumerate(doc, start=1):
        raw_text = page.get_text("text")
        raw_total_length += len(raw_text)

        cleaned_text = clean_pdf_text(
            raw_text,
            is_first_page=(page_index == 1),
            main_case_number=main_case_number
        )

        cleaned_total_length += len(cleaned_text)
        tables = _extract_simple_tables(page) if extract_tables else []

        # сохранение страницы с полезным содержимым
        if cleaned_text or tables:
            pages.append({
                "page": page_index,
                "text": cleaned_text,
                "tables": tables
            })

    # закрытие pdf-документа
    doc.close()

    return pages, raw_total_length, cleaned_total_length, main_case_number


### сборка полного текста из страниц
def build_full_text(
    pages: list[dict], # список страниц
    *,
    main_case_number=None # основной номер дела
) -> str:
    parts = []

    # сборка текстов и таблиц в общий список
    for page in pages:
        if page["text"]:
            parts.append(page["text"])

        if page["tables"]:
            for table in page["tables"]:
                parts.append(table)

    text = "\n\n".join(parts).strip()

    # удаление финального процессуального шума
    text = _remove_procedural_noise(text)
    text = _remove_appeal_tail(text)
    text = _remove_judge_signatures(text)
    text = _final_normalize(text, main_case_number)

    return text


# ТОЧКА ВХОДА

### запуск проверки парсера
def main() -> None:
    pdf_path = "test.pdf"

    # извлечение и очистка страниц
    pages, raw_length, page_cleaned_length, main_case_number = extract_pdf_pages(pdf_path)

    # сборка полного текста
    full_text = build_full_text(
        pages,
        main_case_number=main_case_number
    )

    # расчет статистики очистки
    final_length = len(full_text)
    ratio = final_length / raw_length if raw_length else 0

    print(f"Извлечено страниц с текстом: {len(pages)}")
    print(f"Основной номер дела: {main_case_number}")
    print(f"Символов до очистки: {raw_length}")
    print(f"Символов после постраничной очистки: {page_cleaned_length}")
    print(f"Символов после финальной очистки: {final_length}")
    print(f"Соотношение длины: {ratio:.3f}")

    print("\n--- Обработанный текст целиком ---\n")
    print(full_text)


if __name__ == "__main__":
    main()
