"""Conservative literal negative coverage and overlapping n-gram observations."""

from __future__ import annotations

import re
from functools import lru_cache
from decimal import Decimal
from typing import Any

from evidence_guard import number, require

Json = dict[str, Any]
LAYERS = {"account", "campaign", "ad_group", "shared_lists", "positive_keywords"}


@lru_cache(maxsize=65536)
def tokens(text: str) -> tuple[str, ...] | None:
    # Non-Latin segmentation/punctuation needs semantic review; do not guess.
    return tuple(text.lower().split()) if re.fullmatch(r"[A-Za-z0-9 ]+", text) else None


def literal_match(term: str, negative: str, match_type: str) -> bool | None:
    require(
        isinstance(term, str)
        and bool(term.strip())
        and isinstance(negative, str)
        and bool(negative.strip()),
        "Empty term or negative text",
    )
    require(match_type in {"EXACT", "PHRASE", "BROAD"}, "Unknown match type")
    if term.strip().lower() == negative.strip().lower():
        return True
    t, n = tokens(term), tokens(negative)
    if t is None or n is None or len(t) > 16:
        return None
    if match_type == "EXACT":
        return t == n
    if match_type == "PHRASE":
        return any(t[i : i + len(n)] == n for i in range(len(t) - len(n) + 1))
    # Repeated-word semantics are not inferred from set membership.
    if len(set(n)) != len(n):
        return None
    return set(n).issubset(t)


def validate_inventory_rows(inventory: Json) -> None:
    """Validate before scope filtering so unrelated foreign rows cannot disappear."""
    for name in ("negatives", "shared_lists", "attachments", "positive_keywords"):
        require(isinstance(inventory.get(name), list), f"Missing inventory: {name}")
        for entry in inventory[name]:
            require(isinstance(entry, dict), f"Malformed {name} entry")
            if "customer_id" in entry:
                require(
                    str(entry["customer_id"]) == str(inventory.get("customer_id")),
                    "Foreign account in inventory row",
                )


def partition_inventory(inventory: Json, campaigns: set[str]) -> dict[str, Json]:
    """Keep identical coverage semantics without rescanning every account rule per term."""
    validate_inventory_rows(inventory)
    enabled_lists = {
        r["id"] for r in inventory["shared_lists"] if r.get("status") == "ENABLED"
    }
    result: dict[str, Json] = {}
    for campaign in campaigns:
        attached = {
            r["list_id"]
            for r in inventory["attachments"]
            if r.get("status") == "ENABLED" and r.get("campaign_id") == campaign
        }
        rules = []
        for rule in inventory["negatives"]:
            if rule.get("status") != "ENABLED":
                continue
            scope = rule.get("scope")
            require(
                scope in {"account", "campaign", "ad_group", "shared_list"},
                "Invalid negative scope",
            )
            if (
                scope == "account"
                or (
                    scope in {"campaign", "ad_group"}
                    and rule.get("campaign_id") == campaign
                )
                or (
                    scope == "shared_list"
                    and rule.get("list_id") in enabled_lists & attached
                )
            ):
                rules.append(rule)
        result[campaign] = dict(
            inventory,
            negatives=rules,
            positive_keywords=[
                r
                for r in inventory["positive_keywords"]
                if r.get("campaign_id") == campaign
            ],
        )
    return result


def coverage(term: str, campaign_id: str, inventory: Json) -> Json:
    complete = (
        inventory.get("complete") is True
        and set(inventory.get("layers_checked", [])) >= LAYERS
        and campaign_id in inventory.get("campaign_ids", [])
    )
    validate_inventory_rows(inventory)
    enabled_lists = {
        r["id"] for r in inventory["shared_lists"] if r.get("status") == "ENABLED"
    }
    attached = {
        r["list_id"]
        for r in inventory["attachments"]
        if r.get("status") == "ENABLED" and r.get("campaign_id") == campaign_id
    }
    full: list[str] = []
    partial: list[str] = []
    uncertain: list[str] = []
    for rule in inventory["negatives"]:
        if rule.get("status") != "ENABLED":
            continue
        scope = rule.get("scope")
        require(
            scope in {"account", "campaign", "ad_group", "shared_list"},
            "Invalid negative scope",
        )
        applies = (
            scope == "account"
            or (
                scope in {"campaign", "ad_group"}
                and rule.get("campaign_id") == campaign_id
            )
            or (
                scope == "shared_list"
                and rule.get("list_id") in enabled_lists & attached
            )
        )
        if not applies:
            continue
        matched = literal_match(term, str(rule["text"]), str(rule["match_type"]))
        if matched is None:
            uncertain.append(rule["id"])
        elif matched:
            (partial if scope == "ad_group" else full).append(rule["id"])
    return {
        "inventory_complete": complete,
        "literal_rule_present": bool(full),
        "rule_ids": sorted(full),
        "ad_group_only_rule_ids": sorted(partial),
        "uncertain_rule_ids": sorted(uncertain),
        "meaning": "Current literal configuration only; not past blocking, variants, or auction simulation",
    }


def ngrams(rows: list[Json]) -> Json:
    buckets: dict[tuple[str, str], Json] = {}
    skipped = 0
    for row in rows:
        words = tokens(str(row["term"]))
        if not words:
            skipped += 1
            continue
        # A pattern occurs once per query row, even when the word repeats.
        patterns = {
            " ".join(words[i : i + size])
            for size in (1, 2, 3)
            for i in range(len(words) - size + 1)
        }
        for pattern in patterns:
            key = (str(row["campaign_id"]), pattern)
            dest = buckets.setdefault(
                key,
                {
                    "campaign_id": key[0],
                    "pattern": pattern,
                    "term_count": 0,
                    "cost_micros": Decimal(0),
                    "clicks": Decimal(0),
                    "conversions": Decimal(0),
                },
            )
            dest["term_count"] += 1
            for m in ("cost_micros", "clicks", "conversions"):
                value = row.get(m)
                dest[m] = (
                    None
                    if dest[m] is None or value is None
                    else dest[m] + number(value)
                )
    result = []
    for dest in buckets.values():
        if dest["term_count"] < 2:
            continue
        for m in ("cost_micros", "clicks", "conversions"):
            if dest[m] is not None:
                dest[m] = float(dest[m]) if m == "conversions" else int(dest[m])
        result.append(dest)
    result.sort(
        key=lambda r: (-(r["cost_micros"] or 0), r["campaign_id"], r["pattern"])
    )
    return {
        "patterns": result,
        "skipped_terms": skipped,
        "additive_across_patterns": False,
        "note": "Overlapping 1-3 word observations within each campaign; no automatic negatives",
    }


def positive_overlap(term: str, campaign_id: str, inventory: Json) -> list[str]:
    return [
        str(r["id"])
        for r in inventory.get("positive_keywords", [])
        if r.get("status") == "ENABLED"
        and r.get("campaign_id") == campaign_id
        and str(r.get("text", "")).strip().lower() == term.strip().lower()
    ]
