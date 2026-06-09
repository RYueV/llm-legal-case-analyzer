import copy
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Sequence

# КОНСТАНТЫ

ANNOTATION_SECTIONS = [
    "metadata",
    "procedure",
    "economic_dispute",
    "creditor_profile",
    "claim",
    "crisis",
    "behavior",
    "market_terms",
    "proof",
    "proof_burden",
    "qualification",
    "motivation",
]

ALLOWED_ECONOMIC_DISPUTE_BASKETS = {
    "compensation_financing_loan",
    "ordinary_affiliated_business_deal",
    "sham_or_fictitious_debt",
    "covering_relationship",
    "cession_or_purchase_of_claim",
    "subrogation_guarantee_or_security",
    "restitution_after_avoidance",
    "contractual_subordination",
    "refusal_to_subordinate_despite_affiliation",
    "procedural_issue",
    "mixed",
    "other",
    "unknown",
}

UNKNOWN_MARKERS = {
    "",
    "unknown",
    "none",
    "null",
    "не установлено",
    "нет данных",
    "—",
    "-",
}

VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}
VALID_BINARY_VALUES = {"yes", "no", "unknown"}

# поля без которых сравнение и итоговая интерпретация становятся ненадежными
ABSOLUTE_KEY_FIELDS = [
    "annotation.economic_dispute.economic_dispute_basket",
    "annotation.claim.claim_type",
    "annotation.claim.claim_origin",
    "annotation.creditor_profile.creditor_status",
    "annotation.creditor_profile.formal_affiliation_found",
    "annotation.creditor_profile.factual_affiliation_found",
    "annotation.creditor_profile.control_found",
    "annotation.crisis.crisis_status",
    "annotation.proof.performance_fact_proven",
    "annotation.proof.court_recognized_debt_real",
    "annotation.proof.court_doubted_reality",
    "annotation.proof.court_found_sham_or_artificial",
    "annotation.qualification.legal_qualification",
    "annotation.qualification.key_reason_category",
]

# важные поля для желательного дозаполнения
SOFT_KEY_FIELDS = [
    "annotation.claim.emergence_timing",
    "annotation.creditor_profile.connection_basis_primary",
    "annotation.crisis.creditor_knew_crisis",
    "annotation.crisis.creditor_should_have_known_crisis",
    "annotation.behavior.new_financing_provided",
    "annotation.behavior.left_funds_in_debtor",
    "annotation.behavior.did_not_demand_repayment",
    "annotation.behavior.changed_contract_terms",
    "annotation.behavior.extended_maturity",
    "annotation.behavior.obtained_security",
    "annotation.behavior.obtained_preference",
    "annotation.behavior.participated_in_management",
    "annotation.behavior.acted_as_market_creditor",
    "annotation.market_terms.economic_sense_present",
    "annotation.market_terms.market_conditions_overall",
    "annotation.proof_burden.heightened_standard_applied",
    "annotation.proof_burden.burden_to_remove_doubts_on_creditor",
    "annotation.proof_burden.creditor_rebutted_doubts",
]

# контекстные поля с допустимой неизвестностью
CONTEXTUAL_FIELDS = [
    "annotation.market_terms.interest_terms",
    "annotation.market_terms.maturity_terms",
    "annotation.market_terms.security_terms",
    "annotation.proof.documents_contradictory",
    "annotation.proof.primary_docs_absent",
    "annotation.proof_burden.presumption_comp_financing_applied",
]

BINARY_FIELD_SUFFIXES = {
    "formal_affiliation_found",
    "factual_affiliation_found",
    "control_found",
    "creditor_knew_crisis",
    "creditor_should_have_known_crisis",
    "new_financing_provided",
    "left_funds_in_debtor",
    "did_not_demand_repayment",
    "changed_contract_terms",
    "extended_maturity",
    "obtained_security",
    "obtained_preference",
    "participated_in_management",
    "acted_as_market_creditor",
    "economic_sense_present",
    "performance_fact_proven",
    "documents_present",
    "documents_contradictory",
    "primary_docs_absent",
    "court_recognized_debt_real",
    "court_doubted_reality",
    "heightened_standard_applied",
    "burden_to_remove_doubts_on_creditor",
    "presumption_comp_financing_applied",
    "motivation_facts_detailed",
    "motivation_affiliation_only",
    "motivation_economic_sense",
    "motivation_crisis",
    "motivation_independent_creditors",
    "motivation_market_terms",
    "motivation_good_faith",
    "motivation_formalistic",
    "motivation_contradictory",
}

# НОРМАЛИЗАЦИЯ И БАЗОВЫЕ ПРОВЕРКИ

### преобразование dataclass в обычные структуры данных
def to_plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): to_plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value

### нормализация текста без изменения смысла
def norm_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\xa0", " ").replace("\u202f", " ")
    text = text.replace("«", '"').replace("»", '"')
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

### нормализация значения для сравнения
def norm_key(value: Any) -> str:
    return norm_text(value).lower().replace("ё", "е")

### проверка значения на неизвестность
def is_unknown_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return norm_key(value) in UNKNOWN_MARKERS
    if isinstance(value, list):
        return len(value) == 0
    return False

### проверка структуры признака
def looks_like_feature(obj: Any) -> bool:
    return isinstance(obj, dict) and any(
        key in obj for key in ("value", "evidence_quote", "evidence_source", "confidence")
    )

### получение значения признака
def get_feature_value(feature: Any) -> Any:
    if looks_like_feature(feature):
        return feature.get("value")
    return feature

