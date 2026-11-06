"""Offline evidence checks. No credentials, network calls or account mutations."""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Json = dict[str, Any]
ADDITIVE = {
    "cost_micros",
    "clicks",
    "impressions",
    "conversions",
    "conversions_value",
    "all_conversions",
}
INTEGERS = {"cost_micros", "clicks", "impressions"}


class EvidenceError(ValueError):
    """The claimed evidence cannot safely support aggregation."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EvidenceError(message)


def number(value: object) -> Decimal:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvidenceError("Expected a JSON number")
    require(math.isfinite(float(value)), "Non-finite metric")
    return Decimal(str(value))


def text_field(data: Json, key: str) -> str:
    value = data.get(key)
    require(isinstance(value, str) and bool(value.strip()), f"Missing text: {key}")
    return str(value)


def checked_date(value: object) -> date:
    require(isinstance(value, str), "Date must be YYYY-MM-DD")
    parsed = date.fromisoformat(str(value))
    require(parsed.isoformat() == value, "Date must be YYYY-MM-DD")
    return parsed


def validate_dataset(data: Json) -> list[str]:
    require(data.get("schema_version") in {"0.1", "0.2"}, "Unsupported schema")
    for key in ("evidence_id", "customer_id", "currency", "timezone", "resource"):
        text_field(data, key)
    require(
        data.get("source") in {"google_ads_mcp", "user_export", "synthetic"},
        "Unknown source",
    )
    require(
        data.get("completeness") in {"complete", "partial", "unknown"},
        "Unknown completeness",
    )
    require(
        data.get("visibility") in {"platform_reported", "partial", "unknown"},
        "Unknown visibility",
    )
    fetched = datetime.fromisoformat(text_field(data, "retrieved_at"))
    require(fetched.utcoffset() is not None, "retrieved_at needs a timezone offset")
    require(
        bool(re.fullmatch(r"[A-Z]{3}", data["currency"])),
        "Currency must have an ISO 4217 code shape",
    )
    try:
        account_zone = ZoneInfo(data["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise EvidenceError(
            "Unknown timezone or missing system timezone data; check IANA name and tzdata"
        ) from exc
    period = data.get("period")
    if not isinstance(period, dict):
        raise EvidenceError("Missing period")
    start, end = checked_date(period.get("start")), checked_date(period.get("end"))
    require(start <= end, "Reversed date range")
    require(
        end <= fetched.astimezone(account_zone).date(),
        "Period extends beyond retrieval date in account timezone",
    )
    require(
        period.get("date_basis") in {"interaction_date", "conversion_date"},
        "Unknown date basis",
    )
    grain, metrics, rows = data.get("grain"), data.get("metrics"), data.get("rows")
    if not isinstance(grain, list) or not grain:
        raise EvidenceError("Declare grain")
    require(all(isinstance(k, str) and k for k in grain), "Invalid grain")
    require(len(set(grain)) == len(grain), "Duplicate grain fields")
    if not isinstance(metrics, list) or not metrics:
        raise EvidenceError("Declare metrics")
    require(
        all(isinstance(k, str) and k in ADDITIVE for k in metrics),
        "Only supported additive metrics",
    )
    require(len(set(metrics)) == len(metrics), "Duplicate metric fields")
    require(not set(metrics).intersection(grain), "Metric cannot be grain")
    if not isinstance(rows, list):
        raise EvidenceError("Rows must be an array")
    cap = data.get("limit")
    require(
        cap is None or (isinstance(cap, int) and not isinstance(cap, bool) and cap > 0),
        "Invalid limit",
    )
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        require(isinstance(row, dict), "Row must be an object")
        for context_key in ("customer_id", "currency", "timezone"):
            if context_key in row:
                require(
                    str(row[context_key]) == data[context_key],
                    f"Mixed context: {context_key}",
                )
        for key in grain:
            value = row.get(key)
            require(
                isinstance(value, (int, str))
                and not isinstance(value, bool)
                and str(value) != "",
                "Missing grain key",
            )
        row_key = tuple(str(row[k]) for k in grain)
        require(
            row_key not in seen,
            "Duplicate grain; aggregate all segments before analysis",
        )
        seen.add(row_key)
        if "date" in grain:
            require(
                start <= checked_date(row["date"]) <= end, "Row date outside period"
            )
        for metric in metrics:
            require(
                metric in row,
                f"Missing metric key: {metric}; represent unavailable as null",
            )
            if row[metric] is None:
                continue
            val = number(row[metric])
            if metric in INTEGERS:
                require(
                    val >= 0 and val == val.to_integral_value(),
                    f"Invalid count/micros: {metric}",
                )
    warnings: list[str] = []
    if cap is not None and len(rows) >= cap:
        warnings.append("ROW_LIMIT_REACHED")
    if data["completeness"] != "complete":
        warnings.append("INCOMPLETE_EXTRACTION")
    if not rows:
        warnings.append("EMPTY_IS_NOT_ZERO")
    if any(row[m] is None for row in rows for m in metrics):
        warnings.append("MISSING_METRICS")
    return warnings


def summarize(data: Json) -> Json:
    warnings = validate_dataset(data)
    rows = data["rows"]
    totals: dict[str, int | float | None] = {}
    for metric in data["metrics"]:
        # Strict propagation: incomplete numerator is never silently zero-filled.
        value = (
            None
            if not rows or any(r[metric] is None for r in rows)
            else sum((number(r[metric]) for r in rows), Decimal(0))
        )
        totals[metric] = (
            None
            if value is None
            else (int(value) if metric in INTEGERS else float(value))
        )
    cost = totals.get("cost_micros")
    totals["cost"] = None if cost is None else cost / 1_000_000

    def ratio(a: str, b: str) -> float | None:
        numerator, denominator = totals.get(a), totals.get(b)
        return (
            None
            if numerator is None or denominator is None or denominator <= 0
            else numerator / denominator
        )

    complete = not any(
        x in warnings
        for x in ("ROW_LIMIT_REACHED", "INCOMPLETE_EXTRACTION", "EMPTY_IS_NOT_ZERO")
    )
    return {
        "evidence_id": data["evidence_id"],
        "rows": len(rows),
        "reported_subset_totals": totals,
        "cpc": ratio("cost", "clicks"),
        "ctr": ratio("clicks", "impressions"),
        "platform_value_per_cost": ratio("conversions_value", "cost"),
        "extraction_complete": complete,
        "visibility": data["visibility"],
        "warnings": warnings,
        "business_roi": None,
        "duplicate_tracking_proven": False,
    }


def inventory_coverage(data: Json) -> Json:
    configured, reported = data.get("configured"), data.get("reported")
    if not isinstance(configured, list) or not isinstance(reported, list):
        raise EvidenceError("Need two independent inventories")
    for rows in (configured, reported):
        require(all(isinstance(r, dict) for r in rows), "Invalid inventory row")
        ids = [text_field(r, "id") for r in rows]
        require(
            len(ids) == len(set(ids)),
            "Duplicate action IDs; aggregate performance first",
        )
    for action in configured:
        require(
            action.get("status") in {"ENABLED", "REMOVED", "HIDDEN"},
            "Unknown action status",
        )
        require(isinstance(action.get("primary"), bool), "Unknown primary flag")
    enabled = {r["id"] for r in configured if r["status"] == "ENABLED"}
    observed = {r["id"] for r in reported}
    configured_ids = {r["id"] for r in configured}
    return {
        "enabled_actions": len(enabled),
        "reported_actions": len(observed),
        "enabled_not_reported": sorted(enabled - observed),
        "reported_not_configured": sorted(observed - configured_ids),
        "missing_performance_value": None,
        "meaning": "coverage only; absent rows are not zero and do not prove tracking failure",
    }


def negative_gate(data: Json) -> Json:
    fit = data.get("business_fit")
    require(fit in {"core", "outside", "unknown"}, "Unknown business fit")
    for key in ("protected", "inventory_complete", "already_covered"):
        require(isinstance(data.get(key), bool), f"Need explicit boolean: {key}")
    require(
        data.get("match_type") in {"EXACT", "PHRASE", "BROAD"},
        "Unknown negative match type",
    )
    require(
        data.get("scope") in {"campaign", "ad_group", "shared_list", "account"},
        "Invalid negative scope",
    )
    history = data.get("history_conversions")
    historical = None if history is None else number(history)
    reasons: list[str] = []
    state = "REVIEW_ONLY"
    registry = data.get("evidence_registry")
    inventory_evidence = (
        registry.get(data.get("inventory_ref")) if isinstance(registry, dict) else None
    )
    coverage_verified = isinstance(inventory_evidence, dict) and (
        inventory_evidence.get("role") == "inventory"
        and inventory_evidence.get("customer_id") == data.get("customer_id")
        and str(data.get("campaign_id")) in inventory_evidence.get("campaign_ids", [])
        and inventory_evidence.get("status") == "verified"
    )
    if fit == "core" or data["protected"]:
        state, reasons = "KEEP", ["BUSINESS_PROTECTION"]
    elif (
        data["already_covered"]
        and data["inventory_complete"]
        and isinstance(data.get("inventory_ref"), str)
        and data["inventory_ref"].strip()
        and coverage_verified
    ):
        state, reasons = "ALREADY_COVERED", ["NO_DUPLICATE_ADDITION"]
    else:
        term, negative = data.get("term"), data.get("negative_text")
        if (
            not isinstance(term, str)
            or not term.strip()
            or not isinstance(negative, str)
            or not negative.strip()
        ):
            reasons.append("PROPOSAL_TEXT_MISSING")
        elif term.strip().casefold() != negative.strip().casefold():
            reasons.append("PROPOSAL_DIFFERS_FROM_REVIEWED_TERM")
        if fit != "outside":
            reasons.append("BUSINESS_UNCONFIRMED")
        if historical is None:
            reasons.append("HISTORY_MISSING")
        elif historical != 0:
            reasons.append("HISTORICAL_CONVERSION_OR_ADJUSTMENT_REQUIRES_REVIEW")
        if not data["inventory_complete"]:
            reasons.append("INVENTORY_INCOMPLETE")
        for key in ("business_ref", "history_ref", "inventory_ref"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                reasons.append("MISSING_" + key.upper())
        refs = data.get("evidence_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(r, str) and r.strip() for r in refs)
        ):
            reasons.append("EVIDENCE_MISSING")
        if not isinstance(registry, dict):
            reasons.append("EVIDENCE_REGISTRY_MISSING")
        else:
            if isinstance(refs, list) and any(
                not isinstance(ref, str) or ref not in registry for ref in refs
            ):
                reasons.append("UNRESOLVED_EVIDENCE_REFERENCE")
            for role in ("business", "history", "inventory"):
                ref = data.get(role + "_ref")
                evidence = registry.get(ref) if isinstance(ref, str) else None
                if not isinstance(evidence, dict):
                    reasons.append("UNRESOLVED_" + role.upper() + "_EVIDENCE")
                    continue
                if evidence.get("role") != role or evidence.get(
                    "customer_id"
                ) != data.get("customer_id"):
                    reasons.append("WRONG_" + role.upper() + "_EVIDENCE_CONTEXT")
                if role in {"history", "inventory"} and str(
                    data.get("campaign_id")
                ) not in evidence.get("campaign_ids", []):
                    reasons.append("WRONG_" + role.upper() + "_CAMPAIGN")
                if role == "history" and term not in evidence.get("terms", []):
                    reasons.append("WRONG_HISTORY_TERM")
                if evidence.get("status") != "verified":
                    reasons.append("UNVERIFIED_" + role.upper() + "_EVIDENCE")
        if (
            data["scope"] != "campaign"
            or not str(data.get("campaign_id") or "").strip()
        ):
            reasons.append("SCOPE_NEEDS_SPECIAL_REVIEW")
        if data["match_type"] != "EXACT":
            reasons.append("MATCH_EXPANSION_NEEDS_SPECIAL_REVIEW")
        if reasons:
            state = "INVESTIGATE"
    return {
        "state": state,
        "reasons": reasons,
        "execution_allowed": False,
        "note": "References and business classifications still require actual evidence review.",
    }


def query_guard(data: Json) -> Json:
    """Check specific known query traps; not a replacement for API metadata."""
    resource = text_field(data, "resource")
    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        raise EvidenceError("Missing query fields")
    require(all(isinstance(f, str) for f in fields), "Invalid query fields")
    require(isinstance(data.get("includes_pmax"), bool), "Declare includes_pmax")
    conditions = data.get("conditions", [])
    require(
        isinstance(conditions, list) and all(isinstance(c, str) for c in conditions),
        "Invalid conditions",
    )
    require(
        not (data["includes_pmax"] and resource == "search_term_view"),
        "PMax requires campaign search term view",
    )
    require(
        not (
            data["includes_pmax"]
            and resource == "campaign_search_term_view"
            and (
                any(f.startswith("segments.keyword.") for f in fields)
                or any("segments.keyword." in c for c in conditions)
            )
        ),
        "Keyword segments silently remove PMax",
    )
    require(
        not (
            "segments.conversion_action" in fields and "metrics.cost_micros" in fields
        ),
        "Conversion action segmentation is incompatible with cost",
    )
    require(
        not any(
            re.search(r"\bDURING\s+(LAST_90_DAYS|LAST_12_MONTHS)\b", c, re.I)
            for c in conditions
        ),
        "Use explicit custom dates",
    )
    return {
        "known_traps_passed": True,
        "metadata_and_api_validation_still_required": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dataset", "inventory", "negative", "query"))
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8-sig"))
        require(isinstance(data, dict), "Input must be an object")
        handler = {
            "dataset": summarize,
            "inventory": inventory_coverage,
            "negative": negative_gate,
            "query": query_guard,
        }[args.mode]
        result = handler(data)
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        parser.exit(2, f"Evidence check failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
