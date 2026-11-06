#!/usr/bin/env python3
"""Validate captured B2B acquisition evidence and render a bounded local review.

No network, authentication, advertising mutations, or third-party dependencies.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Obj: TypeAlias = dict[str, object]
METRICS = {
    "ads": {
        "cost_micros",
        "clicks",
        "impressions",
        "platform_conversions",
        "all_conversions",
        "platform_value",
    },
    "ga4": {
        "sessions",
        "totalUsers",
        "engagedSessions",
        "keyEvents",
        "sessionKeyEventRate",
        "engagementRate",
        "screenPageViews",
        "eventCount",
        "bounceRate",
        "averageSessionDuration",
    },
    "gsc": {"clicks", "impressions", "ctr", "position"},
}
INTEGERS = {
    "cost_micros",
    "clicks",
    "impressions",
    "sessions",
    "totalUsers",
    "engagedSessions",
    "screenPageViews",
    "eventCount",
}
RATES = {"ctr", "sessionKeyEventRate", "engagementRate", "bounceRate"}
CONTEXT = ("identity", "scope", "timezone", "currency", "date_basis")
LABELS = {
    "cost": "广告费用",
    "clicks": "点击",
    "impressions": "曝光",
    "cpc": "平均点击费用",
    "platform_conversions": "平台转化（非合格询盘）",
    "all_conversions": "所有平台转化",
    "platform_value": "平台转化价值（非真实收入）",
    "platform_cpa": "平台 CPA",
    "sessions": "会话",
    "totalUsers": "周期去重用户",
    "engagedSessions": "互动会话",
    "keyEvents": "关键事件次数",
    "sessionKeyEventRate": "有关键事件的会话比例",
    "ctr": "点击率",
    "position": "平均排名",
    "engagementRate": "互动率",
    "screenPageViews": "页面浏览次数",
    "eventCount": "事件次数（非去重询盘）",
    "bounceRate": "跳出率",
    "averageSessionDuration": "平均会话时长（秒）",
}


class Invalid(ValueError):
    """Input cannot support the requested calculation."""


def obj(value: object, label: str) -> Obj:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise Invalid(f"{label}: expected an object")
    return {str(k): v for k, v in value.items()}


def unique_object(pairs: list[tuple[str, object]]) -> Obj:
    result: Obj = {}
    for key, value in pairs:
        if key in result:
            raise Invalid(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def arr(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise Invalid(f"{label}: expected an array")
    return list(value)


def string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{label}: expected nonempty text")
    return value


def number(value: object, label: str, *, negative: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{label}: expected number or null; never coerce blanks to zero")
    n = float(value)
    if not math.isfinite(n) or (not negative and n < 0):
        raise Invalid(f"{label}: invalid numeric value")
    return n


def period(value: object) -> tuple[date, date]:
    p = obj(value, "period")
    try:
        a, b = (date.fromisoformat(string(p.get(k), k)) for k in ("start", "end"))
    except ValueError as exc:
        raise Invalid("period: invalid calendar date") from exc
    if a > b:
        raise Invalid("period: start is after end")
    return a, b


def ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b is not None and b != 0 else None


def delta(a: float | None, b: float | None) -> Obj:
    return {
        "absolute": a - b if a is not None and b is not None else None,
        "relative": ratio(a - b, b) if a is not None and b is not None else None,
        "basis": "missing"
        if a is None or b is None
        else "zero_baseline"
        if b == 0
        else "comparable",
    }


def metrics(value: object, system: str) -> dict[str, float | None]:
    raw = obj(value, "metrics")
    if not raw or set(raw) - METRICS[system]:
        raise Invalid(
            f"{system}: empty or unknown metric keys: {set(raw) - METRICS[system]}"
        )
    result = {k: number(v, k) for k, v in raw.items()}
    for k, v in result.items():
        if v is not None and (
            (k in INTEGERS and not v.is_integer()) or (k in RATES and v > 1)
        ):
            raise Invalid(f"{k}: incorrect count/rate scale")
    if system == "gsc":
        c, i = result.get("clicks"), result.get("impressions")
        if c is not None and i is not None and c > i:
            raise Invalid("gsc: clicks exceed impressions")
        r = result.get("ctr")
        if (
            c is not None
            and i
            and r is not None
            and not math.isclose(c / i, r, abs_tol=1e-6)
        ):
            raise Invalid("gsc: CTR disagrees with clicks/impressions")
    return result


def display_metrics(system: str, raw: object) -> dict[str, float | None]:
    m = metrics(raw, system)
    if system == "ads":
        micros = m.pop("cost_micros", None)
        m["cost"] = micros / 1_000_000 if micros is not None else None
        m["cpc"] = ratio(m["cost"], m.get("clicks"))
        m["platform_cpa"] = ratio(m["cost"], m.get("platform_conversions"))
        m["ctr"] = ratio(m.get("clicks"), m.get("impressions"))
    return m


def verify_evidence(packet: Obj, base: Path) -> set[str]:
    entries = obj(packet.get("evidence"), "evidence")
    if not entries:
        raise Invalid("evidence: no captured requests/responses supplied")
    for name, value in entries.items():
        e = obj(value, name)
        relative = Path(string(e.get("file"), "evidence.file"))
        path = (base / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(base.resolve()):
            raise Invalid(
                "evidence files must remain inside the private input directory"
            )
        if not path.is_file():
            raise Invalid(f"{name}: evidence file not found")
        if hashlib.sha256(path.read_bytes()).hexdigest() != e.get("sha256"):
            raise Invalid(f"{name}: evidence digest mismatch")
    return set(entries)


def refs(value: object, known: set[str], label: str) -> None:
    ids = arr(value, label)
    if not ids or any(not isinstance(v, str) or v not in known for v in ids):
        raise Invalid(f"{label}: missing or unknown evidence reference")


def classify_brand(query: str, rules: object) -> str:
    if rules is None:
        return "unknown"
    items = arr(rules, "brand_rules")
    if not items:
        return "unknown"
    matched = False
    for item in items:
        rule = obj(item, "brand rule")
        term = string(rule.get("text"), "brand text").casefold()
        mode = rule.get("match")
        if mode not in ("exact", "contains"):
            raise Invalid("brand match must be exact or contains")
        matched |= (
            query.casefold() == term if mode == "exact" else term in query.casefold()
        )
    return "brand" if matched else "nonbrand"


def validate_report(report: Obj, system: str, evidence_ids: set[str]) -> None:
    name = string(report.get("name"), "report.name")
    dims = arr(report.get("dimensions"), "dimensions")
    if (
        not dims
        or any(not isinstance(d, str) or not d for d in dims)
        or len(set(dims)) != len(dims)
    ):
        raise Invalid("dimensions must be distinct strings")
    if name == "search_terms" and dims != ["campaign_id", "query"]:
        raise Invalid(
            "search_terms: aggregate to campaign_id + original query before review"
        )
    if name == "campaigns" and dims != ["campaign_id"]:
        raise Invalid("campaigns: expected one row per campaign_id")
    obj(report.get("scope"), "report.scope")
    if report.get("complete") is not None and not isinstance(
        report.get("complete"), bool
    ):
        raise Invalid("report.complete must be true/false/null")
    refs(report.get("evidence_refs"), evidence_ids, f"{name}.evidence_refs")
    seen: set[tuple[str, ...]] = set()
    schema: set[str] | None = None
    rows = arr(report.get("rows"), "rows")
    count = report.get("provider_row_count")
    if count is not None:
        n = number(count, "provider_row_count")
        if n is None or not n.is_integer() or n < len(rows):
            raise Invalid("invalid provider_row_count")
        if report.get("complete") is True and n != len(rows):
            raise Invalid("complete report has missing provider rows")
    for value in rows:
        row = obj(value, "row")
        key = arr(row.get("key"), "row.key")
        if len(key) != len(dims) or any(not isinstance(k, str) for k in key):
            raise Invalid("row.key does not match dimensions")
        k = tuple(str(v) for v in key)
        if k in seen:
            raise Invalid(
                f"{name}: duplicate grain; aggregate explicitly, never last-row-wins"
            )
        seen.add(k)
        m = metrics(row.get("metrics"), system)
        if schema is not None and schema != set(m):
            raise Invalid(
                "inconsistent row metric columns; missing must be explicit null"
            )
        schema = set(m)


def compare_reports(current: Obj, previous: Obj) -> Obj:
    result: Obj = {}
    old = {
        string(obj(r, "report").get("name"), "name"): obj(r, "report")
        for r in arr(previous.get("reports", []), "reports")
    }
    for raw in arr(current.get("reports", []), "reports"):
        r = obj(raw, "report")
        name = string(r.get("name"), "name")
        if name not in old:
            continue
        p = old[name]
        if r["dimensions"] != p["dimensions"] or r["scope"] != p["scope"]:
            raise Invalid(f"{name}: report scope or grain changed between periods")

        def indexed(report: Obj) -> dict[tuple[str, ...], Obj]:
            return {
                tuple(str(x) for x in arr(obj(v, "row")["key"], "key")): obj(v, "row")
                for v in arr(report["rows"], "rows")
            }

        a, b = indexed(r), indexed(p)
        union = sorted(a.keys() | b.keys())
        rows: list[Obj] = []
        for key in union:
            am = obj(a[key]["metrics"], "metrics") if key in a else {}
            bm = obj(b[key]["metrics"], "metrics") if key in b else {}
            if key in a and key in b and set(am) != set(bm):
                raise Invalid(f"{name}: metric schema changed")
            rows.append(
                {
                    "key": list(key),
                    "current_present": key in a,
                    "previous_present": key in b,
                    "changes": {
                        k: delta(number(am.get(k), k), number(bm.get(k), k))
                        for k in am.keys() | bm.keys()
                    },
                }
            )
        result[name] = {
            "union_rows": len(union),
            "current_rows": len(a),
            "previous_rows": len(b),
            "one_sided_rows": len(a.keys() ^ b.keys()),
            "rows": rows,
        }
    return result


def economic_result(packet: Obj, ads: Obj | None, evidence_ids: set[str]) -> Obj:
    blocked: Obj = {
        "status": "unavailable",
        "cpql": None,
        "real_revenue_roas": None,
        "ad_contribution_roi": None,
    }
    crm = obj(packet.get("crm", {"status": "missing"}), "crm")
    if crm.get("status") != "observed" or ads is None:
        return {
            **blocked,
            "reason": "缺少真实且同口径的销售确认队列；平台转化价值不能代替收入。",
        }
    refs(crm.get("evidence_refs"), evidence_ids, "crm.evidence_refs")
    binding = obj(crm.get("binding"), "crm.binding")
    if any(binding.get(k) != ads.get(k) for k in (*CONTEXT, "period")):
        raise Invalid(
            "CRM cohort/identity/currency/scope does not match the Ads cost cohort"
        )
    flags = obj(crm.get("checks"), "crm.checks")
    if any(
        flags.get(k) is not True
        for k in (
            "attribution_verified",
            "deduplicated",
            "sales_validated",
            "cohort_mature",
        )
    ):
        return {**blocked, "reason": "销售真实性、去重、归因或队列成熟度尚未核验。"}
    counts = {
        k: number(crm.get(k), k)
        for k in ("valid_inquiries", "qualified_inquiries", "pending_reviews")
    }
    for k, v in counts.items():
        if v is None or not v.is_integer():
            raise Invalid(f"CRM {k}: needs a verified integer count")
    valid, qualified = counts["valid_inquiries"], counts["qualified_inquiries"]
    if valid is not None and qualified is not None and qualified > valid:
        raise Invalid("qualified inquiries exceed valid inquiries")
    if counts["pending_reviews"] != 0:
        return {**blocked, "reason": "还有未判定询盘，暂不输出最终质量经济性。"}
    cost = display_metrics("ads", ads["total"])["cost"]
    revenue = number(crm.get("net_revenue"), "net_revenue", negative=True)
    profit = number(crm.get("gross_profit"), "gross_profit", negative=True)
    return {
        "status": "observed_cohort",
        "cpql": ratio(cost, qualified),
        "real_revenue_roas": ratio(revenue, cost),
        "ad_contribution_roi": ratio(profit - cost, cost)
        if profit is not None and cost is not None
        else None,
        "reason": "仅对应输入中已核验队列；ROI 为扣广告费后的毛利贡献，非公司净利润。",
    }


def analyze(packet: Obj, base: Path) -> Obj:
    if packet.get("schema_version") != 1:
        raise Invalid("unsupported schema_version")
    evidence_ids = verify_evidence(packet, base)
    expected = obj(packet.get("expected"), "expected")
    sources = arr(packet.get("sources"), "sources")
    if not sources:
        raise Invalid("sources cannot be empty")
    by_system: dict[str, dict[str, Obj]] = {}
    warnings: list[str] = []
    source_ids: set[str] = set()
    for value in sources:
        s = obj(value, "source")
        system = string(s.get("system"), "system")
        if system not in METRICS:
            raise Invalid("unknown source system")
        sid = string(s.get("id"), "source.id")
        if sid in source_ids:
            raise Invalid("duplicate source.id")
        source_ids.add(sid)
        target = obj(expected.get(system), "expected identity")
        for k in CONTEXT:
            if k not in s or k not in target or s[k] != target[k]:
                raise Invalid(f"{sid}: {k} does not match the verified target")
        string(s["identity"], "identity")
        scope = obj(s["scope"], "scope")
        if system == "ga4":
            string(scope.get("hostname"), "GA4 production hostname")
        if system == "gsc":
            if scope.get("type") not in (
                "web",
                "image",
                "video",
                "news",
                "discover",
                "googleNews",
            ):
                raise Invalid("GSC search type must be explicit")
            string(scope.get("aggregation_type"), "GSC aggregation_type")
        try:
            ZoneInfo(string(s["timezone"], "timezone"))
        except ZoneInfoNotFoundError as exc:
            raise Invalid(
                "unknown timezone or missing IANA timezone data; on Windows install tzdata in the project Python environment"
            ) from exc
        if (
            s["date_basis"]
            != {
                "ads": "interaction_date",
                "ga4": "property_date",
                "gsc": "pacific_date",
            }[system]
        ):
            raise Invalid(
                "unsupported date basis; do not mix conversion-date and acquisition cohorts"
            )
        if system == "ads" and not re.fullmatch(r"[A-Z]{3}", str(s["currency"])):
            raise Invalid("Ads currency must be an ISO-style code")
        period(s.get("period"))
        refs(s.get("evidence_refs"), evidence_ids, f"{sid}.evidence_refs")
        role = string(s.get("role"), "role")
        if role not in ("current", "previous") or role in by_system.setdefault(
            system, {}
        ):
            raise Invalid("duplicate or invalid source role")
        by_system[system][role] = s
        m = metrics(s.get("total"), system)
        required = {
            "ads": {"cost_micros", "clicks", "impressions"},
            "ga4": {"sessions", "totalUsers"},
            "gsc": {"clicks", "impressions", "ctr", "position"},
        }[system]
        if not required.issubset(m):
            raise Invalid(f"{sid}: missing required independent total columns")
        if s.get("finality") not in ("settled", "provisional", "unknown"):
            raise Invalid("finality must be settled/provisional/unknown")
        if s["finality"] != "settled":
            warnings.append(f"{sid}: 数据成熟度为 {s['finality']}，趋势仅作暂时观察。")
        meta = obj(s.get("metadata", {}), "metadata")
        for flags in (
            ("subjectToThresholding", "subject_to_thresholding"),
            ("dataLossFromOtherRow", "data_loss_from_other_row"),
            ("samplingMetadatas", "sampling_metadatas"),
            ("dataTruncationReasons", "data_truncation_reasons"),
        ):
            if any(meta.get(flag) for flag in flags):
                warnings.append(f"{sid}: 平台元数据提示 {flags[0]}，需保留数据限制。")
        incomplete = meta.get("firstIncompleteDate", meta.get("first_incomplete_date"))
        if incomplete:
            try:
                d = date.fromisoformat(str(incomplete))
            except ValueError as exc:
                raise Invalid("invalid firstIncompleteDate") from exc
            if d <= period(s["period"])[1]:
                if s["finality"] == "settled":
                    raise Invalid(
                        "source claims settled but provider marks incomplete dates"
                    )
                warnings.append(f"{sid}: 平台标注从 {d} 起未完成。")
        if any(v is None for v in m.values()):
            warnings.append(f"{sid}: 独立总计存在缺失值，相关计算保留未知。")
        names: set[str] = set()
        for raw in arr(s.get("reports", []), "reports"):
            r = obj(raw, "report")
            validate_report(r, system, evidence_ids)
            name = string(r["name"], "name")
            if name in names:
                raise Invalid("duplicate report name")
            names.add(name)
            rows = [obj(v, "row") for v in arr(r["rows"], "rows")]
            if r.get("complete") is not True:
                warnings.append(f"{sid}/{name}: 返回行仅作有界样本，不代表全量。")
            if system == "ads" and name == "campaigns" and r.get("complete") is True:
                if r["scope"] != s["scope"]:
                    raise Invalid(
                        "campaign report scope differs from independent total"
                    )
                for metric, total in m.items():
                    values = [
                        number(obj(v["metrics"], "metrics").get(metric), metric)
                        for v in rows
                    ]
                    if total is None or any(v is None for v in values):
                        warnings.append(f"{sid}/{name}: {metric} 有缺失，对账未完成。")
                    if total is not None and all(v is not None for v in values):
                        summed = sum(v for v in values if v is not None)
                        if not math.isclose(summed, total, rel_tol=1e-8, abs_tol=1e-5):
                            raise Invalid(
                                f"{sid}: campaign sum differs from independent {metric}"
                            )
    summary: Obj = {}
    for system, roles in by_system.items():
        if "current" not in roles:
            raise Invalid("each system needs a current report")
        c = roles["current"]
        item: Obj = {
            "identity": c["identity"],
            "scope": c["scope"],
            "currency": c["currency"],
            "timezone": c["timezone"],
            "date_basis": c["date_basis"],
            "period": c["period"],
            "current": display_metrics(system, c["total"]),
        }
        if "previous" in roles:
            p = roles["previous"]
            ca, cb = period(c["period"])
            pa, pb = period(p["period"])
            if pb >= ca or cb - ca != pb - pa:
                raise Invalid(
                    "comparison windows overlap, are reversed, or differ in length"
                )
            if ca.weekday() != pa.weekday():
                warnings.append(f"{system}: 两期星期结构不同，不能忽略工作日影响。")
            cm, pm = (
                display_metrics(system, c["total"]),
                display_metrics(system, p["total"]),
            )
            if set(cm) != set(pm):
                raise Invalid("total metric schema changed between periods")
            item.update(
                {
                    "previous_period": p["period"],
                    "previous": pm,
                    "changes": {k: delta(cm[k], pm[k]) for k in cm},
                    "reports": compare_reports(c, p),
                }
            )
        summary[system] = item
    actions = arr(packet.get("actions", []), "actions")
    if len(actions) > 3:
        raise Invalid("show at most three priority actions")
    for value in actions:
        a = obj(value, "action")
        for k in (
            "title",
            "fact",
            "hypothesis",
            "action",
            "owner",
            "verification",
            "stop_rule",
        ):
            string(a.get(k), k)
        refs(a.get("source_ids"), source_ids, "action.source_ids")
    if not actions:
        warnings.append("尚未填写任务相关行动：当前仅为计算骨架，业务分析未完成。")
    economy = economic_result(
        packet, by_system.get("ads", {}).get("current"), evidence_ids
    )
    if economy["status"] == "unavailable":
        warnings.append(str(economy["reason"]))
    conversion_comparability: Obj = {"status": "not_applicable"}
    if "ads" in by_system:
        conversion_comparability = obj(
            packet.get("conversion_comparability", {"status": "unknown"}),
            "conversion_comparability",
        )
        status = conversion_comparability.get("status")
        if status not in ("unknown", "changed", "verified_same"):
            raise Invalid(
                "conversion comparability must be unknown/changed/verified_same"
            )
        if status != "unknown":
            refs(
                conversion_comparability.get("evidence_refs"),
                evidence_ids,
                "conversion_comparability.evidence_refs",
            )
            string(
                conversion_comparability.get("reason"),
                "conversion comparability reason",
            )
        if status != "verified_same":
            warnings.append(
                "Ads 跨期转化口径已变化或未核验；转化与平台 CPA 差值只作算术记录，"
                "不能据此判断效率。按同一动作 ID 的 All conversions、目标历史和成熟度复核。"
            )
    return {
        "schema_version": 1,
        "title": packet.get("title", "外贸 B2B 广告分析"),
        "status": "validated_evidence_packet",
        "systems": summary,
        "economics": economy,
        "warnings": warnings,
        "actions": actions,
        "conversion_comparability": conversion_comparability,
        "boundary": "校验通过只代表输入与计算合同通过，不证明采集、销售断言、因果或投放收益。",
    }


def safe_csv(value: object) -> str:
    s = str(value)
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: object) -> str:
    if value is None:
        return "—（缺失或分母为零）"
    if isinstance(value, (float, int)):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return esc(value)


def metric_text(key: str, value: object) -> str:
    if isinstance(value, (float, int)):
        if key in RATES or key == "ad_contribution_roi":
            return f"{value:.2%}"
        if key in {"cost", "cpc", "platform_cpa", "cpql", "platform_value"}:
            return f"{value:,.2f}"
    return fmt(value)


def render(result: Obj) -> str:
    parts = [
        f"<h1>{esc(result['title'])}</h1>",
        '<p class="intro">费用、网站行为与询盘质量分别看；先确认能信的证据，再决定下一步。</p>',
    ]
    parts.append('<section><h2>优先行动</h2><div class="cards">')
    for value in arr(result["actions"], "actions"):
        a = obj(value, "action")
        parts.append(f"<article><h3>{esc(a['title'])}</h3>")
        for key, label in (
            ("fact", "事实"),
            ("hypothesis", "推断与缺证"),
            ("action", "下一步"),
            ("owner", "负责人"),
            ("verification", "如何验证"),
            ("stop_rule", "停止条件"),
        ):
            parts.append(f"<p><b>{label}</b> {esc(a[key])}</p>")
        parts.append(f"<small>证据：{esc(a['source_ids'])}</small></article>")
    parts.append("</div></section><section><h2>读取范围与独立总计</h2>")
    for system, value in obj(result["systems"], "systems").items():
        s = obj(value, "system")
        p, pp = obj(s["period"], "period"), obj(s.get("previous_period", {}), "period")
        parts.append(f"<h3>{esc(system.upper())} · {esc(s['identity'])}</h3>")
        parts.append(
            f"<p>本期 {esc(p['start'])} — {esc(p['end'])}；对比 {esc(pp.get('start', '未提供'))} — {esc(pp.get('end', ''))}<br>时区 {esc(s['timezone'])} · 币种 {esc(s['currency'])} · 日期依据 {esc(s['date_basis'])}</p>"
        )
        parts.append(
            f"<details><summary>查看过滤范围</summary><pre>{esc(json.dumps(s['scope'], ensure_ascii=False))}</pre></details>"
        )
        parts.append(
            '<div class="table"><table><thead><tr><th>指标</th><th>本期</th><th>前期</th><th>变化</th></tr></thead><tbody>'
        )
        previous = obj(s.get("previous", {}), "previous")
        changes = obj(s.get("changes", {}), "changes")
        for k, v in obj(s["current"], "current").items():
            change = obj(changes.get(k, {}), "change")
            rel = change.get("relative")
            change_text = f"{rel:+.1%}" if isinstance(rel, (float, int)) else "—"
            absolute = change.get("absolute")
            if k == "position" and isinstance(absolute, (float, int)):
                direction = (
                    "更靠后" if absolute > 0 else "更靠前" if absolute < 0 else "不变"
                )
                change_text = f"{absolute:+.2f} 位（{direction}）"
            elif k in RATES and isinstance(absolute, (float, int)):
                change_text = f"{absolute * 100:+.2f} 个百分点"
            if system == "ads" and k in {
                "platform_conversions",
                "all_conversions",
                "platform_cpa",
                "platform_value",
            }:
                comparability = obj(
                    result.get("conversion_comparability", {}), "comparability"
                )
                if comparability.get("status") != "verified_same":
                    change_text = "口径变化或未核验，暂不判断效率"
            parts.append(
                f"<tr><td>{esc(LABELS.get(k, k))}</td><td>{metric_text(k, v)}</td><td>{metric_text(k, previous.get(k))}</td><td>{change_text}</td></tr>"
            )
        parts.append("</tbody></table></div>")
    parts.append("</section>")
    e = obj(result["economics"], "economics")
    parts.append(
        f"<section><h2>询盘经济性</h2><p>{esc(e['reason'])}</p><p>合格询盘成本：{metric_text('cpql', e['cpql'])} · 真实收入 ROAS：{fmt(e['real_revenue_roas'])} · 广告贡献 ROI：{metric_text('ad_contribution_roi', e['ad_contribution_roi'])}</p></section>"
    )
    parts.append("<section><h2>需要保留的限制</h2><ul>")
    parts.extend(f"<li>{esc(w)}</li>" for w in arr(result["warnings"], "warnings"))
    parts.append(
        f"</ul><p>{esc(result['boundary'])}</p><p>建议尚未执行；本报告不包含保证收益或已实现节省。</p></section>"
    )
    css = "body{margin:0;background:#f5f3ed;color:#1d302d;font:16px/1.65 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:40px 24px}h1{font-size:clamp(28px,4vw,46px);line-height:1.2}h2{margin-top:32px}h3{margin-top:0}.intro{font-size:19px;color:#50635b}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:16px}article,section{background:white;padding:22px;border-radius:14px;margin:18px 0}article{background:#edf4ef;margin:0}article p{font-size:14px}b{display:block}small,pre{overflow-wrap:anywhere;white-space:pre-wrap}.table{overflow:auto}table{border-collapse:collapse;width:100%;min-width:530px}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:right}th:first-child,td:first-child{text-align:left}li{margin-bottom:8px}@media(max-width:500px){main{padding:20px 12px}section{padding:16px}}@media print{body{background:white}main{padding:0}.cards{display:block}article{break-inside:avoid}.table{overflow:visible}table{min-width:0;font-size:12px}details{display:none}}"
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>外贸 B2B 广告分析</title><style>'
        + css
        + "</style><body><main>"
        + "".join(parts)
        + "</main></body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise Invalid("output directory exists; choose a new run directory")
        packet = obj(
            json.loads(
                args.input.read_text(encoding="utf-8"), object_pairs_hook=unique_object
            ),
            "packet",
        )
        result = analyze(packet, args.input.parent)
        args.out.mkdir(parents=True, mode=0o700)
        (args.out / "review.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        (args.out / "report.html").write_text(render(result), encoding="utf-8")
        fields = [
            "title",
            "fact",
            "hypothesis",
            "action",
            "owner",
            "verification",
            "stop_rule",
            "source_ids",
        ]
        with (args.out / "actions.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for a in arr(result["actions"], "actions"):
                row = obj(a, "action")
                writer.writerow({k: safe_csv(row[k]) for k in fields})
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "sources": list(obj(result["systems"], "systems")),
                    "warnings": len(arr(result["warnings"], "warnings")),
                    "output": str(args.out),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (Invalid, OSError, json.JSONDecodeError) as exc:
        print(f"Cannot review: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
