import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# КОНСТАНТЫ

PROCEDURAL_HISTORY_CONTEXT_LIMIT = 12000
PARTY_POSITION_CONTEXT_LIMIT = 6000
COURT_REASONING_HEAD_LIMIT = 9000
DISPUTE_CONTEXT_LIMIT = 1800


# МОДЕЛИ ДАННЫХ

@dataclass(frozen=True)
class HeaderMetadata:
    case_number: str
    act_date: str
    court_name: str
    judges: list[str]
    debtor_name: str


@dataclass(frozen=True)
class CaseContext:
    debtor_name: str
    dispute_context: str


# НОРМАЛИЗАЦИЯ

### нормализация текста без изменения смысла
def _norm(text: Any) -> str:
    # приведение входного значения к строке
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u202f", " ").replace("\u2007", " ")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\uFFFC", " ")
    text = text.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"').replace("„", '"')
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = re.sub(r"\[\[TABLE\]\].*?\[\[/TABLE\]\]", " ", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text.strip()


### удаление повторяющихся значений
def _unique(values: list[str]) -> list[str]:
    # подготовка контейнеров уникальности
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _norm(value).strip(" ,.;:-")
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


### удаление технических скобок
def _strip_technical_parentheses(text: str) -> str:
    text = _norm(text)
    text = re.sub(
        r"\((?=[^)]*(?:ИНН|ОГРН|ОГРНИП|КПП|адрес|далее|место нахождения|местонахождение))[^)]*\)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:ИНН|ОГРН|ОГРНИП|КПП)\s*[:№]?\s*\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bадрес\s*:\s*[^.;]+", " ", text, flags=re.IGNORECASE)
    return _norm(text)


### удаление начального маркера суда
def _strip_leading_court_marker(text: str) -> str:
    text = _norm(text)
    return re.sub(
        r"^(?:"
        r"у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)?|"
        r"с\s*у\s*д\s+у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л(?:а)?|"
        r"установил(?:а)?|"
        r"установил\s+следующее|"
        r"судом\s+установлено"
        r")\s*:?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


# БЕЗОПАСНОЕ ДЕЛЕНИЕ НА ПРЕДЛОЖЕНИЯ

### защита точек внутри сокращений
def _protect_dots(text: str) -> str:
    text = _norm(text)

    text = re.sub(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", r"\1§\2§\3", text)

    text = re.sub(r"\b([А-ЯЁA-Z])\.\s*([А-ЯЁA-Z])\.", r"\1§\2§", text)

    protected = {
        "руб.": "руб§",
        "коп.": "коп§",
        "млн.": "млн§",
        "млрд.": "млрд§",
        "тыс.": "тыс§",
        "г.": "г§",
        "ул.": "ул§",
        "д.": "д§",
        "лит.": "лит§",
        "стр.": "стр§",
        "корп.": "корп§",
        "пом.": "пом§",
        "оф.": "оф§",
        "офис.": "офис§",
        "ст.": "ст§",
        "п.": "п§",
        "пп.": "пп§",
        "ч.": "ч§",
        "абз.": "абз§",
        "т.д.": "т§д§",
        "т.п.": "т§п§",
        "т.е.": "т§е§",
        "в т.ч.": "в т§ч§",
    }
    for src, dst in protected.items():
        text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)

    return text


### восстановление защищенных точек
def _unprotect_dots(text: str) -> str:
    return text.replace("§", ".")


### безопасное деление текста на предложения
def _split_sentences_safe(text: str) -> list[str]:
    # защита сокращений перед делением
    text = _protect_dots(text)
    # деление по границам предложений
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text)
    return [_norm(_unprotect_dots(part)) for part in parts if _norm(_unprotect_dots(part))]


# ИЗВЛЕЧЕНИЕ МЕТАДАННЫХ

### извлечение номера дела
def extract_case_number(header_text: str) -> str:
    # поиск арбитражного номера дела
    match = re.search(r"\b[АA]\d{1,4}\s*-\s*\d+[/\-]\d{2,4}\b", _norm(header_text))
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(0)).replace("A", "А")


### извлечение даты судебного акта
def extract_act_date(header_text: str) -> str:
    text = _norm(header_text)
    patterns = [
        r"Постановление\s+изготовлено\s+в\s+полном\s+об[ъьё]еме\s+(\d{1,2}\s+[а-яА-ЯёЁ]+\s+\d{4}\s+года)",
        r"Постановление\s+в\s+полном\s+об[ъьё]еме\s+изготовлено\s+(\d{1,2}\s+[а-яА-ЯёЁ]+\s+\d{4}\s+года)",
        r"Полный\s+текст\s+постановления\s+изготовлен\s+(\d{1,2}\s+[а-яА-ЯёЁ]+\s+\d{4}\s+года)",
        r"Полный\s+текст\s+постановления\s+изготовлен\s+(\d{1,2}\.\d{1,2}\.\d{4})",
        r"(\d{1,2}\s+[а-яА-ЯёЁ]+\s+\d{4}\s+года)\s+Дело\s*№",
        r"(\d{1,2}\.\d{1,2}\.\d{4})\s+Дело\s*№",
        r"Дело\s*№\s*[АA]\d{1,4}\s*-\s*\d+[/\-]\d{2,4}\s+(\d{1,2}\s+[а-яА-ЯёЁ]+\s+\d{4}\s+года)",
        r"Дело\s*№\s*[АA]\d{1,4}\s*-\s*\d+[/\-]\d{2,4}\s+(\d{1,2}\.\d{1,2}\.\d{4})",
        r"г\.\s*[А-ЯЁа-яё\- ]+\s+(\d{1,2}\s+[а-яА-ЯёЁ]+\s+\d{4}\s+года)\b",
    ]
    # последовательная проверка форматов даты
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _norm(match.group(1))
    return None