### получение семантического ключа значения
def semantic_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return norm_key(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return norm_key(value)

### создание признака unknown
def make_unknown_feature() -> dict[str, Any]:
    return {
        "value": "unknown",
        "evidence_quote": "",
        "evidence_source": "none",
        "confidence": "low",
    }

### создание производного признака
def make_derived_feature(
    *,
    value: str,
    reason: str,
    confidence: str = "medium",
    matched_rules = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "evidence_quote": f"derived from accepted schema fields: {reason}",
        "evidence_source": "derived: extractionAuditor",
        "confidence": confidence if confidence in VALID_CONFIDENCE_VALUES else "medium",
        "derivation": {
            "reason": reason,
            "matched_rules": matched_rules or [],
        },
    }

### проверка технического источника
def is_technical_source(source: Any) -> bool:
    source_text = norm_key(source)
    return (
        source_text.startswith("zone=header")
        or source_text.startswith("derived:")
        or "extractionauditor" in source_text
    )

### проверка наличия допустимого доказательства
def has_valid_evidence(feature: Any) -> bool:
    if not looks_like_feature(feature):
        return not is_unknown_value(feature)

    value = feature.get("value")
    if is_unknown_value(value):
        return True

    quote = norm_text(feature.get("evidence_quote"))
    source = norm_text(feature.get("evidence_source"))

    if not source or norm_key(source) == "none":
        return False

    if not quote and not is_technical_source(source):
        return False

    return True

### проверка принятого признака
def is_accepted_feature(feature: Any) -> bool:
    if not looks_like_feature(feature):
        return not is_unknown_value(feature)
    return not is_unknown_value(feature.get("value")) and has_valid_evidence(feature)

### проверка бинарного поля по пути
def is_binary_path(path: str) -> bool:
    return path.split(".")[-1] in BINARY_FIELD_SUFFIXES

### нормализация бинарного признака
def normalize_binary_feature(feature: Any) -> Any:
    if not looks_like_feature(feature):
        return feature

    value = feature.get("value")
    if isinstance(value, bool):
        fixed = copy.deepcopy(feature)
        fixed["value"] = "yes" if value else "no"
        fixed.setdefault("audit_note", "boolean_normalized_to_yes_no")
        return fixed

    value_norm = norm_key(value)
    if value_norm in {"true", "да", "имеется", "установлено", "присутствует"}:
        fixed = copy.deepcopy(feature)
        fixed["value"] = "yes"
        fixed.setdefault("audit_note", "string_normalized_to_yes")
        return fixed
    if value_norm in {"false", "нет", "отсутствует", "не установлено", "не доказано"}:
        fixed = copy.deepcopy(feature)
        fixed["value"] = "no"
        fixed.setdefault("audit_note", "string_normalized_to_no")
        return fixed

    return feature

### проверка конфликта значений
def values_conflict(left: Any, right: Any) -> bool:
    left_value = get_feature_value(left)
    right_value = get_feature_value(right)

    if is_unknown_value(left_value) or is_unknown_value(right_value):
        return False

    left_norm = semantic_value(left_value)
    right_norm = semantic_value(right_value)

    if left_norm == right_norm:
        return False

    # отличие длинных текстовых полей по детализации а не по смыслу
    if len(left_norm) > 40 or len(right_norm) > 40:
        return False

    return True

### выбор более полного заполненного признака без скоринга
def prefer_filled_with_evidence(left: Any, right: Any, *, path: str) -> Any:
    left = normalize_binary_feature(left) if is_binary_path(path) else left
    right = normalize_binary_feature(right) if is_binary_path(path) else right

    left_accepted = is_accepted_feature(left)
    right_accepted = is_accepted_feature(right)

    if right_accepted and not left_accepted:
        return copy.deepcopy(right)
    if left_accepted and not right_accepted:
        return copy.deepcopy(left)

    left_value_unknown = is_unknown_value(get_feature_value(left))
    right_value_unknown = is_unknown_value(get_feature_value(right))

    if right_value_unknown and not left_value_unknown:
        return copy.deepcopy(left)
    if left_value_unknown and not right_value_unknown:
        return copy.deepcopy(right)

    # выбор варианта с цитатой при одинаковых значениях
    if looks_like_feature(left) and looks_like_feature(right):
        if semantic_value(left.get("value")) == semantic_value(right.get("value")):
            left_quote = norm_text(left.get("evidence_quote"))
            right_quote = norm_text(right.get("evidence_quote"))
            if right_quote and not left_quote:
                return copy.deepcopy(right)
            return copy.deepcopy(left)

    return copy.deepcopy(left)

# РАЗБОР РЕЗУЛЬТАТОВ ЭКСТРАКТОРОВ

### извлечение структурированного ответа
def unwrap_structured_answer(answer: Any):
    answer = to_plain_data(answer)

    if not isinstance(answer, dict):
        return None

    if isinstance(answer.get("annotation"), dict):
        return answer["annotation"]

    result = answer.get("result")
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return unwrap_structured_answer(result[0])
    if isinstance(result, dict):
        return unwrap_structured_answer(result)

    return answer

### перебор непустых блоков экстракторов
def iter_partial_blocks(schema_extractions: Sequence[dict[str, Any]]) -> Iterable:
    for item in schema_extractions or []:
        if item.get("skipped") or item.get("error") or item.get("parse_error"):
            continue

        answer = unwrap_structured_answer(item.get("structured_answer"))
        if not isinstance(answer, dict):
            continue

        extractor_name = str(item.get("extraction_type") or item.get("card_name") or "unknown_extractor")
        yield extractor_name, answer

# ОБЪЕДИНЕНИЕ БЕЗ СКОРИНГА

### фиксация отклоненного признака
def record_rejected_feature(
    rejected_fields: list[dict[str, Any]],
    *,
    path: str,
    feature: Any,
    reason: str,
) -> None:
    rejected_fields.append(
        {
            "path": path,
            "value": get_feature_value(feature),
            "reason": reason,
            "evidence_quote": feature.get("evidence_quote") if looks_like_feature(feature) else "",
            "evidence_source": feature.get("evidence_source") if looks_like_feature(feature) else "none",
        }
    )

### объединение признаков
def merge_features(
    left: Any,
    right: Any,
    *,
    path: str,
    field_conflicts: list[dict[str, Any]],
    rejected_fields: list[dict[str, Any]],
) -> Any:
    if left is None:
        candidate = normalize_binary_feature(right) if is_binary_path(path) else right
        if looks_like_feature(candidate) and not is_unknown_value(candidate.get("value")) and not has_valid_evidence(candidate):
            record_rejected_feature(rejected_fields, path=path, feature=candidate, reason="filled_value_without_required_evidence")
            return make_unknown_feature()
        return copy.deepcopy(candidate)

    if right is None:
        return copy.deepcopy(left)

    left_norm = normalize_binary_feature(left) if is_binary_path(path) else left
    right_norm = normalize_binary_feature(right) if is_binary_path(path) else right

    left_invalid_filled = looks_like_feature(left_norm) and not is_unknown_value(left_norm.get("value")) and not has_valid_evidence(left_norm)
    right_invalid_filled = looks_like_feature(right_norm) and not is_unknown_value(right_norm.get("value")) and not has_valid_evidence(right_norm)

    if left_invalid_filled:
        record_rejected_feature(rejected_fields, path=path, feature=left_norm, reason="filled_value_without_required_evidence")
    if right_invalid_filled:
        record_rejected_feature(rejected_fields, path=path, feature=right_norm, reason="filled_value_without_required_evidence")

    if left_invalid_filled and not right_invalid_filled:
        return copy.deepcopy(right_norm)
    if right_invalid_filled and not left_invalid_filled:
        return copy.deepcopy(left_norm)
    if left_invalid_filled and right_invalid_filled:
        return make_unknown_feature()

    if values_conflict(left_norm, right_norm):
        field_conflicts.append(
            {
                "path": path,
                "left_value": get_feature_value(left_norm),
                "right_value": get_feature_value(right_norm),
                "left_source": left_norm.get("evidence_source") if looks_like_feature(left_norm) else "none",
                "right_source": right_norm.get("evidence_source") if looks_like_feature(right_norm) else "none",
                "reason": "conflicting_accepted_values",
            }
        )

        result = prefer_filled_with_evidence(left_norm, right_norm, path=path)
        if looks_like_feature(result):
            result = copy.deepcopy(result)
            result["audit_warning"] = "conflict_detected_manual_review_required"
        return result

    return prefer_filled_with_evidence(left_norm, right_norm, path=path)

### объединение значений
def merge_values(
    left: Any,
    right: Any,
    *,
    path: str,
    field_conflicts: list[dict[str, Any]],
    rejected_fields: list[dict[str, Any]],
) -> Any:
    if left is None:
        if looks_like_feature(right):
            return merge_features(None, right, path=path, field_conflicts=field_conflicts, rejected_fields=rejected_fields)
        return copy.deepcopy(right)

    if right is None:
        return copy.deepcopy(left)

    if looks_like_feature(left) or looks_like_feature(right):
        return merge_features(
            left,
            right,
            path=path,
            field_conflicts=field_conflicts,
            rejected_fields=rejected_fields,
        )

    if isinstance(left, dict) and isinstance(right, dict):
        result = copy.deepcopy(left)
        for key, right_value in right.items():
            child_path = f"{path}.{key}" if path else str(key)
            result[key] = merge_values(
                result.get(key),
                right_value,
                path=child_path,
                field_conflicts=field_conflicts,
                rejected_fields=rejected_fields,
            )
        return result

    if isinstance(left, list) and isinstance(right, list):
        result = copy.deepcopy(left)
        seen = {semantic_value(item) for item in result}
        for item in right:
            marker = semantic_value(item)
            if marker not in seen:
                seen.add(marker)
                result.append(copy.deepcopy(item))
        return result

    if values_conflict(left, right):
        field_conflicts.append(
            {
                "path": path,
                "left_value": left,
                "right_value": right,
                "reason": "conflicting_plain_values",
            }
        )

    return copy.deepcopy(left if not is_unknown_value(left) else right)

# МЕТАДАННЫЕ И ПРАВОВЫЕ ССЫЛКИ

### построение metadata по шапке акта
def build_metadata_from_header(header: Any, case_context = None) -> dict[str, Any]:
    header_data = to_plain_data(header) or {}
    context_data = to_plain_data(case_context) or {}

    def feature(value: Any, source: str = "zone=header") -> dict[str, Any]:
        if value in (None, [], ""):
            return make_unknown_feature()
        return {
            "value": value,
            "evidence_quote": norm_text(value) if not isinstance(value, list) else "; ".join(map(str, value)),
            "evidence_source": source,
            "confidence": "high",
        }

    result = {
        "case_number": feature(header_data.get("case_number")),
        "act_date": feature(header_data.get("act_date")),
        "court_name": feature(header_data.get("court_name")),
        "judge_name": feature(header_data.get("judges") or []),
        "debtor_name": feature(context_data.get("debtor_name") or header_data.get("debtor_name")),
    }

    dispute_context = context_data.get("dispute_context")
    if dispute_context:
        result["dispute_context"] = feature(dispute_context, source="zone=header/procedural_history")

    return result

### приведение правовых ссылок к плоскому виду
def flatten_legal_references(references_report) -> dict[str, Any]:
    report = to_plain_data(references_report) or {}
    legal_references = report.get("legal_references") or {}

    flat_hits: list[dict[str, Any]] = []
    for category, hits in legal_references.items():
        for hit in hits or []:
            item = dict(hit)
            item.setdefault("category", category)
            flat_hits.append(item)

    return {
        "hits_count": report.get("hits_count", len(flat_hits)),
        "blacklisted_sentence_ids": report.get("blacklisted_sentence_ids", []),
        "blacklisted_count": report.get("blacklisted_count", len(report.get("blacklisted_sentence_ids", []))),
        "by_category": legal_references,
        "flat_hits": flat_hits,
    }

# РАБОТА С ПУТЯМИ И ПРИНЯТЫМИ ЗНАЧЕНИЯМИ

### получение значения по пути
def get_by_path(root: dict[str, Any], path: str):
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current

### установка значения по пути
def set_by_path(root: dict[str, Any], path: str, value: Any) -> None:
    current = root
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value

### получение значения признака по пути
def feature_value_at(annotation: dict[str, Any], path: str) -> Any:
    feature = get_by_path({"annotation": annotation}, f"annotation.{path}" if not path.startswith("annotation.") else path)
    return get_feature_value(feature)

### получение принятого значения по пути
def accepted_value_at(annotation: dict[str, Any], path: str) -> str:
    feature = get_by_path({"annotation": annotation}, f"annotation.{path}" if not path.startswith("annotation.") else path)
    if not is_accepted_feature(feature):
        return "unknown"
    return semantic_value(get_feature_value(feature))

### проверка значения yes
def is_yes(annotation: dict[str, Any], path: str) -> bool:
    return accepted_value_at(annotation, path) == "yes"

### проверка значения no
def is_no(annotation: dict[str, Any], path: str) -> bool:
    return accepted_value_at(annotation, path) == "no"

### проверка значения в допустимом наборе
def value_in(annotation: dict[str, Any], path: str, values: set[str]) -> bool:
    return accepted_value_at(annotation, path) in values

# ДЕТЕРМИНИРОВАННОЕ ОПРЕДЕЛЕНИЕ ECONOMIC_DISPUTE_BASKET

### определение economic_dispute_basket по иерархии правил
def infer_economic_dispute_basket(annotation: dict[str, Any]):
    matched_rules: list[str] = []

    def match(rule: str) -> None:
        matched_rules.append(rule)

    claim_type = accepted_value_at(annotation, "claim.claim_type")
    claim_origin = accepted_value_at(annotation, "claim.claim_origin")
    dispute_object = accepted_value_at(annotation, "procedure.dispute_object")
    legal_qualification = accepted_value_at(annotation, "qualification.legal_qualification")
    key_reason = accepted_value_at(annotation, "qualification.key_reason_category")
    creditor_status = accepted_value_at(annotation, "creditor_profile.creditor_status")
    crisis_status = accepted_value_at(annotation, "crisis.crisis_status")
    market_conditions = accepted_value_at(annotation, "market_terms.market_conditions_overall")
    court_found_sham = accepted_value_at(annotation, "proof.court_found_sham_or_artificial")

    # преимущественно процессуальный спор
    if dispute_object in {"procedural_succession", "creditor_voting_rights", "manager_action_complaint"}:
        claim_unknown = claim_type == "unknown"
        proof_unknown = accepted_value_at(annotation, "proof.performance_fact_proven") == "unknown"
        if claim_unknown and proof_unknown:
            match(f"procedure.dispute_object={dispute_object}; claim/proof not established")
            return (
                make_derived_feature(
                    value="procedural_issue",
                    reason="спор имеет преимущественно процессуальный предмет, материально-правовой сценарий не установлен",
                    confidence="medium",
                    matched_rules=matched_rules,
                ),
                {"decision": "procedural_issue", "matched_rules": matched_rules},
            )

    # специальные юридические формы требования
    special_matches: list = []
    if claim_type == "cession":
        special_matches.append(("cession_or_purchase_of_claim", "claim.claim_type=cession"))
    if claim_type in {"subrogation", "guarantee", "payment_for_debtor"}:
        special_matches.append(("subrogation_guarantee_or_security", f"claim.claim_type={claim_type}"))
    if claim_type == "restitution":
        special_matches.append(("restitution_after_avoidance", "claim.claim_type=restitution"))

    # приоритет мнимости искусственности или недоказанности долга над формой сделки
    sham_rules: list[str] = []
    if court_found_sham in {"sham", "artificial", "sham_and_artificial"}:
        sham_rules.append(f"proof.court_found_sham_or_artificial={court_found_sham}")
    if is_no(annotation, "proof.court_recognized_debt_real"):
        sham_rules.append("proof.court_recognized_debt_real=no")
    if is_no(annotation, "proof.performance_fact_proven"):
        sham_rules.append("proof.performance_fact_proven=no")
    if key_reason in {"sham_or_artificial_debt", "reality_not_proven"}:
        sham_rules.append(f"qualification.key_reason_category={key_reason}")

    if sham_rules:
        matched_rules.extend(sham_rules)
        return (
            make_derived_feature(
                value="sham_or_fictitious_debt",
                reason="судом установлены или приняты признаки мнимости, искусственности либо недоказанности долга",
                confidence="high",
                matched_rules=matched_rules,
            ),
            {"decision": "sham_or_fictitious_debt", "matched_rules": matched_rules},
        )

    # выбор mixed при нескольких специальных формах без более сильного сценария
    if len({bucket for bucket, _ in special_matches}) > 1:
        for _, rule in special_matches:
            match(rule)
        return (
            make_derived_feature(
                value="mixed",
                reason="одновременно сработали несколько специальных сценариев требования",
                confidence="medium",
                matched_rules=matched_rules,
            ),
            {"decision": "mixed", "matched_rules": matched_rules},
        )

    if special_matches:
        bucket, rule = special_matches[0]
        match(rule)
        return (
            make_derived_feature(
                value=bucket,
                reason=f"сценарий определяется юридической формой требования: {rule}",
                confidence="high",
                matched_rules=matched_rules,
            ),
            {"decision": bucket, "matched_rules": matched_rules},
        )

    # компенсационное финансирование
    compensation_rules: list[str] = []
    if claim_origin == "compensation_financing":
        compensation_rules.append("claim.claim_origin=compensation_financing")
    if legal_qualification == "recognized_as_compensation_financing":
        compensation_rules.append("qualification.legal_qualification=recognized_as_compensation_financing")
    if key_reason == "compensation_financing":
        compensation_rules.append("qualification.key_reason_category=compensation_financing")

    loan_crisis_behavior = (
        claim_type == "loan"
        and crisis_status == "established"
        and creditor_status in {"affiliated", "controlling"}
        and (
            is_yes(annotation, "behavior.new_financing_provided")
            or is_yes(annotation, "behavior.left_funds_in_debtor")
            or is_yes(annotation, "behavior.did_not_demand_repayment")
            or is_yes(annotation, "behavior.extended_maturity")
        )
    )
    if loan_crisis_behavior:
        compensation_rules.append("loan + crisis established + affiliated/controlling creditor + financing/retention behavior")

    if compensation_rules:
        matched_rules.extend(compensation_rules)
        return (
            make_derived_feature(
                value="compensation_financing_loan",
                reason="признаки указывают на заем, финансирование или оставление ресурса в кризис",
                confidence="high" if len(compensation_rules) >= 2 else "medium",
                matched_rules=matched_rules,
            ),
            {"decision": "compensation_financing_loan", "matched_rules": matched_rules},
        )

    # обычная хозяйственная сделка
    if claim_type in {"supply", "lease", "works_or_services"}:
        ordinary_rules: list[str] = [f"claim.claim_type={claim_type}"]
        if is_yes(annotation, "proof.court_recognized_debt_real"):
            ordinary_rules.append("proof.court_recognized_debt_real=yes")
        if is_yes(annotation, "behavior.acted_as_market_creditor"):
            ordinary_rules.append("behavior.acted_as_market_creditor=yes")
        if market_conditions == "market":
            ordinary_rules.append("market_terms.market_conditions_overall=market")
        if len(ordinary_rules) >= 2:
            matched_rules.extend(ordinary_rules)
            return (
                make_derived_feature(
                    value="ordinary_affiliated_business_deal",
                    reason="требование основано на обычной хозяйственной сделке, реальность/рыночность подтверждена принятыми признаками",
                    confidence="high" if len(ordinary_rules) >= 3 else "medium",
                    matched_rules=matched_rules,
                ),
                {"decision": "ordinary_affiliated_business_deal", "matched_rules": matched_rules},
            )

    # аффилированность без применения субординации
    if creditor_status in {"affiliated", "controlling"} and legal_qualification == "included_general":
        if key_reason in {"market_behavior", "no_crisis_at_relevant_moment", "no_affiliation_or_control_proven"}:
            matched_rules.extend(
                [
                    f"creditor_profile.creditor_status={creditor_status}",
                    "qualification.legal_qualification=included_general",
                    f"qualification.key_reason_category={key_reason}",
                ]
            )
            return (
                make_derived_feature(
                    value="refusal_to_subordinate_despite_affiliation",
                    reason="связь кредитора обсуждалась, но требование включено без субординации",
                    confidence="medium",
                    matched_rules=matched_rules,
                ),
                {"decision": "refusal_to_subordinate_despite_affiliation", "matched_rules": matched_rules},
            )

    # договорная субординация корпоративная природа или прикрытие по итоговой причине
    if key_reason == "corporate_nature" or legal_qualification == "corporate_claim":
        match("qualification indicates corporate nature")
        return (
            make_derived_feature(
                value="contractual_subordination",
                reason="итоговая квалификация указывает на корпоративную или субординационную природу требования",
                confidence="medium",
                matched_rules=matched_rules,
            ),
            {"decision": "contractual_subordination", "matched_rules": matched_rules},
        )

    return (
        make_derived_feature(
            value="unknown",
            reason="по принятым признакам схемы экономико-правовой сценарий не установлен",
            confidence="low",
            matched_rules=[],
        ),
        {"decision": "unknown", "matched_rules": []},
    )

# ПРОВЕРКИ АУДИТА

### перебор признаков в структуре
def iter_features(root: Any, prefix: str = "") -> Iterable:
    if looks_like_feature(root):
        yield prefix, root
        return
    if isinstance(root, dict):
        for key, value in root.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_features(value, path)
    elif isinstance(root, list):
        for index, value in enumerate(root):
            path = f"{prefix}[{index}]"
            yield from iter_features(value, path)

### построение списка принятых полей
def build_accepted_fields(final_case: dict[str, Any]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for path, feature in iter_features(final_case.get("annotation", {}), "annotation"):
        if is_accepted_feature(feature):
            accepted.append({"path": path, "value": feature.get("value")})
    return accepted

### построение предупреждений по доказательствам
def build_evidence_warnings(final_case: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for path, feature in iter_features(final_case.get("annotation", {}), "annotation"):
        value = feature.get("value")
        quote = norm_text(feature.get("evidence_quote"))
        source = norm_text(feature.get("evidence_source"))
        confidence = norm_key(feature.get("confidence"))

        if not is_unknown_value(value) and not quote and not is_technical_source(source):
            warnings.append({"path": path, "reason": "filled_value_without_evidence_quote", "value": value})

        if not is_unknown_value(value) and source in {"", "none"}:
            warnings.append({"path": path, "reason": "filled_value_without_evidence_source", "value": value})

        if confidence not in VALID_CONFIDENCE_VALUES:
            warnings.append({"path": path, "reason": "unexpected_confidence_value", "confidence": feature.get("confidence")})

        if is_binary_path(path) and semantic_value(value) not in VALID_BINARY_VALUES:
            warnings.append({"path": path, "reason": "unexpected_binary_value", "value": value})

    return warnings

### поиск незаполненных полей
def missing_fields(final_case: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in paths:
        feature = get_by_path(final_case, path)
        if feature is None:
            result.append({"path": path, "reason": "missing_field", "needed_context": infer_needed_context(path)})
            continue
        if not is_accepted_feature(feature):
            result.append({"path": path, "reason": "unknown_or_unaccepted_field", "needed_context": infer_needed_context(path)})
    return result

### определение нужного контекста для дозаполнения
def infer_needed_context(path: str) -> str:
    if ".claim." in path:
        return "claimAndDealCard / courtAssessmentCard: договор, основание, период и природа требования"
    if ".creditor_profile." in path:
        return "creditorStatusCard / courtAssessmentCard: связь, аффилированность, контроль, статус кредитора"
    if ".crisis." in path:
        return "debtorCrisisCard: кризис, объективное банкротство, знание кредитора"
    if ".behavior." in path:
        return "creditorStatusCard / courtAssessmentCard: поведение кредитора, финансирование, взыскание, обеспечение, управление"
    if ".market_terms." in path:
        return "claimAndDealCard / courtAssessmentCard: проценты, сроки, обеспечение, рыночность"
    if ".proof." in path:
        return "proofAndEvidenceCard: исполнение, документы, реальность долга, мнимость"
    if ".proof_burden." in path:
        return "proofAndEvidenceCard / courtAssessmentCard: стандарт доказывания, бремя снятия сомнений"
    if ".qualification." in path:
        return "courtAssessmentCard: итог суда, причина исхода, резолютивный результат"
    if ".economic_dispute." in path:
        return "accepted claim, proof, creditor_profile, crisis, behavior and qualification fields"
    if ".metadata." in path:
        return "header zone"
    return "nearest source card fragments"

### поиск ошибок парсинга экстракторов
def detect_parse_errors(schema_extractions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in schema_extractions or []:
        if item.get("parse_error") or item.get("error"):
            result.append(
                {
                    "card_name": item.get("card_name"),
                    "extraction_type": item.get("extraction_type"),
                    "error": item.get("parse_error") or item.get("error"),
                }
            )
    return result

### проверка допустимости economic_dispute_basket
def validate_economic_basket(final_case: dict[str, Any], structural_warnings: list[dict[str, Any]]) -> None:
    feature = get_by_path(final_case, "annotation.economic_dispute.economic_dispute_basket")
    if not looks_like_feature(feature):
        structural_warnings.append({"path": "annotation.economic_dispute.economic_dispute_basket", "reason": "missing_feature_object"})
        set_by_path(final_case, "annotation.economic_dispute.economic_dispute_basket", make_unknown_feature())
        return

    value = feature.get("value")
    if value not in ALLOWED_ECONOMIC_DISPUTE_BASKETS:
        structural_warnings.append(
            {
                "path": "annotation.economic_dispute.economic_dispute_basket",
                "value": value,
                "reason": "value_not_in_allowed_baskets",
            }
        )
        feature["value"] = "unknown"
        feature["confidence"] = "low"

### построение логических несоответствий
def build_logical_inconsistencies(final_case: dict[str, Any]) -> list[dict[str, Any]]:
    annotation = final_case.get("annotation") or {}
    issues: list[dict[str, Any]] = []

    def add(paths: list[str], reason: str, severity: str = "warning") -> None:
        issues.append({"paths": paths, "reason": reason, "severity": severity})

    creditor_status = accepted_value_at(annotation, "creditor_profile.creditor_status")
    basis = accepted_value_at(annotation, "creditor_profile.connection_basis_primary")
    crisis_status = accepted_value_at(annotation, "crisis.crisis_status")
    legal_qualification = accepted_value_at(annotation, "qualification.legal_qualification")
    key_reason = accepted_value_at(annotation, "qualification.key_reason_category")
    sham = accepted_value_at(annotation, "proof.court_found_sham_or_artificial")

    if creditor_status == "independent" and (
        is_yes(annotation, "creditor_profile.formal_affiliation_found")
        or is_yes(annotation, "creditor_profile.factual_affiliation_found")
        or is_yes(annotation, "creditor_profile.control_found")
    ):
        add(
            [
                "annotation.creditor_profile.creditor_status",
                "annotation.creditor_profile.formal_affiliation_found",
                "annotation.creditor_profile.factual_affiliation_found",
                "annotation.creditor_profile.control_found",
            ],
            "creditor_status=independent conflicts with established affiliation/control",
            "critical",
        )

    if creditor_status == "controlling" and not is_yes(annotation, "creditor_profile.control_found"):
        add(
            ["annotation.creditor_profile.creditor_status", "annotation.creditor_profile.control_found"],
            "creditor_status=controlling requires control_found=yes",
            "critical",
        )

    if creditor_status == "affiliated" and is_yes(annotation, "creditor_profile.control_found"):
        add(
            ["annotation.creditor_profile.creditor_status", "annotation.creditor_profile.control_found"],
            "creditor_status=affiliated conflicts with control_found=yes; controlling may be required",
            "warning",
        )

    if basis == "none" and (
        is_yes(annotation, "creditor_profile.formal_affiliation_found")
        or is_yes(annotation, "creditor_profile.factual_affiliation_found")
        or is_yes(annotation, "creditor_profile.control_found")
    ):
        add(
            [
                "annotation.creditor_profile.connection_basis_primary",
                "annotation.creditor_profile.formal_affiliation_found",
                "annotation.creditor_profile.factual_affiliation_found",
                "annotation.creditor_profile.control_found",
            ],
            "connection_basis_primary=none conflicts with established affiliation/control",
            "critical",
        )

    if basis in {"equity_participation", "management", "group_of_companies", "family_ties"} and not is_yes(annotation, "creditor_profile.formal_affiliation_found"):
        add(
            ["annotation.creditor_profile.connection_basis_primary", "annotation.creditor_profile.formal_affiliation_found"],
            "formal connection basis usually requires formal_affiliation_found=yes",
            "warning",
        )

    if basis in {"factual_control", "coordinated_actions", "economic_dependence"} and not is_yes(annotation, "creditor_profile.factual_affiliation_found"):
        add(
            ["annotation.creditor_profile.connection_basis_primary", "annotation.creditor_profile.factual_affiliation_found"],
            "factual connection basis usually requires factual_affiliation_found=yes",
            "warning",
        )

    if crisis_status == "not_established" and (
        is_yes(annotation, "crisis.creditor_knew_crisis")
        or is_yes(annotation, "crisis.creditor_should_have_known_crisis")
    ):
        add(
            ["annotation.crisis.crisis_status", "annotation.crisis.creditor_knew_crisis", "annotation.crisis.creditor_should_have_known_crisis"],
            "crisis_status=not_established is inconsistent with creditor knowledge of crisis",
            "warning",
        )

    if is_yes(annotation, "behavior.acted_as_market_creditor") and is_yes(annotation, "behavior.obtained_preference"):
        add(
            ["annotation.behavior.acted_as_market_creditor", "annotation.behavior.obtained_preference"],
            "market behavior and obtained preference may conflict",
            "warning",
        )

    if is_yes(annotation, "behavior.left_funds_in_debtor") and not (
        is_yes(annotation, "behavior.did_not_demand_repayment")
        or is_yes(annotation, "behavior.extended_maturity")
        or is_yes(annotation, "behavior.changed_contract_terms")
    ):
        add(
            [
                "annotation.behavior.left_funds_in_debtor",
                "annotation.behavior.did_not_demand_repayment",
                "annotation.behavior.extended_maturity",
                "annotation.behavior.changed_contract_terms",
            ],
            "left_funds_in_debtor=yes usually needs non-demand, extension, or changed terms",
            "warning",
        )

    if is_yes(annotation, "proof.court_recognized_debt_real") and sham in {"sham", "artificial", "sham_and_artificial"}:
        add(
            ["annotation.proof.court_recognized_debt_real", "annotation.proof.court_found_sham_or_artificial"],
            "court_recognized_debt_real=yes conflicts with sham/artificial debt finding",
            "critical",
        )

    if legal_qualification == "included_general" and key_reason in {
        "compensation_financing",
        "corporate_nature",
        "sham_or_artificial_debt",
        "reality_not_proven",
    }:
        add(
            ["annotation.qualification.legal_qualification", "annotation.qualification.key_reason_category"],
            "included_general conflicts with reason usually supporting subordination/refusal",
            "critical",
        )

    if legal_qualification == "refused" and key_reason == "market_behavior":
        add(
            ["annotation.qualification.legal_qualification", "annotation.qualification.key_reason_category"],
            "refused conflicts with market_behavior as key reason",
            "warning",
        )

    if legal_qualification == "remand" and key_reason not in {"procedural_remand", "unknown"}:
        add(
            ["annotation.qualification.legal_qualification", "annotation.qualification.key_reason_category"],
            "remand usually requires key_reason_category=procedural_remand",
            "warning",
        )

    return issues


# ДОЗАПОЛНЕНИЕ ПО РЕЗУЛЬТАТАМ missingFieldRefiner

### снятие префикса annotation. с пути
def strip_annotation_prefix(path: str) -> str:
    value = str(path or "").strip()
    while value.startswith("annotation."):
        value = value[len("annotation."):]
    return value

### нормализация отчета missingFieldRefiner
def normalize_refinement_results(refinement_extractions: Any) -> list[dict[str, Any]]:
    data = to_plain_data(refinement_extractions)

    if not data:
        return []

    if isinstance(data, dict):
        items = data.get("refinement_results") or data.get("schema_extractions") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    return [dict(item) for item in items if isinstance(item, dict)]

### получение кандидата дозаполнения по целевому пути
def get_candidate_feature_from_partial(partial: dict[str, Any], target_path: str) -> Any:
    clean_path = strip_annotation_prefix(target_path)
    if not clean_path:
        return None

    # прямой поиск в annotation like объекте
    direct = get_by_path(partial, clean_path)
    if direct is not None:
        return direct

    # запасной поиск при обертке annotation
    wrapped = get_by_path({"annotation": partial}, target_path if target_path.startswith("annotation.") else f"annotation.{target_path}")
    if wrapped is not None:
        return wrapped

    return None

### можно ли пытаться дозаполнять данный путь через LLM-кандидата
def is_refinable_path(path: str) -> bool:
    clean = strip_annotation_prefix(path)
    if not clean:
        return False

    # deterministic вывод economic_dispute_basket без llm
    if clean.startswith("economic_dispute."):
        return False

    section = clean.split(".", 1)[0]
    return section in ANNOTATION_SECTIONS

### применение кандидатов дозаполнения к annotation
def apply_refinement_extractions(
    *,
    annotation: dict[str, Any],
    refinement_extractions: Any,
):
    # применение только целевых путей дозаполнения
    # сохранение уже принятых полей
    # отклонение unknown и признаков без доказательств
    updated_annotation = copy.deepcopy(annotation)

    audit = {
        "used": False,
        "accepted": [],
        "rejected": [],
        "skipped": [],
        "errors": [],
        "stats": {
            "results_count": 0,
            "target_paths_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "skipped_count": 0,
            "errors_count": 0,
        },
    }

    results = normalize_refinement_results(refinement_extractions)
    audit["stats"]["results_count"] = len(results)

    if not results:
        return updated_annotation, audit

    audit["used"] = True

    for result in results:
        extraction_type = result.get("extraction_type") or result.get("job_name") or "unknown_refiner"
        target_paths = [str(path).strip() for path in (result.get("target_paths") or []) if str(path).strip()]
        audit["stats"]["target_paths_count"] += len(target_paths)

        if result.get("error") or result.get("parse_error"):
            audit["errors"].append(
                {
                    "extraction_type": extraction_type,
                    "target_paths": target_paths,
                    "reason": "refinement_extractor_error",
                    "error": result.get("error") or result.get("parse_error"),
                }
            )
            continue

        partial = unwrap_structured_answer(result.get("structured_answer"))
        if not isinstance(partial, dict):
            audit["errors"].append(
                {
                    "extraction_type": extraction_type,
                    "target_paths": target_paths,
                    "reason": "empty_or_unreadable_refinement_answer",
                }
            )
            continue

        for target_path in target_paths:
            if not is_refinable_path(target_path):
                audit["skipped"].append(
                    {
                        "path": target_path,
                        "extraction_type": extraction_type,
                        "reason": "path_is_not_refinable_or_derived_only",
                    }
                )
                continue

            current_feature = get_by_path({"annotation": updated_annotation}, target_path)
            if is_accepted_feature(current_feature):
                audit["skipped"].append(
                    {
                        "path": target_path,
                        "extraction_type": extraction_type,
                        "reason": "target_field_already_accepted_not_overwritten",
                        "current_value": get_feature_value(current_feature),
                    }
                )
                continue

            candidate = get_candidate_feature_from_partial(partial, target_path)
            if candidate is None:
                audit["rejected"].append(
                    {
                        "path": target_path,
                        "extraction_type": extraction_type,
                        "reason": "candidate_field_not_returned",
                    }
                )
                continue

            if looks_like_feature(candidate):
                candidate = normalize_binary_feature(candidate) if is_binary_path(target_path) else candidate

            if not looks_like_feature(candidate):
                audit["rejected"].append(
                    {
                        "path": target_path,
                        "extraction_type": extraction_type,
                        "reason": "candidate_is_not_feature_object",
                        "candidate_preview": candidate,
                    }
                )
                continue

            if is_unknown_value(candidate.get("value")):
                audit["rejected"].append(
                    {
                        "path": target_path,
                        "extraction_type": extraction_type,
                        "reason": "candidate_value_is_unknown",
                    }
                )
                continue

            if not has_valid_evidence(candidate):
                audit["rejected"].append(
                    {
                        "path": target_path,
                        "extraction_type": extraction_type,
                        "reason": "candidate_without_required_evidence",
                        "value": candidate.get("value"),
                        "evidence_quote": candidate.get("evidence_quote", ""),
                        "evidence_source": candidate.get("evidence_source", "none"),
                    }
                )
                continue

            clean_path = strip_annotation_prefix(target_path)
            accepted_candidate = copy.deepcopy(candidate)
            accepted_candidate["refined_by"] = extraction_type
            set_by_path(updated_annotation, clean_path, accepted_candidate)

            audit["accepted"].append(
                {
                    "path": target_path,
                    "extraction_type": extraction_type,
                    "value": accepted_candidate.get("value"),
                    "evidence_source": accepted_candidate.get("evidence_source"),
                }
            )

    audit["stats"]["accepted_count"] = len(audit["accepted"])
    audit["stats"]["rejected_count"] = len(audit["rejected"])
    audit["stats"]["skipped_count"] = len(audit["skipped"])
    audit["stats"]["errors_count"] = len(audit["errors"])

    return updated_annotation, audit

# ПУБЛИЧНЫЙ API

### построение итоговой структуры и аудита извлечений
def audit_schema_extractions(
    *,
    schema_extractions: Sequence[dict[str, Any]],
    header = None,
    case_context = None,
    legal_references_report = None,
    merged_cards = None,
    refinement_extractions: Any = None,
) -> dict[str, Any]:
    field_conflicts: list[dict[str, Any]] = []
    rejected_fields: list[dict[str, Any]] = []
    structural_warnings: list[dict[str, Any]] = []

    annotation: dict[str, Any] = {}

    # приоритет metadata из headerExtractor
    if header is not None:
        annotation["metadata"] = build_metadata_from_header(header, case_context)

    extractor_sources: list[dict[str, Any]] = []

    for extractor_name, partial in iter_partial_blocks(schema_extractions):
        extractor_sources.append({"extractor": extractor_name, "sections": sorted(partial.keys())})

        for section, value in partial.items():
            if section in {"audit", "legal_references"}:
                continue

            if section == "annotation" and isinstance(value, dict):
                for nested_section, nested_value in value.items():
                    if nested_section in ANNOTATION_SECTIONS:
                        annotation[nested_section] = merge_values(
                            annotation.get(nested_section),
                            nested_value,
                            path=nested_section,
                            field_conflicts=field_conflicts,
                            rejected_fields=rejected_fields,
                        )
                    else:
                        structural_warnings.append(
                            {"path": f"annotation.{nested_section}", "reason": "unexpected_section_from_extractor"}
                        )
                continue

            if section in ANNOTATION_SECTIONS:
                # игнорирование economic_dispute из llm для детерминированного basket
                if section == "economic_dispute":
                    structural_warnings.append(
                        {
                            "path": "annotation.economic_dispute",
                            "reason": "llm_economic_dispute_ignored_basket_is_derived_only",
                            "extractor": extractor_name,
                        }
                    )
                    continue

                annotation[section] = merge_values(
                    annotation.get(section),
                    value,
                    path=section,
                    field_conflicts=field_conflicts,
                    rejected_fields=rejected_fields,
                )
            else:
                structural_warnings.append({"path": section, "reason": "unexpected_top_level_section_from_extractor"})

    for section in ANNOTATION_SECTIONS:
        annotation.setdefault(section, {})

    # адресное дозаполнение по результатам missingfieldrefiner
    # применение до расчета economic_dispute_basket
    refinement_audit = {
        "used": False,
        "accepted": [],
        "rejected": [],
        "skipped": [],
        "errors": [],
        "stats": {
            "results_count": 0,
            "target_paths_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "skipped_count": 0,
            "errors_count": 0,
        },
    }
    if refinement_extractions is not None:
        annotation, refinement_audit = apply_refinement_extractions(
            annotation=annotation,
            refinement_extractions=refinement_extractions,
        )

    # детерминированное заполнение economic_dispute_basket по принятым признакам
    annotation.setdefault("economic_dispute", {})
    basket_feature, basket_audit = infer_economic_dispute_basket(annotation)
    annotation["economic_dispute"]["economic_dispute_basket"] = basket_feature

    final_case = {
        "annotation": annotation,
        "legal_references": flatten_legal_references(legal_references_report),
        "audit": {},
    }

    validate_economic_basket(final_case, structural_warnings)

    parse_errors = detect_parse_errors(schema_extractions)
    evidence_warnings = build_evidence_warnings(final_case)
    logical_inconsistencies = build_logical_inconsistencies(final_case)
    missing_key_fields = missing_fields(final_case, ABSOLUTE_KEY_FIELDS)
    soft_missing_fields = missing_fields(final_case, SOFT_KEY_FIELDS)
    accepted_fields = build_accepted_fields(final_case)

    manual_review_required: list[dict[str, Any]] = []
    manual_review_required.extend({"path": item["path"], "reason": "field_conflict"} for item in field_conflicts)
    manual_review_required.extend(
        {"path": ", ".join(item.get("paths", [])), "reason": item.get("reason")}
        for item in logical_inconsistencies
        if item.get("severity") == "critical"
    )
    manual_review_required.extend(
        {"path": item.get("path", "schema_extractions"), "reason": item.get("reason", "structural_warning")}
        for item in structural_warnings
        if item.get("reason") != "llm_economic_dispute_ignored_basket_is_derived_only"
    )

    if parse_errors:
        manual_review_required.extend(
            {
                "path": f"schema_extractions.{item.get('extraction_type')}",
                "reason": "extractor_parse_or_runtime_error",
            }
            for item in parse_errors
        )

    refill_tasks = [
        *[
            {
                "path": item["path"],
                "priority": "absolute_key",
                "reason": item["reason"],
                "needed_context": item["needed_context"],
            }
            for item in missing_key_fields
        ],
        *[
            {
                "path": item["path"],
                "priority": "soft_key",
                "reason": item["reason"],
                "needed_context": item["needed_context"],
            }
            for item in soft_missing_fields
        ],
    ]

    has_critical_logic = any(item.get("severity") == "critical" for item in logical_inconsistencies)
    has_serious_structural_warning = any(
        item.get("reason") != "llm_economic_dispute_ignored_basket_is_derived_only"
        for item in structural_warnings
    )

    if field_conflicts or has_critical_logic or parse_errors or has_serious_structural_warning:
        overall_status = "needs_manual_review"
    elif missing_key_fields or rejected_fields or evidence_warnings:
        overall_status = "needs_refill"
    elif soft_missing_fields or logical_inconsistencies:
        overall_status = "complete_with_warnings"
    else:
        overall_status = "complete"

    final_case["audit"] = {
        "overall_status": overall_status,
        "accepted_fields": accepted_fields,
        "rejected_fields": rejected_fields,
        "refinement_audit": refinement_audit,
        "missing_key_fields": missing_key_fields,
        "soft_missing_fields": soft_missing_fields,
        "contextual_fields": CONTEXTUAL_FIELDS,
        "field_conflicts": field_conflicts,
        "logical_inconsistencies": logical_inconsistencies,
        "evidence_warnings": evidence_warnings,
        "refill_tasks": refill_tasks,
        "manual_review_required": manual_review_required,
        "structural_warnings": structural_warnings,
        "extractor_errors": parse_errors,
        "extractor_sources": extractor_sources,
        "economic_dispute_basket_audit": basket_audit,
        "merged_cards_stats": (merged_cards or {}).get("stats", {}) if isinstance(merged_cards, dict) else {},
        "summary": {
            "sections_present": sorted(k for k, v in annotation.items() if v),
            "legal_references_count": final_case["legal_references"].get("hits_count", 0),
            "accepted_fields_count": len(accepted_fields),
            "rejected_fields_count": len(rejected_fields),
            "missing_key_fields_count": len(missing_key_fields),
            "soft_missing_fields_count": len(soft_missing_fields),
            "field_conflicts_count": len(field_conflicts),
            "logical_inconsistencies_count": len(logical_inconsistencies),
            "evidence_warnings_count": len(evidence_warnings),
            "refill_tasks_count": len(refill_tasks),
            "refinement_used": refinement_audit.get("used", False),
            "refinement_accepted_count": len(refinement_audit.get("accepted", [])),
            "refinement_rejected_count": len(refinement_audit.get("rejected", [])),
            "refinement_errors_count": len(refinement_audit.get("errors", [])),
        },
    }

    return final_case

### формирование html представления итогового дела
def render_final_case_html(final_case: dict[str, Any]) -> str:
    import html

    audit = final_case.get("audit") or {}
    status = audit.get("overall_status", "unknown")
    body = json.dumps(final_case, ensure_ascii=False, indent=2)

    return f"""
    <div class="raw-card">
        <h2>finalStructuredCase</h2>
        <div class="source">audit.overall_status={html.escape(str(status))}</div>
        <pre>{html.escape(body)}</pre>
    </div>
    """
