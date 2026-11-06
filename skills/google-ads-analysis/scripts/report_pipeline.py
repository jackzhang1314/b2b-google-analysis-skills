"""Build an offline HTML report and review CSVs from a declared evidence bundle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from evidence_guard import (
    EvidenceError,
    ADDITIVE,
    INTEGERS,
    number,
    checked_date,
    inventory_coverage,
    negative_gate,
    require,
    summarize,
)
from search_review import coverage, ngrams, partition_inventory, positive_overlap

Json = dict[str, Any]
CORE = (
    "account_current",
    "account_previous",
    "campaigns_current",
    "campaigns_previous",
)
LABELS = {
    "KEEP": "保留",
    "INVESTIGATE": "先查清，再决定",
    "REVIEW_ONLY": "可考虑精确否定，尚未执行",
    "ALREADY_COVERED": "已有相关规则，先不重复添加",
}
ACTION_FIELDS = (
    ("why_it_matters", "为什么要管"),
    ("owner", "由谁负责"),
    ("expected_benefit", "希望带来的帮助"),
    ("success_check", "做完看什么"),
    ("decision_boundary", "什么情况先不调整广告"),
)


def period(data: Json) -> tuple[date, date]:
    return checked_date(data["period"]["start"]), checked_date(data["period"]["end"])


def timestamp(value: object) -> datetime:
    require(isinstance(value, str), "Missing evidence timestamp")
    result = datetime.fromisoformat(str(value))
    require(result.utcoffset() is not None, "Timestamp needs a timezone offset")
    return result


def check_context(value: Json, anchor: Json) -> None:
    for key in ("customer_id", "currency", "timezone"):
        if key in value:
            require(
                str(value[key]) == str(anchor[key]),
                f"Configuration context mismatch: {key}",
            )


def configuration_times(value: object, anchor: Json, as_of: datetime) -> list[datetime]:
    """Check declared context at every configuration level before deriving relationships."""
    times: list[datetime] = []
    if isinstance(value, dict):
        check_context(value, anchor)
        if "period" in value:
            require(
                value["period"] == anchor["period"],
                "Nested configuration period/date basis mismatch",
            )
        if "date_basis" in value:
            require(
                value["date_basis"] == anchor["period"]["date_basis"],
                "Nested date basis mismatch",
            )
        for key in ("retrieved_at", "configuration_retrieved_at", "as_of"):
            if key in value:
                observed = timestamp(value[key])
                require(observed <= as_of, "Nested configuration evidence after as_of")
                times.append(observed)
        for child in value.values():
            if isinstance(child, (dict, list)):
                times.extend(configuration_times(child, anchor, as_of))
    elif isinstance(value, list):
        for child in value:
            times.extend(configuration_times(child, anchor, as_of))
    return times


def check_measurement_context(
    data: Json, anchor: Json, as_of: datetime, campaigns: set[str]
) -> bool:
    configuration_times(data, anchor, as_of)
    check_context(data, anchor)
    if "period" in data:
        require(
            data["period"] == anchor["period"], "Measurement period/date basis mismatch"
        )
    for key in ("retrieved_at", "configuration_retrieved_at"):
        if key in data:
            require(timestamp(data[key]) <= as_of, "Measurement evidence after as_of")
    for name in ("configured", "reported"):
        for row in data.get(name, []):
            check_context(row, anchor)
            for key in (
                "conversions",
                "conversions_value",
                "all_conversions",
                "default_value",
            ):
                if row.get(key) is not None:
                    number(row[key])
    goals = data.get("goal_evidence", {})
    check_context(goals, anchor)
    if "retrieved_at" in goals:
        require(timestamp(goals["retrieved_at"]) <= as_of, "Goal evidence after as_of")
    action_ids = {str(row["id"]) for row in data.get("configured", [])}
    goal_ids = {str(row["id"]) for row in goals.get("custom_goals", [])}
    require(
        len(goal_ids) == len(goals.get("custom_goals", [])), "Duplicate custom goal ID"
    )
    links = goals.get("campaign_goals", [])
    require(
        len({str(link.get("campaign_id")) for link in links}) == len(links),
        "Duplicate campaign goal configuration",
    )
    for goal in goals.get("custom_goals", []):
        check_context(goal, anchor)
        require(
            set(goal.get("actions", [])) <= action_ids, "Goal references unknown action"
        )
    for link in goals.get("campaign_goals", []):
        check_context(link, anchor)
        require(
            str(link.get("campaign_id")) in campaigns,
            "Goal belongs to unknown campaign",
        )
        if link.get("custom_goal_id") is not None:
            require(str(link["custom_goal_id"]) in goal_ids, "Unknown custom goal")
    return (
        all(
            key in data
            for key in (
                "customer_id",
                "currency",
                "timezone",
                "period",
                "retrieved_at",
                "configuration_retrieved_at",
            )
        )
        and data.get("configuration_complete") is True
        and data.get("performance_complete") is True
    )


def validate_bundle(bundle: Json) -> tuple[Json, list[str]]:
    require(bundle.get("bundle_version") == "0.2", "Unsupported bundle version")
    tables = bundle.get("datasets")
    if not isinstance(tables, dict):
        raise EvidenceError("Missing datasets")
    mode = bundle.get("mode", "compare")
    require(
        mode in {"compare", "search_terms", "measurement", "single_period"},
        "Unknown report mode",
    )
    required = CORE if mode == "compare" else ("account_current", "campaigns_current")
    require(
        all(key in tables for key in required),
        "Need independent account/campaign tables for selected mode",
    )
    if mode == "search_terms":
        require("terms_current" in tables, "Search review requires current terms")
    if mode == "measurement":
        require(
            bool(bundle.get("measurement")),
            "Measurement review requires action evidence",
        )
    summaries = {key: summarize(data) for key, data in tables.items()}
    anchor = tables["account_current"]
    as_of = timestamp(bundle.get("as_of"))
    ids = [table["evidence_id"] for table in tables.values()]
    require(len(set(ids)) == len(ids), "Evidence IDs must be unique")
    for table in tables.values():
        require(
            timestamp(table["retrieved_at"]) <= as_of,
            "Dataset evidence after report as_of",
        )
        require(
            all(
                table[key] == anchor[key]
                for key in ("customer_id", "currency", "timezone")
            ),
            "Mixed account/currency/timezone",
        )
        require(
            table["period"]["date_basis"] == anchor["period"]["date_basis"],
            "Mixed date basis",
        )
    for suffix in ("current", "previous"):
        if f"account_{suffix}" not in tables and f"campaigns_{suffix}" not in tables:
            continue
        require(
            f"account_{suffix}" in tables and f"campaigns_{suffix}" in tables,
            "Incomplete comparison pair",
        )
        require(
            period(tables[f"account_{suffix}"])
            == period(tables[f"campaigns_{suffix}"]),
            "Account/campaign period mismatch",
        )
        require(
            tables[f"campaigns_{suffix}"]["grain"] == ["campaign_id"],
            "Aggregate campaign segments before reporting",
        )
        require(
            tables[f"account_{suffix}"]["grain"] == ["customer_id"],
            "Aggregate account segments before reporting",
        )
    start, end = period(anchor)
    warnings = [
        f"{key}: {w}" for key, summary in summaries.items() for w in summary["warnings"]
    ]
    if bundle.get("measurement"):
        complete = check_measurement_context(
            bundle["measurement"],
            anchor,
            as_of,
            {str(r["campaign_id"]) for r in tables["campaigns_current"]["rows"]},
        )
        if not complete:
            warnings.append("MEASUREMENT_CONTEXT_OR_COVERAGE_UNVERIFIED")
    if "account_previous" in tables:
        old_start, old_end = period(tables["account_previous"])
        require(end - start == old_end - old_start, "Unequal comparison duration")
        require(old_end < start or end < old_start, "Comparison periods overlap")
        if start.weekday() != old_start.weekday():
            warnings.append(
                "WEEKDAYS_DIFFER: user-selected windows may have weekday effects"
            )
    for key in ("terms_current", "terms_history"):
        if key in tables:
            require(
                tables[key]["grain"] == ["campaign_id", "term"],
                "Terms require canonical campaign + raw term grain",
            )
    if "terms_current" in tables:
        require(
            period(tables["terms_current"]) == (start, end),
            "Search term window mismatch",
        )
    if "terms_history" in tables:
        hs, he = period(tables["terms_history"])
        require(
            hs < start and he <= end,
            "History must start earlier and not use later outcomes",
        )
    return summaries, warnings


def reconcile(tables: Json, summaries: Json) -> Json:
    result: Json = {}
    for suffix in ("current", "previous"):
        if f"account_{suffix}" not in summaries:
            continue
        a, c = summaries[f"account_{suffix}"], summaries[f"campaigns_{suffix}"]
        differences: Json = {}
        metrics = (
            set(a["reported_subset_totals"])
            & set(c["reported_subset_totals"])
            & ADDITIVE
        )
        for m in sorted(metrics):
            av, cv = (
                a["reported_subset_totals"].get(m),
                c["reported_subset_totals"].get(m),
            )
            differences[m] = None if av is None or cv is None else av - cv

        def agrees(metric: str, value: object) -> bool:
            if value is None:
                return False
            if metric in INTEGERS:
                return value == 0
            return abs(float(str(value))) <= 1e-6 + 1e-9 * abs(
                float(a["reported_subset_totals"].get(metric) or 0)
            )

        result[suffix] = {
            "differences": differences,
            "matched": bool(metrics)
            and all(agrees(m, v) for m, v in differences.items()),
            "extraction_complete": a["extraction_complete"]
            and c["extraction_complete"],
        }
    if "terms_current" in summaries:
        by_campaign = {
            str(r["campaign_id"]): r for r in tables["campaigns_current"]["rows"]
        }
        totals: dict[str, dict[str, int | None]] = {}
        for row in tables["terms_current"]["rows"]:
            cid = str(row["campaign_id"])
            sub = totals.setdefault(cid, {m: 0 for m in INTEGERS})
            for m in INTEGERS:
                sub[m] = (
                    None
                    if sub[m] is None or row.get(m) is None
                    else int(sub[m] or 0) + int(row[m])
                )
        inconsistent = []
        for cid, sub in totals.items():
            upper = by_campaign.get(cid)
            if upper is None:
                inconsistent.append(cid)
            elif summaries["campaigns_current"]["extraction_complete"] and any(
                sub[m] is not None
                and upper.get(m) is not None
                and int(sub[m] or 0) > int(upper[m])
                for m in INTEGERS
            ):
                inconsistent.append(cid)
        result["term_scope_inconsistencies"] = sorted(inconsistent)
        term_cost = summaries["terms_current"]["reported_subset_totals"].get(
            "cost_micros"
        )
        cost = summaries["account_current"]["reported_subset_totals"].get("cost_micros")
        result["visible_term_cost_fraction"] = (
            None if cost is None or cost <= 0 or term_cost is None else term_cost / cost
        )
        result["visibility_note"] = (
            "可见字词花费占账户花费，不是完整搜索覆盖率；差额不能全部归因于隐私隐藏。"
        )
    return result


def review_terms(bundle: Json, summaries: Json) -> list[Json]:
    tables = bundle["datasets"]
    if "terms_current" not in tables:
        return []
    current = tables["terms_current"]
    history = tables.get("terms_history")
    history_rows = (
        {}
        if history is None
        else {(str(r["campaign_id"]), r["term"]): r for r in history["rows"]}
    )
    context, inventory = (
        bundle.get("business_context", {}),
        bundle.get("negative_inventory"),
    )
    cid = current["customer_id"]
    if inventory is not None:
        check_context(inventory, current)
        require(
            inventory.get("customer_id") == cid,
            "Negative inventory belongs to another account",
        )
        fetched = timestamp(inventory.get("retrieved_at"))
        as_of = timestamp(bundle.get("as_of"))
        snapshot_times = configuration_times(inventory, current, as_of)
        require(fetched <= as_of, "Negative inventory evidence after as_of")
        # A named freshness policy, not a claim that configurations cannot change sooner.
        if as_of - min(snapshot_times or [fetched]) > timedelta(hours=24):
            inventory = dict(inventory, complete=False)
    if context:
        require(
            context.get("customer_id") == cid,
            "Business context belongs to another account",
        )
    reviews = bundle.get("term_reviews", [])
    require(isinstance(reviews, list), "term_reviews must be an array")
    keys = [(str(r["campaign_id"]), r["term"]) for r in reviews]
    require(len(set(keys)) == len(keys), "Duplicate term review")
    source_keys = {(str(r["campaign_id"]), r["term"]) for r in current["rows"]}
    require(set(keys) <= source_keys, "Review does not match current evidence")
    reviews_by_key = dict(zip(keys, reviews, strict=True))
    inventory_by_campaign = (
        {}
        if inventory is None
        else partition_inventory(
            inventory, {str(r["campaign_id"]) for r in current["rows"]}
        )
    )
    protected = {str(t).strip().lower() for t in context.get("protected_terms", [])}
    results = []
    for row in current["rows"]:
        campaign, term = str(row["campaign_id"]), str(row["term"])
        key = campaign, term
        review = reviews_by_key.get(key, {})
        h = history_rows.get(key)
        cov = (
            coverage(term, campaign, inventory_by_campaign[campaign])
            if inventory is not None
            else {
                "inventory_complete": False,
                "literal_rule_present": False,
                "rule_ids": [],
                "ad_group_only_rule_ids": [],
                "uncertain_rule_ids": [],
            }
        )
        hist_complete = (
            history is not None
            and summaries["terms_history"]["extraction_complete"]
            and h is not None
        )
        history_ref = (
            "history:"
            + hashlib.sha256((campaign + "\0" + term).encode()).hexdigest()[:16]
        )
        current_ref = str(current["evidence_id"])
        business_ref = str(context.get("evidence_id", "business-missing"))
        inventory_ref = (
            "inventory-missing"
            if inventory is None
            else str(inventory.get("evidence_id", "inventory-missing"))
        )
        registry = {
            current_ref: {"role": "current", "customer_id": cid, "status": "verified"},
            history_ref: {
                "role": "history",
                "customer_id": cid,
                "campaign_ids": [campaign],
                "terms": [term],
                "status": "verified" if hist_complete else "missing",
            },
            business_ref: {
                "role": "business",
                "customer_id": cid,
                "status": "verified"
                if context.get("confirmed") is True
                else "unconfirmed",
            },
            inventory_ref: {
                "role": "inventory",
                "customer_id": cid,
                "campaign_ids": []
                if inventory is None
                else inventory.get("campaign_ids", []),
                "status": "verified" if cov["inventory_complete"] else "incomplete",
            },
        }
        fit = review.get("business_fit", "unknown")
        quote = review.get("business_quote", "")
        grounded = (
            isinstance(quote, str)
            and bool(quote.strip())
            and quote in context.get("description", "")
            and bool(review.get("rationale"))
        )
        if fit == "outside" and not grounded:
            fit = "unknown"
        positive = (
            []
            if inventory is None
            else positive_overlap(term, campaign, inventory_by_campaign[campaign])
        )
        candidate = {
            "customer_id": cid,
            "campaign_id": campaign,
            "term": term,
            "negative_text": term,
            "business_fit": fit,
            "protected": term.strip().lower() in protected or bool(positive),
            "history_conversions": None if h is None else h.get("conversions"),
            "inventory_complete": cov["inventory_complete"],
            "already_covered": cov["literal_rule_present"],
            "scope": "campaign",
            "match_type": "EXACT",
            "business_ref": business_ref,
            "history_ref": history_ref,
            "inventory_ref": inventory_ref,
            "evidence_refs": [current_ref, business_ref, history_ref, inventory_ref],
            "evidence_registry": registry,
        }
        decision = negative_gate(candidate)
        if decision["state"] == "REVIEW_ONLY":
            if (
                row.get("conversions") is None
                or row["conversions"] != 0
                or not summaries["terms_current"]["extraction_complete"]
            ):
                decision.update(
                    state="INVESTIGATE",
                    reasons=["CURRENT_CONVERSION_OR_INCOMPLETE_EVIDENCE"],
                )
            elif (
                row.get("all_conversions") is None
                or h is None
                or h.get("all_conversions") is None
            ):
                decision.update(
                    state="INVESTIGATE",
                    reasons=["OTHER_CONVERSION_ACTIONS_NOT_CHECKED"],
                )
            elif any(
                source is not None
                and source.get("all_conversions") is not None
                and number(source["all_conversions"]) > 0
                for source in (row, h)
            ):
                decision.update(
                    state="INVESTIGATE",
                    reasons=["OTHER_CONVERSION_ACTIONS_NEED_REVIEW"],
                )
            elif cov["uncertain_rule_ids"] or cov["ad_group_only_rule_ids"]:
                decision.update(
                    state="INVESTIGATE", reasons=["COVERAGE_NEEDS_SCOPE_REVIEW"]
                )
        results.append(
            {
                "campaign_id": campaign,
                "campaign_name": row.get("campaign_name", campaign),
                "term": term,
                "negative_text": term,
                "match_type": "EXACT",
                "scope": "campaign",
                "current_cost_micros": row.get("cost_micros"),
                "current_clicks": row.get("clicks"),
                "current_conversions": row.get("conversions"),
                "current_all_conversions": row.get("all_conversions"),
                "history_cost_micros": None if h is None else h.get("cost_micros"),
                "history_clicks": None if h is None else h.get("clicks"),
                "history_conversions": candidate["history_conversions"],
                "history_all_conversions": None
                if h is None
                else h.get("all_conversions"),
                "history_period": None if history is None else history.get("period"),
                "business_fit": fit,
                "business_rationale": review.get("rationale")
                or (
                    "核心业务或正向词保护，保留并排查效率。"
                    if candidate["protected"] or fit == "core"
                    else "本系列历史有平台转化，先核验真实业务质量。"
                    if h is not None and h.get("conversions") not in (None, 0)
                    else "缺少已确认的业务、历史或规则证据，先补证。"
                ),
                "coverage": cov,
                "positive_keyword_overlap": positive,
                "evidence_refs": candidate["evidence_refs"],
                "evidence_registry": registry,
                **decision,
            }
        )
    return results


def measurement_review(data: Json) -> Json:
    if not data:
        return {"status": "NOT_COLLECTED", "actions": [], "business_roi": None}
    cov = inventory_coverage(data)
    reported = {r["id"]: r for r in data["reported"]}
    goals = data.get("goal_evidence", {})
    custom = {g["id"]: g.get("actions", []) for g in goals.get("custom_goals", [])}
    applied: dict[str, list[str]] = {}
    for campaign in goals.get("campaign_goals", []):
        for action_id in custom.get(campaign.get("custom_goal_id"), []):
            applied.setdefault(action_id, []).append(str(campaign["campaign_id"]))
    actions = []
    configurations = list(data["configured"])
    configured_ids = {r["id"] for r in configurations}
    configurations += [
        {"id": action_id, "status": "UNKNOWN", "primary": None}
        for action_id in reported
        if action_id not in configured_ids
    ]
    for action in configurations:
        if action["status"] != "ENABLED" and action["id"] not in reported:
            continue
        actual = reported.get(action["id"])
        actions.append(
            {
                "id": action["id"],
                "name": action.get("name", ""),
                "status": action["status"],
                "primary": action["primary"],
                "category": action.get("category"),
                "origin": action.get("origin"),
                "type": action.get("type"),
                "counting_type": action.get("counting_type"),
                "click_through_lookback_window_days": action.get(
                    "click_through_lookback_window_days"
                ),
                "attribution_model": action.get(
                    "attribution_model_settings.attribution_model"
                ),
                "applied_custom_goal_campaigns": applied.get(action["id"], []),
                "always_use_default_value": action.get("always_use_default_value"),
                "default_value": action.get("default_value"),
                "has_performance_row": actual is not None,
                "conversions": None if actual is None else actual.get("conversions"),
                "conversions_value": None
                if actual is None
                else actual.get("conversions_value"),
                "all_conversions": None
                if actual is None
                else actual.get("all_conversions"),
                "business_success": "UNKNOWN",
                "front_end_trigger": "UNKNOWN",
                "platform_record": "REPORT_ROW_PRESENT"
                if actual is not None
                else "UNKNOWN",
                "counting_attribution": "UNKNOWN",
                "backend_success": "UNKNOWN",
                "crm_quality_revenue": "UNKNOWN",
            }
        )
    return {
        "status": "ADS_ONLY",
        "coverage": cov,
        "actions": actions,
        "goal_evidence": goals,
        "business_roi": None,
        "duplicate_tracking_proven": False,
        "offline_uploads_exist": None,
        "notes": [
            "无表现行不是零；无离线上传返回也不证明不存在上传。",
            "固定价值不是销售收入；目标是否参与优化需结合系列配置。",
            "未验证网站、去重、真实询盘与 CRM，不能由 Ads 数值替代。",
        ],
    }


def analyze(bundle: Json) -> Json:
    summaries, warnings = validate_bundle(bundle)
    tables = bundle["datasets"]
    known_refs = {str(t["evidence_id"]) for t in tables.values()}
    for context_key in ("business_context", "negative_inventory", "measurement"):
        if bundle.get(context_key, {}).get("evidence_id"):
            known_refs.add(str(bundle[context_key]["evidence_id"]))
    findings = bundle.get("findings", [])
    require(isinstance(findings, list), "Findings must be an array")
    for finding in findings:
        require(isinstance(finding, dict), "Finding must be an object")
        for key in ("title", "fact", "interpretation", "next_step"):
            require(
                isinstance(finding.get(key), str) and bool(finding[key].strip()),
                f"Missing finding {key}",
            )
        for key, _ in ACTION_FIELDS:
            if key in finding:
                require(
                    isinstance(finding[key], str) and bool(finding[key].strip()),
                    f"Invalid finding {key}",
                )
        refs = finding.get("evidence_refs")
        require(
            isinstance(refs, list)
            and bool(refs)
            and all(isinstance(ref, str) and ref in known_refs for ref in refs),
            "Unresolved finding evidence",
        )
    rec = reconcile(tables, summaries)
    for suffix in ("current", "previous"):
        if suffix in rec and not rec[suffix]["matched"]:
            warnings.append(f"{suffix}: ACCOUNT_CAMPAIGN_TOTAL_MISMATCH")
    if rec.get("term_scope_inconsistencies"):
        warnings.append("SEARCH_TERMS_EXCEED_OR_MISS_CAMPAIGN_SCOPE")
    inventory_times = configuration_times(
        bundle.get("negative_inventory", {}),
        tables["account_current"],
        timestamp(bundle["as_of"]),
    )
    if inventory_times and timestamp(bundle["as_of"]) - min(
        inventory_times
    ) > timedelta(hours=24):
        warnings.append("NEGATIVE_INVENTORY_STALE_REFRESH_REQUIRED")
    reviews = review_terms(bundle, summaries)
    evidence_index: dict[str, Json] = {
        str(table["evidence_id"]): {
            **{
                k: table.get(k)
                for k in (
                    "evidence_id",
                    "source",
                    "resource",
                    "period",
                    "retrieved_at",
                    "grain",
                    "limit",
                    "completeness",
                    "visibility",
                )
            },
            "bundle_section": "datasets." + name,
            "row_count": len(table["rows"]),
            "query": table.get("query"),
            "data_sha256": hashlib.sha256(
                json.dumps(
                    table, sort_keys=True, ensure_ascii=False, allow_nan=False
                ).encode()
            ).hexdigest(),
        }
        for name, table in tables.items()
    }
    for key in ("business_context", "negative_inventory", "measurement"):
        context = bundle.get(key, {})
        if context.get("evidence_id"):
            evidence_id = str(context["evidence_id"])
            require(
                evidence_id not in evidence_index,
                "Evidence ID reused by different context",
            )
            evidence_index[evidence_id] = {
                "evidence_id": evidence_id,
                "bundle_section": key,
                **{
                    k: context.get(k)
                    for k in ("customer_id", "source", "period", "retrieved_at")
                },
            }
    for review in reviews:
        for ref, evidence in review.pop("evidence_registry").items():
            evidence_index[ref] = {
                **evidence_index.get(ref, {}),
                **evidence,
                "evidence_id": ref,
            }
            if evidence.get("role") == "history":
                evidence_index[ref]["bundle_section"] = "datasets.terms_history"
                evidence_index[ref]["period"] = tables.get("terms_history", {}).get(
                    "period"
                )
    for review in reviews:
        if review["state"] in {"REVIEW_ONLY", "ALREADY_COVERED"} and (
            not rec["current"]["matched"]
            or not rec["current"]["extraction_complete"]
            or review["campaign_id"] in rec.get("term_scope_inconsistencies", [])
        ):
            review.update(
                state="INVESTIGATE",
                reasons=["INCONSISTENT_OR_INCOMPLETE_PARENT_EVIDENCE"],
            )
    warnings.append("CONVERSION_MATURITY_NOT_VERIFIED")
    current = summaries["account_current"]
    previous = summaries.get(
        "account_previous", {"reported_subset_totals": {}, "cpc": None}
    )
    ca, pa = current["reported_subset_totals"], previous["reported_subset_totals"]
    changes = {
        m: None if ca.get(m) is None or pa.get(m) is None else ca[m] - pa[m]
        for m in (
            "cost_micros",
            "clicks",
            "impressions",
            "conversions",
            "conversions_value",
        )
    }
    decomposition = None
    current_campaigns = {
        str(r["campaign_id"]): r for r in tables["campaigns_current"]["rows"]
    }
    prior_campaigns = {
        str(r["campaign_id"]): r
        for r in tables.get("campaigns_previous", {}).get("rows", [])
    }
    campaign_comparison = []
    for campaign_id in sorted(current_campaigns.keys() | prior_campaigns.keys()):
        now, old = (
            current_campaigns.get(campaign_id, {}),
            prior_campaigns.get(campaign_id, {}),
        )
        cost_now, cost_old = now.get("cost_micros"), old.get("cost_micros")
        campaign_comparison.append(
            {
                "campaign_id": campaign_id,
                "name": now.get("campaign_name", old.get("campaign_name", campaign_id)),
                "status": now.get("status"),
                "cost_current": None if cost_now is None else cost_now / 1_000_000,
                "cost_previous": None if cost_old is None else cost_old / 1_000_000,
                "cost_change": None
                if cost_now is None or cost_old is None
                else (cost_now - cost_old) / 1_000_000,
                "clicks_current": now.get("clicks"),
                "conversions_current": now.get("conversions"),
                "platform_cpa": None
                if cost_now is None
                or not now.get("conversions")
                or now["conversions"] <= 0
                else cost_now / 1_000_000 / now["conversions"],
            }
        )
    campaign_comparison.sort(key=lambda row: -abs(row["cost_change"] or 0))
    c1, c0 = ca.get("clicks"), pa.get("clicks")
    p1, p0 = current["cpc"], previous["cpc"]
    if c1 and c0 and p1 is not None and p0 is not None:
        decomposition = {
            "click_factor": (c1 - c0) * (p1 + p0) / 2,
            "cpc_factor": (p1 - p0) * (c1 + c0) / 2,
            "meaning": "Arithmetic decomposition, not causation",
        }
    return {
        "title": bundle.get("title", "Google Ads 分析报告"),
        "mode": bundle.get("mode", "compare"),
        "context": {
            k: tables["account_current"][k]
            for k in ("customer_id", "currency", "timezone", "period", "retrieved_at")
        },
        "previous_period": tables.get("account_previous", {}).get("period"),
        "summaries": summaries,
        "reconciliation": rec,
        "changes": changes,
        "cost_decomposition": decomposition,
        "campaign_comparison": campaign_comparison,
        "warnings": warnings,
        "campaigns": [
            {
                k: r.get(k)
                for k in (
                    "campaign_id",
                    "campaign_name",
                    "status",
                    "channel",
                    "cost_micros",
                    "clicks",
                    "impressions",
                    "conversions",
                    "conversions_value",
                )
            }
            for r in tables["campaigns_current"]["rows"]
        ],
        "negative_review": reviews,
        "ngrams": ngrams(tables.get("terms_current", {}).get("rows", [])),
        "measurement": measurement_review(bundle.get("measurement", {})),
        "findings": findings,
        "execution_allowed": False,
        "evidence_index": list(evidence_index.values()),
        "input_sha256": hashlib.sha256(
            json.dumps(
                bundle, sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest(),
    }


def safe_csv(value: object) -> str:
    text = (
        json.dumps(value, ensure_ascii=False)
        if isinstance(value, (list, dict))
        else ""
        if value is None
        else str(value)
    )
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
        else text
    )


def write_csv(path: Path, rows: list[Json], columns: list[str]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows([safe_csv(row.get(key)) for key in columns] for row in rows)


def display(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "、".join(display(item) for item in value) if value else "未列出"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def render_handoff(finding: Json, index: int) -> str:
    """A copyable, read-only next task; external finding text stays plain text."""
    prompt = (
        f"请先阅读我提供的这份广告报告，处理第{index}项：{finding['title']}\n"
        "先确认报告日期与现有权限，再按下面的步骤继续：\n"
        f"{finding['next_step']}\n"
        "已有权限下能完成的读取、整理和核对请直接做；确实缺少资料时，"
        "只告诉我需要补充的最少内容，以及从哪里找。用通俗中文解释结果和下一步。"
        "本次先只读检查，不修改广告、预算、目标或否定词，也不提交网站表单。"
    )
    return (
        '<div class="handoff">'
        f'<button type="button" class="copy-task" data-copy="task-{index}">'
        f"复制第{index}项任务给Codex</button>"
        f'<span id="task-{index}-status" class="copy-status" role="status"></span>'
        '<details><summary>查看任务文字，也可手动复制</summary>'
        f'<pre id="task-{index}" class="task-text">{html.escape(prompt)}</pre>'
        "</details></div>"
    )


HANDOFF_JS = """document.querySelectorAll('[data-copy]').forEach(button=>{
button.addEventListener('click',async()=>{
const target=document.getElementById(button.dataset.copy);
const status=document.getElementById(button.dataset.copy+'-status');
try{await navigator.clipboard.writeText(target.textContent);
status.textContent='已复制。回到Codex会话，粘贴发送，并附上这份报告。';}
catch{target.closest('details').open=true;
const range=document.createRange();range.selectNodeContents(target);
const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
status.textContent='未能自动复制。文字已展开并选中，请按 Ctrl+C 或 ⌘C，再粘贴给Codex。';}
});});"""


HANDOFF_CSS = """.copy-task{font:inherit;font-size:15px;font-weight:650;padding:10px 15px;
border:1px solid #245d4d;border-radius:6px;background:#245d4d;color:white;cursor:pointer}
.copy-status{display:block;color:#245d4d;font-size:14px;margin:8px 0}
.task-text{font-family:inherit;font-size:15px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}
.handoff{margin-top:20px}.handoff details{margin:10px 0}
button:focus-visible,.scroll:focus-visible,.mobile-records:focus-visible{outline:3px solid #a76325;outline-offset:4px}
@media print{.copy-task,.copy-status{display:none}}"""


def table(rows: list[Json], fields: list[tuple[str, str]], limit: int = 200) -> str:
    def cell(value: object) -> str:
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        attr = ' class="numeric"' if numeric else ""
        return f"<td{attr}>{html.escape(display(value))}</td>"

    header = "".join(f"<th>{html.escape(label)}</th>" for _, label in fields)
    body = "".join(
        "<tr>"
        + "".join(cell(row.get(key)) for key, _ in fields)
        + "</tr>"
        for row in rows[:limit]
    )
    cards = "".join(
        '<article class="record">'
        + "".join(
            f"<div><span>{html.escape(label)}</span><p>{html.escape(display(row.get(key)))}</p></div>"
            for key, label in fields
        )
        + "</article>"
        for row in rows[:limit]
    )
    return f'<p class="note">表格内可上下滚动；列较多时左右滚动。显示 {min(len(rows), limit)} / {len(rows)} 行；完整依据见 analysis.json，字词与转化另有 CSV。</p><div class="scroll desktop-table" tabindex="0" role="region" aria-label="数据明细，可滚动"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div><div class="mobile-records" tabindex="0" role="region" aria-label="数据明细卡片，可滚动">{cards}</div>'


def render(result: Json) -> str:
    context = result["context"]
    current = result["summaries"]["account_current"]
    totals = current["reported_subset_totals"]

    def esc(value: object) -> str:
        return html.escape(display(value))

    def metric(value: object) -> str:
        if value is None:
            return "未知"
        if isinstance(value, (int, float)):
            return f"{value:,.2f}".removesuffix(".00")
        return esc(value)

    previous_period = result["previous_period"]
    comparison = (
        "本次只检查一个时期，不作前后比较。"
        if previous_period is None
        else f"与 {esc(previous_period['start'])} — {esc(previous_period['end'])} 比较。"
    )
    cards = "".join(
        f'<div class="card"><span>{label}</span><strong>{metric(value)}</strong><small>{hint}</small></div>'
        for label, value, hint in (
            (
                f"广告花费（{esc(context['currency'])}）",
                totals.get("cost"),
                "这段时间实际投广告花了多少。",
            ),
            (
                "广告点击次数",
                totals.get("clicks"),
                "广告被点了多少次，不等于独立人数。",
            ),
            (
                "平均每次点击花费",
                current.get("cpc"),
                "总花费除以总点击；变便宜不保证客户更好。",
            ),
            (
                "平台记录的转化次数",
                totals.get("conversions"),
                "可能是留邮箱、填表等动作，不等于客户或订单数。",
            ),
        )
    )
    reviews = [
        dict(
            row,
            campaign_label=row.get("campaign_name") or row["campaign_id"],
            state_label=LABELS[row["state"]],
            current_cost=None
            if row["current_cost_micros"] is None
            else row["current_cost_micros"] / 1_000_000,
        )
        for row in result["negative_review"]
    ]
    priority = {"REVIEW_ONLY": 0, "INVESTIGATE": 1, "KEEP": 2, "ALREADY_COVERED": 3}
    reviews.sort(
        key=lambda row: (priority[row["state"]], -(row["current_cost_micros"] or 0))
    )
    findings = ""
    for index, finding in enumerate(result["findings"], 1):
        missing = [label for key, label in ACTION_FIELDS if not finding.get(key)]
        steps = "".join(
            f"<li>{esc(line.strip())}</li>"
            for line in finding["next_step"].splitlines()
            if line.strip()
        )
        explanations = "".join(
            f"<dt>{label}</dt><dd>{esc(finding[key])}</dd>"
            for key, label in ACTION_FIELDS
            if finding.get(key)
        )
        pending = (
            f'<p class="pending">行动说明待补充：{esc(missing)}。这些信息尚未确定，请 Codex 结合现有证据补齐。</p>'
            if missing
            else ""
        )
        findings += (
            f'<article class="action"><span class="eyebrow">优先 {index}</span>'
            f"<h3>{esc(finding['title'])}</h3>"
            f"<p><b>发现了什么：</b>{esc(finding['fact'])}</p>"
            f"<p><b>怎样理解：</b>{esc(finding['interpretation'])}</p>"
            f"<h4>具体怎么做</h4><ol>{steps}</ol><dl>{explanations}</dl>{pending}"
            f"{render_handoff(finding, index)}"
            f"<details><summary>查看这条建议的数据依据</summary><p>{esc(finding['evidence_refs'])}</p></details></article>"
        )
    if not findings:
        findings = '<p class="pending">这是自动核对底稿。Codex 仍需结合业务与证据补写事实、解释和优先动作；不要把空白底稿当完成诊断。</p>'
    counts = (
        "".join(
            f"<p><b>{esc(label)}：{sum(row['state'] == state for row in reviews)} 条</b></p>"
            for state, label in LABELS.items()
        )
        if reviews
        else "<p>当前输入没有可审查的搜索字词，不能据此断言账户没有这类流量。</p>"
    )
    warnings = "".join(f"<li>{esc(w)}</li>" for w in result["warnings"])
    visible_campaigns = [
        row
        for row in result["campaign_comparison"]
        if any(
            row.get(key)
            for key in (
                "cost_current",
                "cost_previous",
                "clicks_current",
                "conversions_current",
            )
        )
    ]
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(result["title"])}</title>
<style>body{{margin:0;background:#f4f2ec;color:#18252d;font:17px/1.8 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}h1{{font-size:36px;line-height:1.25}}h2{{margin-top:44px}}h3{{font-size:23px;line-height:1.45}}h4{{margin-bottom:6px}}.eyebrow{{color:#40685c;font-weight:700;letter-spacing:.05em}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}.card,article{{padding:24px;background:white;border:1px solid #d9ded8;border-radius:12px;margin:12px 0;min-width:0}}.card strong{{display:block;font-size:28px}}.card small{{display:block}}.note,small{{color:#536665}}.pending{{padding:16px;border-left:4px solid #a76325;background:#fff0dc}}.action p,.action li,.action dd{{overflow-wrap:anywhere}}.action dl{{display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px 18px}}dt{{font-weight:700}}dd{{margin:0;white-space:pre-line}}.scroll{{overflow:auto;background:white;max-height:560px}}.scroll thead th{{position:sticky;top:0;z-index:1}}td.numeric{{white-space:nowrap;font-variant-numeric:tabular-nums}}.mobile-records{{display:none}}.record div{{display:grid;grid-template-columns:100px minmax(0,1fr);gap:12px;border-bottom:1px solid #edf0ec;padding:7px 0}}.record span{{color:#536665;font-size:13px}}.record p{{margin:0;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;min-width:760px;font-size:14px}}th,td{{padding:12px;border-bottom:1px solid #e3e6e2;text-align:left;vertical-align:top}}th{{white-space:nowrap;background:#e4ece7}}a{{color:#126652}}details{{background:white;padding:20px;margin:20px 0;border:1px solid #d9ded8;border-radius:8px}}summary{{cursor:pointer;font-weight:700}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}a:focus-visible,summary:focus-visible{{outline:3px solid #a76325;outline-offset:4px}}@media(max-width:650px){{.desktop-table{{display:none}}.mobile-records{{display:block;max-height:65vh;overflow:auto}}main{{padding:24px 14px}}h1{{font-size:27px}}.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.card{{padding:16px}}.card strong{{font-size:25px}}.action dl{{grid-template-columns:1fr;gap:4px}}.action dd{{margin-bottom:12px}}}}@media print{{main{{padding:0}}details{{display:block}}.scroll,.mobile-records{{max-height:none;overflow:visible}}}}{HANDOFF_CSS}</style></head><body><main>
<p class="eyebrow">广告检查报告 · 建议尚未执行</p><h1>{esc(result["title"])}</h1>
<p>{esc(context["period"]["start"])} — {esc(context["period"]["end"])} · {esc(context["timezone"])} · {esc(context["currency"])}</p>
<p class="note">{comparison}先看下面的行动说明，再按需要展开数据。</p>
<h2>先做什么，为什么做</h2><p>先做第一项即可。把对应任务复制给Codex，并附上本报告；能读取和整理的工作交给它。这里的销售、广告和网站负责人是工作角色，一个人兼任也可以。当前套件用于只读分析；真正修改账户还需要对应的工具权限和具体变更授权。</p>{findings}
<h2>这段时间的钱花得怎么样</h2><div class="cards">{cards}</div>
<p>谷歌的“转化”是广告账户记录的某种动作，例如提交邮箱或点击联系按钮。是否是真正的采购咨询，需要结合实际咨询内容确认。真实 ROI：未知；也就是目前还不知道扣除相关成本后，广告到底带来了多少实际回报。</p>
<details><summary>查看各组广告的花费与记录</summary><p>“广告系列”可以理解为后台的一组广告，通常有自己的预算和目标。下表只列本次或对比期有花费、点击或转化的系列；完整输入保留在依据文件中。每组记录的成功动作可能不同，不要只看转化多少就排好坏。</p>{table(visible_campaigns, [("name", "广告系列"), ("cost_previous", "前期花费"), ("cost_current", "本期花费"), ("cost_change", "花费变化"), ("conversions_current", "平台记录的转化次数")])}</details>
<h2>哪些搜索可以考虑不再投</h2><p>搜索字词是客户实际输入的内容。“否定关键词”是限制某些搜索触发广告；本报告采用范围较小的系列内精确否定建议。它不会删除网站内容，也没有被自动执行。</p>{counts}
<p>先核对清单中的完整词和所属系列，不要自行拆成大范围词根。“保留”表示目前不建议否定，不代表已赚钱；“先查清”也不等于坏流量。建议花费是过去已经花掉的钱，不是保证今后能省下的钱。</p>
<details><summary>查看搜索字词、理由与处理范围</summary>{table(reviews, [("campaign_label", "所属广告系列"), ("term", "客户实际搜索"), ("current_cost", "本期花费"), ("current_conversions", "本期平台转化"), ("history_conversions", "同系列历史转化"), ("state_label", "处理建议"), ("business_rationale", "为什么")])}</details>
<p><a href="negative-review.csv">下载完整搜索字词待审表</a>（供审核，不是自动导入文件）</p>
<h2>这些记录是不是客户或订单</h2><p>目前只核对了广告平台。要判断客户质量，还需看网站是否真正收到提交、是否为同一个需求、销售是否确认相关和可联系。平台给一次行为设置的固定“价值”是参考分值，即使显示成金额，也不能直接当作销售收入。</p>
<p>“主转化”是计入平台“转化”列的动作记录；“所有转化”还可能包括其他动作。不要把两者相加当客户数。是否用于自动优化，还要看每组广告实际选了哪些目标。近期记录可能补回来，暂时没返回的数据也不当作零。</p>
<details><summary>给负责广告的同事：查看动作配置明细</summary>{table(result["measurement"]["actions"], [("name", "记录的动作"), ("status", "后台状态"), ("primary", "标为主要动作"), ("applied_custom_goal_campaigns", "使用自定义目标的系列"), ("always_use_default_value", "使用固定参考价值"), ("has_performance_row", "本期返回了记录"), ("conversions", "平台转化次数")])}</details>
<p><a href="measurement-review.csv">下载转化核对表</a></p>
<details><summary>进一步分析：反复出现的词组</summary><p>帮助发现反复出现的搜索需求，不直接用于否定。词组互相重叠，不能相加为“浪费总额”；中文及复杂符号还需结合上下文判断。</p>{table(result["ngrams"]["patterns"], [("campaign_id", "系列编号"), ("pattern", "反复出现的词组"), ("term_count", "涉及字词数"), ("clicks", "点击"), ("conversions", "平台转化")])}</details>
<details><summary>数据来源、限制与技术核对</summary><p>读取时间：{esc(context["retrieved_at"])}。账户编号：{esc(context["customer_id"])}。近期记录可能继续补回；不同报告范围需要分别核对。</p><ul>{warnings}</ul><pre>{esc(json.dumps(result["reconciliation"], ensure_ascii=False, indent=2))}</pre><p>报告没有修改广告。现有规则核对是当前的保守文字检查，不代表模拟了所有拼写、语言变化或历史生效情况。</p><p><a href="analysis.json">查看完整结构化依据</a></p></details>
</main><script>{HANDOFF_JS}</script></body></html>"""


def generate(bundle: Json, destination: Path) -> Json:
    result = analyze(bundle)
    # Analyze completely before creating outputs. A fresh directory preserves prior reports.
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    (destination / "report.html").write_text(render(result), encoding="utf-8")
    write_csv(
        destination / "negative-review.csv",
        result["negative_review"],
        [
            "campaign_id",
            "campaign_name",
            "term",
            "negative_text",
            "match_type",
            "scope",
            "state",
            "current_cost_micros",
            "current_clicks",
            "current_conversions",
            "current_all_conversions",
            "history_cost_micros",
            "history_clicks",
            "history_conversions",
            "history_all_conversions",
            "history_period",
            "business_fit",
            "business_rationale",
            "coverage",
            "evidence_refs",
            "reasons",
            "execution_allowed",
        ],
    )
    write_csv(
        destination / "measurement-review.csv",
        result["measurement"]["actions"],
        [
            "id",
            "name",
            "status",
            "primary",
            "category",
            "origin",
            "type",
            "counting_type",
            "click_through_lookback_window_days",
            "attribution_model",
            "applied_custom_goal_campaigns",
            "always_use_default_value",
            "default_value",
            "has_performance_row",
            "conversions",
            "conversions_value",
            "all_conversions",
            "business_success",
            "front_end_trigger",
            "platform_record",
            "counting_attribution",
            "backend_success",
            "crm_quality_revenue",
        ],
    )
    (destination / "receipt.json").write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "version": "0.2",
                "files": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in destination.iterdir()
                    if p.is_file()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    try:
        result = generate(
            json.loads(args.bundle.read_text(encoding="utf-8-sig")),
            args.output_directory,
        )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, f"Report failed: {exc}\n")
    print(
        json.dumps(
            {
                "report": str(args.output_directory / "report.html"),
                "review_rows": len(result["negative_review"]),
                "execution_allowed": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