### извлечение наименования суда
def extract_court_name(header_text: str) -> str:
    match = re.search(r"АРБИТРАЖНЫЙ\s+СУД\s+[А-ЯЁ\s\-]+?ОКРУГА", _norm(header_text), flags=re.IGNORECASE)
    if not match:
        return None
    return _norm(match.group(0)).upper()


### извлечение состава судей
def extract_judges(header_text: str) -> list[str]:
    text = _norm(header_text)
    match = re.search(
        r"в\s+составе\s*:?\s*(.+?)(?:при\s+ведении\s+протокола|при\s+участии|рассмотрев|рассмотрел|установил|УСТАНОВИЛ)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    fragment = match.group(1)
    names = re.findall(
        r"\b[А-ЯЁ][а-яё]+(?:ой|ова|ева|ина|енко|ко|их|ых|ая|ий|ый|ев|ин|ов|ын)?\s+[А-ЯЁ]\.?\s*[А-ЯЁ]\.?",
        fragment,
    )
    return _unique(names)


# ИЗВЛЕЧЕНИЕ ДОЛЖНИКА

ORG_PREFIX = (
    r"(?:"
    r"общество\s+с\s+ограниченной\s+ответственностью|"
    r"общества\s+с\s+ограниченной\s+ответственностью|"
    r"обществу\s+с\s+ограниченной\s+ответственностью|"
    r"обществом\s+с\s+ограниченной\s+ответственностью|"
    r"общество\s+с\s+ограниченной\s+ответственности|"
    r"общества\s+с\s+ограниченной\s+ответственности|"
    r"обществу\s+с\s+ограниченной\s+ответственности|"
    r"обществом\s+с\s+ограниченной\s+ответственности|"
    r"закрытое\s+акционерное\s+общество|"
    r"закрытого\s+акционерного\s+общества|"
    r"закрытому\s+акционерному\s+обществу|"
    r"закрытым\s+акционерным\s+обществом|"
    r"открытое\s+акционерное\s+общество|"
    r"открытого\s+акционерного\s+общества|"
    r"открытому\s+акционерному\s+обществу|"
    r"открытым\s+акционерным\s+обществом|"
    r"публичное\s+акционерное\s+общество|"
    r"публичного\s+акционерного\s+общества|"
    r"публичному\s+акционерному\s+обществу|"
    r"публичным\s+акционерным\s+обществом|"
    r"акционерное\s+общество|"
    r"акционерного\s+общества|"
    r"акционерному\s+обществу|"
    r"акционерным\s+обществом|"
    r"ООО|АО|ПАО|ОАО|ЗАО"
    r")"
)


SHORT_ORG_PREFIX = r"(?:общество|общества|обществу|обществом)"


### нормализация падежа организации
def _normalize_org_case(name: str) -> str:
    name = _norm(name)
    replacements = [
        (r"^общество\s+с\s+ограниченной\s+ответственностью\b", "общества с ограниченной ответственностью"),
        (r"^общества\s+с\s+ограниченной\s+ответственности\b", "общества с ограниченной ответственностью"),
        (r"^общество\s+с\s+ограниченной\s+ответственности\b", "общества с ограниченной ответственностью"),
        (r"^обществу\s+с\s+ограниченной\s+ответственностью\b", "общества с ограниченной ответственностью"),
        (r"^обществу\s+с\s+ограниченной\s+ответственности\b", "общества с ограниченной ответственностью"),
        (r"^обществом\s+с\s+ограниченной\s+ответственностью\b", "общества с ограниченной ответственностью"),
        (r"^обществом\s+с\s+ограниченной\s+ответственности\b", "общества с ограниченной ответственностью"),
        (r"^акционерное\s+общество\b", "акционерного общества"),
        (r"^акционерному\s+обществу\b", "акционерного общества"),
        (r"^акционерным\s+обществом\b", "акционерного общества"),
        (r"^закрытое\s+акционерное\s+общество\b", "закрытого акционерного общества"),
        (r"^закрытому\s+акционерному\s+обществу\b", "закрытого акционерного общества"),
        (r"^закрытым\s+акционерным\s+обществом\b", "закрытого акционерного общества"),
        (r"^открытое\s+акционерное\s+общество\b", "открытого акционерного общества"),
        (r"^открытому\s+акционерному\s+обществу\b", "открытого акционерного общества"),
        (r"^открытым\s+акционерным\s+обществом\b", "открытого акционерного общества"),
        (r"^публичное\s+акционерное\s+общество\b", "публичного акционерного общества"),
        (r"^публичному\s+акционерному\s+обществу\b", "публичного акционерного общества"),
        (r"^публичным\s+акционерным\s+обществом\b", "публичного акционерного общества"),
    ]
    for pattern, replacement in replacements:
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
    return _norm(name)


### очистка хвоста названия организации
def _cut_org_tail(text: str) -> str:
    text = _strip_technical_parentheses(text)

    stop_patterns = [
        r"\s+(?:и\s+)?у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л.*",
        r"\s+(?:и\s+)?с\s*у\s*д\s+у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л.*",
        r"\s+установил(?:а)?\s*:?.*",
        r"[,;\s]+принят\w*\s+по\s+заявлени\w+.*",
        r"\s+по\s+заявлени\w+.*",
        r"\s+в\s+арбитражн\w+\s+суд.*",
        r"\s+обратил\w+.*",
        r"\s+обрати\w+.*",
        r"[,;\s]+и\s+в\s+рамках\s+дела.*",
        r"\s+с\s+требовани\w+.*",
        r"\s+с\s+заявлени\w+.*",
        r"\s+о\s+включени\w+.*",
        r"\s+об\s+установлени\w+.*",
        r"\s+о\s+признани\w+.*",
        r"\s+об\s+изменени\w+.*",
        r"\s+несостоятельн\w+.*",
        r"\s+банкрот\w+.*",
        r"\s+утвержден\w+.*",
        r"[,;\s]+введен\w+.*",
        r"\s+открыт\w+.*",
        r"\s+на\s+определени\w+.*",
        r"\s+на\s+постановлени\w+.*",
        r"\s+третье\s+лицо.*",
        r"\s+задолженност\w+\s+по\s+договор\w+.*",
        r"\s+по\s+результатам\s+проведени\w+.*",
        r"\s+о\s+процессуальн\w+\s+правопреемств\w+.*",
    ]
    for pattern in stop_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(r"\(.*$", "", text)
    return _norm(text).strip(" ,.;:-")


### извлечение организации из фрагмента
def _extract_org_from_fragment(fragment: str) -> str:
    fragment = _norm(fragment)

    fragment = re.split(
        r"(?i)\s*(?:\(\s*далее\b|,?\s*адрес\b|,?\s*ИНН\b|,?\s*ОГРН\b|,?\s*ОГРНИП\b|,?\s*КПП\b|;)",
        fragment,
        maxsplit=1,
    )[0]

    fragment = _strip_technical_parentheses(fragment)

    first_org = re.search(rf"{ORG_PREFIX}", fragment, flags=re.IGNORECASE)

    if not first_org:
        short_match = re.search(rf"({SHORT_ORG_PREFIX}\s+\"[^\"]{2,160}\")", fragment, flags=re.IGNORECASE)
        if short_match:
            return _cut_org_tail(_normalize_org_case(short_match.group(1)))
        return None

    prefix_before_org = fragment[:first_org.start()]
    if len(prefix_before_org.strip()) > 80:
        return None

    if re.search(r"(?i)\b(?:Определением|Постановлением|Решением|Судом|Арбитражный\s+суд)\b", prefix_before_org):
        return None

    stop = re.search(
        r"(?i)"
        r"(?:"
        r"\s+(?:и\s+)?у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л|"
        r"\s+(?:и\s+)?установил(?:а)?|"
        r"\s+с\s+заявлени\w+|"
        r"\s+обратил\w+|"
        r"\s+обрати\w+|"
        r"\s+в\s+арбитражн\w+\s+суд|"
        r",?\s*принят\w*\s+по\s+заявлени\w+|"
        r"\s+по\s+заявлени\w+|"
        r"\s+несостоятельн\w+|"
        r"\s+банкрот\w+|"
        r"\s+признан\w+|"
        r"\s+введен\w+|"
        r"\s+открыт\w+|"
        r"\s+утвержден\w+|"
        r"\s+на\s+определени\w+|"
        r"\s+на\s+постановлени\w+|"
        r"\s+третье\s+лицо|"
        r"\s+задолженност\w+\s+по\s+договор\w+|"
        r"\s+по\s+результатам\s+проведени\w+|"
        r"\s+о\s+процессуальн\w+\s+правопреемств\w+"
        r")",
        fragment,
    )
    if stop:
        fragment = fragment[:stop.start()]

    fragment = re.split(
        r"(?i)\s*(?:,?\s*\(|,?\s*адрес\b|,?\s*ИНН\b|,?\s*ОГРН\b|,?\s*ОГРНИП\b|,?\s*КПП\b|,?\s*далее\b)",
        fragment,
        maxsplit=1,
    )[0]

    match = re.search(rf"({ORG_PREFIX}\s+.+)", fragment, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    candidate = match.group(1)
    candidate = _cut_org_tail(candidate)

    if not candidate:
        return None

    candidate = re.split(r"(?i)\s+(?:по\s+делу|по\s+заявлению|в\s+рамках\s+дела)\b", candidate, maxsplit=1)[0]
    candidate = _normalize_org_case(candidate)
    return candidate.strip(" ,.;:-") or None


### извлечение наименования должника
def extract_debtor_name(text: str) -> str:
    # нормализация текста перед поиском
    text = _norm(text)

    # поиск организации после банкротной формулы
    after_bankruptcy_patterns = [
        r"(?:по|в\s+рамках|в)\s+дел\w*(?:\s*№?\s*[АA]\d{1,4}\s*-\s*\d+[/\-]\d{2,4})?\s+о\s+(?:несостоятельност\w*\s*\(\s*банкротств\w*\s*\)|банкротств\w*)\s+(.{0,650})",
        r"дел[аеу]?\s+(?:о|от)\s+(?:несостоятельност\w*\s*\(\s*банкротств\w*\s*\)|банкротств\w*)\s+(.{0,650})",
        r"о\s+(?:несостоятельност\w*\s*\(\s*банкротств\w*\s*\)|банкротств\w*)\s+(.{0,650})",
        r"в\s+отношении\s+(.{0,500})",
    ]

    for pattern in after_bankruptcy_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            org = _extract_org_from_fragment(match.group(1))
            if org:
                return org

    before_bankruptcy_patterns = [
        r"(?:дел[ае]|в\s+рамках\s+дела|производство\s+по\s+делу)\s+о\s+признании\s+(.{0,420}?)\s+несостоятельн\w*\s*\(\s*банкрот\w*\s*\)",
        r"(?:заявлени\w+|ходатайств\w+)\s+о\s+признании\s+(.{0,420}?)\s+несостоятельн\w*\s*\(\s*банкрот\w*\s*\)",
        r"(?:заявлени\w+|ходатайств\w+)\s+о\s+признании\s+(.{0,420}?)\s+банкрот\w*",
        r"(.{0,420}?)\s+(?:был[оа]?\s+)?признан\w*\s+(?:несостоятельн\w*\s*)?\(?банкрот\w*\)?",
        r"(.{0,420}?)\s+введен\w*\s+процедур\w+\s+(?:наблюдения|банкротства|конкурсного\s+производства)",
    ]

    # поиск организации перед банкротной формулой
    for pattern in before_bankruptcy_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            org = _extract_org_from_fragment(match.group(1))
            if org:
                return org

    reestr_patterns = [
        r"реестр\s+требований\s+кредиторов\s+(.{0,420})",
        r"реестр\s+кредиторов\s+(.{0,420})",
    ]
    # поиск должника через формулу реестра
    for pattern in reestr_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            org = _extract_org_from_fragment(match.group(1))
            if org:
                return org

    return None


# ИЗВЛЕЧЕНИЕ ПРЕДМЕТА СПОРА

_LOWER_COURT_STOP_RE = re.compile(
    r"""
    (?=
        \bТребования\s+основаны\b |
        \bОпределением\b |
        \bПостановлением\b |
        \bРешением\b |
        \bНе\s+соглас |
        \bВ\s+кассационн |
        \bКассационн(?:ая|ые)\s+жалоб |
        \bВ\s+судебном\s+заседании |
        \bПредставител |
        \bИные\s+лица |
        \bЗаконность\b |
        \bИзучив\b |
        \bПроверив\b |
        \bСуд\s+округа\b |
        \bСуд\s+кассационной\s+инстанции\b
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


### очистка контекста обособленного спора
def _clean_dispute_context(text: Any) -> str:
    value = _norm(text)
    value = _strip_leading_court_marker(value)

    value = re.sub(r"\(далее\s*[-–]\s*[^)]*\)", " ", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\((?=[^)]*(?:адрес|ИНН|ОГРН|ОГРНИП|КПП|далее|место нахождения))[^)]*\)",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bадрес\s*:\s*[^.;]+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:ИНН|ОГРН|ОГРНИП|КПП)\s*[:№]?\s*\d+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    return value.strip(" ,.;:-")


### обрезка текста после исходного заявления
def _cut_after_application(text: str) -> str:
    text = _clean_dispute_context(text)
    text = _LOWER_COURT_STOP_RE.split(text, maxsplit=1)[0]
    text = re.split(
        r"(?i)\b(?:В\s+обоснование\s+(?:кассационной\s+)?жалоб[ыи]|Заявитель\s+указывает|По\s+мнению\s+заявител[яь]|Кассационная\s+жалоба\s+мотивирована)\b",
        text,
        maxsplit=1,
    )[0]
    return _clean_dispute_context(text)


### проверка наличия предмета спора
def _has_dispute_subject(text: str) -> bool:
    s = _norm(text).lower()
    return any([
        "реестр требований кредиторов" in s,
        "реестр кредиторов" in s,
        ("включени" in s or "включить" in s or "включено" in s) and "требован" in s,
        ("установлени" in s or "установить" in s) and "требован" in s,
        "процессуальн" in s and "правопреемств" in s,
        "замене кредитора" in s,
        "заменить кредитора" in s,
        "замена кредитора" in s,
        "недействительн" in s and ("сдел" in s or "договор" in s or "операц" in s or "платеж" in s),
        "оспаривани" in s,
        "очередност" in s and "требован" in s,
        "субординац" in s,
        "о взыскании" in s and ("денежн" in s or "задолжен" in s),
        "признании должника несостоятельным" in s,
        "признании" in s and "банкрот" in s and "включени" in s,
        "прекращени" in s and "производств" in s and "банкротств" in s,
        "погашени" in s and "требован" in s and "кредитор" in s,
    ])


### проверка спора только о введении банкротства
def _is_bankruptcy_initiation_only(text: str) -> bool:
    s = _norm(text).lower()
    return (
        "о признании" in s
        and "банкрот" in s
        and "реестр" not in s
        and "требован" not in s
        and "очередност" not in s
        and "субординац" not in s
        and "правопреемств" not in s
        and "недействительн" not in s
        and "оспарив" not in s
    )


### отсев неподходящего кандидата предмета спора
def _is_forbidden_dispute_candidate(text: str) -> bool:
    s = _norm(text)

    if re.search(r"(?i)^\s*(?:Определением|Постановлением|Решением)\b", s):
        return True

    if re.search(
        r"(?i)\b(?:"
        r"суд\s+первой\s+инстанции|"
        r"апелляционн\w+\s+суд|"
        r"суд\s+апелляционной\s+инстанции|"
        r"нижестоящ\w+\s+суд|"
        r"оставлен\w+\s+без\s+изменения|"
        r"заявление\s+[^.;]{0,160}?удовлетворено|"
        r"в\s+удовлетворении\s+[^.;]{0,180}?отказано|"
        r"производство\s+по\s+[^.;]{0,180}?прекращено|"
        r"требовани\w+\s+[^.;]{0,180}?признан\w+\s+обоснованн\w+|"
        r"включен\w+\s+в\s+[^.;]{0,80}?реестр"
        r")\b",
        s,
    ):
        if not re.search(r"(?i)\b(?:обратил\w*|обрати\w*|поступил\w*|просил\w*\s+включить|просит\s+включить)\b", s):
            return True

    if re.search(
        r"(?i)\b(?:"
        r"кассационн\w+\s+жалоб|"
        r"апелляционн\w+\s+жалоб|"
        r"не\s+согласившись|"
        r"подател[ьи]\s+жалоб|"
        r"заявител[ьи]\s+жалоб|"
        r"по\s+мнению|"
        r"указывает\s+на|"
        r"ссылает\w+\s+на|"
        r"довод\w+\s+жалоб"
        r")\b",
        s,
    ):
        if not re.search(r"(?i)\b(?:обратил\w*|обрати\w*|поступил\w*|просил\w*\s+включить|просит\s+включить)\b", s):
            return True

    return False


### проверка признаков исходного заявления
def _looks_like_application(text: str) -> bool:
    s = _norm(text).lower()

    if _is_forbidden_dispute_candidate(text):
        return False

    if re.search(r"(?i)\b(?:Определением|Постановлением|Решением)\b", text):
        return False

    if re.search(r"(?i)\b(?:заявление\s+[^.;]{0,120}?удовлетворено|в\s+удовлетворении\s+[^.;]{0,120}?отказано|производство\s+по\s+[^.;]{0,120}?прекращено|требовани\w+\s+[^.;]{0,160}?признан\w+\s+обоснованн\w+)\b", text):
        if not re.search(r"(?i)(?:обратил\w*|обрати\w*|поступил\w*|просил\w*\s+включить)", text):
            return False

    if re.search(r"(?i)\bпо\s+заявлению\s+в\s+указанной\s+части\b", text):
        return False

    has_marker = any([
        "по заявлен" in s,
        "с заявлен" in s,
        "с ходатайств" in s,
        "поступило заявлен" in s,
        "поступило требован" in s,
        "поступило ходатайство" in s,
        "обратил" in s,
        "обрати" in s,
        "просило включить" in s,
        "просил включить" in s,
        "просила включить" in s,
        "просит включить" in s,
        "просили признать" in s,
        "по спору о" in s,
        "о включении" in s and "требован" in s,
    ])
    return has_marker and _has_dispute_subject(text) and not _is_bankruptcy_initiation_only(text)


### оценка кандидата предмета спора
def _score_dispute_candidate(text: str, source: str) -> int:
    s = _norm(text).lower()
    score = 0
    if source == "header":
        score += 10
    elif source == "procedural_history":
        score += 6
    elif source == "party_position":
        score += 2
    elif source == "court_reasoning_head":
        score -= 8
    if "по заявлен" in s:
        score += 10
    if "по спору о" in s:
        score += 9
    if "обратил" in s or "обрати" in s or "поступило заявлен" in s:
        score += 8
    if "реестр требований кредиторов" in s or "реестр кредиторов" in s:
        score += 8
    if "процессуальн" in s and "правопреемств" in s:
        score += 8
    if "недействительн" in s:
        score += 8
    if "очередност" in s or "субординац" in s:
        score += 8
    if re.search(r"\d[\d\s]*[,.]?\d*\s*(?:руб|рублей)", s):
        score += 2
    if re.search(r"(?i)\b(?:Определением|Постановлением|Решением)\b", text):
        score -= 12
    if re.match(r"(?i)^\s*в\s+лице\b", text):
        score -= 6
    if re.match(rf"(?i)^\s*(?:{ORG_PREFIX}|[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.?\s*[А-ЯЁ]\.?)", text):
        score += 4
    if len(text) > 900:
        score -= 4
    if len(text) > 1400:
        score -= 10
    return score


### извлечение кандидатов предмета спора из шапки
def _extract_header_dispute_candidates(header_text: str) -> list[str]:
    text = _norm(header_text)
    candidates: list[str] = []

    patterns = [
        r"(по\s+заявлени\w+\s+(?!отказано|удовлетворено)[\s\S]{0,1600}?)(?=(?:\s+в\s+рамках\s+дела\s+о|\s+в\s+деле\s+о|\s+по\s+делу\s+о|\s+УСТАНОВИЛ|\s+у\s*с\s*т\s*а\s*н|\s+С\s*у\s*д\s*у|\s+В\s+судебном\s+заседании|\s+В\s+помещении|$))",
        r"(по\s+спору\s+о[\s\S]{0,1200}?)(?=(?:\s+в\s+рамках\s+дела\s+о|\s+по\s+делу\s+о|\s+УСТАНОВИЛ|\s+у\s*с\s*т\s*а\s*н|$))",
        r"(о\s+включении[\s\S]{0,900}?требовани[\s\S]{0,900}?)(?=(?:\s+в\s+рамках\s+дела\s+о|\s+по\s+делу\s+о|\s+УСТАНОВИЛ|\s+у\s*с\s*т\s*а\s*н|$))",
        r"(по\s+иску\s+[\s\S]{0,1000}?\s+о\s+[\s\S]{0,500}?)(?=(?:\s+УСТАНОВИЛ|\s+у\s*с\s*т\s*а\s*н|\s+С\s*у\s*д\s*у|\s+В\s+судебном\s+заседании|\s+В\s+помещении|$))",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            candidate = _cut_after_application(match.group(1))
            if _looks_like_application(candidate):
                candidates.append(candidate)

    return _unique(candidates)


### извлечение кандидатов заявления из текста
def _extract_application_candidates_from_text(text: str, source: str) -> list[tuple[str, str]]:
    text = _norm(text)
    # сбор кандидатов из приоритетных источников
    candidates: list[tuple[str, str]] = []

    patterns = [
        r"((?:(?:%s)\s+[^.!?]{0,260}|[А-ЯЁA-Z][^.!?]{0,220})\s+\d{1,2}\.\d{1,2}\.\d{4}\s+(?:обратил\w*|обрати\w*)[\s\S]{0,700}?с\s+заявлени\w+[\s\S]{0,900})" % ORG_PREFIX,
        r"((?:В\s+рамках\s+дела|В\s+деле|В\s+настоящем\s+деле)[\s\S]{0,900}?(?:обратил\w*|обрати\w*)[\s\S]{0,900}?с\s+заявлени\w+[\s\S]{0,900})",
        r"((?:[А-ЯЁA-Z][^.!?]{0,500}?)(?:обратил\w*|обрати\w*)[\s\S]{0,700}?с\s+заявлени\w+[\s\S]{0,900})",
        r"((?:В\s+арбитражн\w+\s+суд[\s\S]{0,300}?)?поступил\w*\s+(?:заявлени\w+|требовани\w+|ходатайств\w+)[\s\S]{0,1200})",
        r"([А-ЯЁA-Z][^.!?]{0,500}?(?:обратил\w*|обрати\w*)[\s\S]{0,500}?с\s+ходатайств\w+[\s\S]{0,900})",
        r"([А-ЯЁA-Z][^.!?]{0,500}?просил\w*\s+включить[\s\S]{0,900})",
        r"((?:требовани\w+|заявлени\w+)[\s\S]{0,300}?о\s+включении[\s\S]{0,900})",
        r"(по\s+заявлени\w+\s+(?!отказано|удовлетворено)[\s\S]{0,1200})",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            candidate = _cut_after_application(match.group(1))
            if _looks_like_application(candidate):
                candidates.append((candidate, source))

    for sentence in _split_sentences_safe(text):
        sentence = _cut_after_application(sentence)
        if _looks_like_application(sentence):
            candidates.append((sentence, source))

    return [(c, source) for c in _unique([c for c, _ in candidates])]


### выделение возможного заявления из позиции стороны
def _cut_party_position_to_possible_application(text: str) -> str:
    text = _norm(text)

    cut = re.split(
        r"(?i)\\b(?:В\\s+кассационн(?:ой|ых)\\s+жалоб|Не\\s+согласившись|Кассационн(?:ая|ые)\\s+жалоб|Податель\\s+жалобы|По\\s+мнению\\s+подателя\\s+жалобы)\\b",
        text,
        maxsplit=1,
    )[0]

    return cut.strip()


### извлечение контекста обособленного спора
def extract_dispute_context(
    header_text: str = "",
    procedural_history_text: str = "",
    party_position_text: str = "",
    court_reasoning_text: str = "",
    limit: int = DISPUTE_CONTEXT_LIMIT,
) -> str:
    header_text = _norm(header_text)
    procedural_history_text = _norm(procedural_history_text)
    party_position_text = _norm(party_position_text)
    court_reasoning_text = _norm(court_reasoning_text)[:COURT_REASONING_HEAD_LIMIT]

    candidates: list[tuple[str, str]] = []
    candidates.extend((candidate, "header") for candidate in _extract_header_dispute_candidates(header_text))
    candidates.extend(_extract_application_candidates_from_text(procedural_history_text, "procedural_history"))
    party_position_application_head = _cut_party_position_to_possible_application(party_position_text)
    candidates.extend(_extract_application_candidates_from_text(party_position_application_head, "party_position"))
    candidates.extend(_extract_application_candidates_from_text(court_reasoning_text, "court_reasoning_head"))

    candidates = [
        (candidate, source)
        for candidate, source in candidates
        if not _is_forbidden_dispute_candidate(candidate)
    ]

    # отсутствие подходящих кандидатов
    if not candidates:
        return None

    source_priority = ["header", "procedural_history", "party_position", "court_reasoning_head"]

    # выбор кандидата по приоритету источника
    selected = None
    for source_name in source_priority:
        for candidate, source in candidates:
            if source == source_name:
                selected = candidate
                break
        if selected:
            break

    result = _clean_dispute_context(selected)

    # ограничение длины результата
    if len(result) > limit:
        result = result[:limit].rsplit(" ", 1)[0].strip(" ,.;:-") + "..."

    return result or None


### извлечение последней организации из фрагмента
def _extract_last_org_from_fragment(fragment: str) -> str:
    fragment = _strip_technical_parentheses(_norm(fragment))

    candidates: list[str] = []

    for m in re.finditer(
        rf"((?:{ORG_PREFIX}|{SHORT_ORG_PREFIX})\s+\".+?\")(?=\s*(?:,|\(|;|$|несостоятельн|банкрот|признан|введен|открыт))",
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        candidates.append(m.group(1))

    if not candidates:
        return None

    return _cut_org_tail(_normalize_org_case(candidates[-1])) or None


### извлечение должника по статусной банкротной формуле
def _extract_bankruptcy_status_debtor(text: str) -> str:
    text = _norm(text)

    for sentence in _split_sentences_safe(text):
        if not re.search(r"(?i)\bпризнан\w*\s+несостоятельн\w*\s*\(\s*банкрот\w*\s*\)", sentence):
            continue

        before_status = re.split(
            r"(?i)\bпризнан\w*\s+несостоятельн\w*\s*\(\s*банкрот\w*\s*\)",
            sentence,
            maxsplit=1,
        )[0]

        org = _extract_last_org_from_fragment(before_status)
        if org:
            return org

    after_patterns = [
        r"производств\w+\s+по\s+дел\w+\s+о\s+(?:несостоятельност\w*\s*\(\s*банкротств\w*\s*\)|банкротств\w*)\s+(.{0,650})",
        r"дел[аеу]?\s+о\s+(?:несостоятельност\w*\s*\(\s*банкротств\w*\s*\)|банкротств\w*)\s+(.{0,650})",
    ]

    for pattern in after_patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            org = _extract_org_from_fragment(m.group(1))
            if org:
                return org

    return None

_PREVIOUS_FINAL_extract_debtor_name = extract_debtor_name


### извлечение наименования должника
def extract_debtor_name(text: str) -> str:
    text = _norm(text)

    org = _PREVIOUS_FINAL_extract_debtor_name(text)
    if org:
        return org

    return _extract_bankruptcy_status_debtor(text)


_PREVIOUS_FINAL_extract_dispute_context = extract_dispute_context


### извлечение контекста обособленного спора
def extract_dispute_context(
    header_text: str = "",
    procedural_history_text: str = "",
    party_position_text: str = "",
    court_reasoning_text: str = "",
    limit: int = DISPUTE_CONTEXT_LIMIT,
) -> str:
    old = _PREVIOUS_FINAL_extract_dispute_context(
        header_text=header_text,
        procedural_history_text=procedural_history_text,
        party_position_text=party_position_text,
        court_reasoning_text=court_reasoning_text,
        limit=limit,
    )
    if old:
        return old

    combined = _norm(header_text + " " + procedural_history_text)
    m = re.search(
        r"(Иск\s+заявлен\s+[^.!?]{0,900}?\s+о\s+взыскании\s+[^.!?]{0,500})",
        combined,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None

    result = _clean_dispute_context(m.group(1))
    if len(result) > limit:
        result = result[:limit].rsplit(" ", 1)[0].strip(" ,.;:-") + "..."
    return result or None


# ПУБЛИЧНЫЙ API

### извлечение метаданных шапки
def extract_header_metadata(header_text: str) -> HeaderMetadata:
    header_text = _norm(header_text)
    return HeaderMetadata(
        case_number=extract_case_number(header_text),
        act_date=extract_act_date(header_text),
        court_name=extract_court_name(header_text),
        judges=extract_judges(header_text),
        debtor_name=extract_debtor_name(header_text),
    )


### извлечение контекста дела
def extract_case_context(
    header_text: str,
    procedural_history_text: str = "",
    party_position_text: str = "",
    court_reasoning_text: str = "",
    procedural_history_limit: int = PROCEDURAL_HISTORY_CONTEXT_LIMIT,
    party_position_limit: int = PARTY_POSITION_CONTEXT_LIMIT,
) -> CaseContext:
    header_text = _norm(header_text)
    procedural_history_text = _norm(procedural_history_text)[:procedural_history_limit]
    party_position_text = _norm(party_position_text)[:party_position_limit]
    court_reasoning_text = _norm(court_reasoning_text)[:COURT_REASONING_HEAD_LIMIT]

    debtor_context = _norm(header_text + " " + procedural_history_text + " " + court_reasoning_text)

    return CaseContext(
        debtor_name=extract_debtor_name(debtor_context),
        dispute_context=extract_dispute_context(
            header_text=header_text,
            procedural_history_text=procedural_history_text,
            party_position_text=party_position_text,
            court_reasoning_text=court_reasoning_text,
        ),
    )


### получение текста зоны
def _get_zone_text(zones: list[dict[str, Any]], zone_type: str) -> str:
    zone = next((zone for zone in zones if zone.get("zone_type") == zone_type), None)
    if not zone:
        return ""
    return str(zone.get("text", "") or "")


### извлечение метаданных шапки из зон
def extract_header_metadata_from_zones(zones: list[dict[str, Any]]) -> HeaderMetadata:
    header_text = _get_zone_text(zones, "header")
    if not header_text:
        raise ValueError("Header-зона не найдена.")
    return extract_header_metadata(header_text)


### извлечение контекста дела из зон
def extract_case_context_from_zones(
    zones: list[dict[str, Any]],
    procedural_history_limit: int = PROCEDURAL_HISTORY_CONTEXT_LIMIT,
    party_position_limit: int = PARTY_POSITION_CONTEXT_LIMIT,
) -> CaseContext:
    header_text = _get_zone_text(zones, "header")
    if not header_text:
        raise ValueError("Header-зона не найдена.")

    procedural_history_text = _get_zone_text(zones, "procedural_history")
    party_position_text = _get_zone_text(zones, "party_position")
    court_reasoning_text = _get_zone_text(zones, "court_reasoning")

    return extract_case_context(
        header_text=header_text,
        procedural_history_text=procedural_history_text,
        party_position_text=party_position_text,
        court_reasoning_text=court_reasoning_text,
        procedural_history_limit=procedural_history_limit,
        party_position_limit=party_position_limit,
    )


### печать метаданных шапки
def print_header_metadata(metadata: HeaderMetadata) -> None:
    print(f"Номер дела: {metadata.case_number}")
    print(f"Дата акта: {metadata.act_date}")
    print(f"Суд: {metadata.court_name}")
    print(f"Судьи: {'; '.join(metadata.judges) if metadata.judges else None}")


### печать контекста дела
def print_case_context(context: CaseContext) -> None:
    print(f"Должник: {context.debtor_name}")
    print(f"Обособленный спор: {context.dispute_context}")


# ОТЛАДКА

### ручной запуск проверки
def main() -> None:
    from pdfParser import extract_pdf_pages
    from structureSplitter import split_pages_into_zones

    case_number = "А03-20799-2023__20250123"
    pdf_path = Path("data/cassation_docs/кассация") / f"{case_number}.pdf"

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF не найден: {pdf_path.resolve()}")

    # запуск полного контура извлечения
    parsed = extract_pdf_pages(str(pdf_path))
    zones = split_pages_into_zones(parsed)

    metadata = extract_header_metadata_from_zones(zones)
    context = extract_case_context_from_zones(zones)

    print_header_metadata(metadata)
    print_case_context(context)


if __name__ == "__main__":
    main()
