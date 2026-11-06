"""Map a saved MCP response to explicit evidence fields; never fetch credentials."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from evidence_guard import (
    EvidenceError,
    INTEGERS,
    checked_date,
    require,
    validate_dataset,
)

Json = dict[str, Any]


def extract_rows(envelope: Json) -> list[Json]:
    require(envelope.get("isError") is not True, "MCP reported an error")
    if "structuredContent" in envelope:
        payload = envelope["structuredContent"]
    else:
        content = envelope.get("content")
        if not isinstance(content, list):
            raise EvidenceError("Missing MCP content")
        texts = [
            x.get("text")
            for x in content
            if isinstance(x, dict) and x.get("type") == "text"
        ]
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise EvidenceError("Expected one JSON text block")
        payload = json.loads(texts[0])
    if not isinstance(payload, dict):
        raise EvidenceError("Expected object response")
    require(
        not payload.get("error") and payload.get("isError") is not True, "Payload error"
    )
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise EvidenceError("Expected flat structuredContent.result rows")
    require(all(isinstance(row, dict) for row in rows), "Malformed result row")
    return [dict(row) for row in rows]


def normalize(envelope: Json, spec: Json) -> Json:
    """spec: metadata plus explicit short_name -> raw flat field mapping."""
    raw_rows = extract_rows(envelope)
    metadata, mapping = spec.get("metadata"), spec.get("mapping")
    if not isinstance(metadata, dict) or not isinstance(mapping, dict):
        raise EvidenceError("Need metadata and mapping")
    require(
        all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()),
        "Invalid mapping",
    )
    require(
        len(set(mapping.values())) == len(mapping),
        "One raw field must not become two metrics",
    )
    result = dict(metadata)
    rows: list[Json] = []
    for raw in raw_rows:
        for key, value in raw.items():
            if key.endswith("resource_name") or key.endswith(
                (".campaign", ".ad_group")
            ):
                owner = (
                    re.match(r"^customers/([^/]+)(?:/|$)", value)
                    if isinstance(value, str)
                    else None
                )
                if owner:
                    require(
                        owner.group(1) == str(metadata["customer_id"]),
                        "Foreign account in raw resource name",
                    )
        # Validate context before projection; an omitted mapping must not erase a contradiction.
        for target, aliases in {
            "customer_id": ("customer.id", "customer_id"),
            "currency": ("customer.currency_code", "currency"),
            "timezone": ("customer.time_zone", "timezone"),
        }.items():
            for source in aliases:
                if source in raw:
                    require(
                        str(raw[source]) == str(metadata[target]),
                        f"Raw context mismatch: {source}",
                    )
        for source in ("segments.date", "date"):
            if source in raw:
                require(
                    checked_date(metadata["period"]["start"])
                    <= checked_date(raw[source])
                    <= checked_date(metadata["period"]["end"]),
                    "Raw date outside declared period",
                )
        if not any(key in raw for key in ("customer.id", "customer_id")):
            request = spec.get("request")
            require(
                isinstance(request, dict)
                and str(request.get("customer_id")) == str(metadata["customer_id"])
                and request.get("resource") == metadata["resource"],
                "Rows have no identity; preserve the actual single-account MCP request",
            )
        row: Json = {}
        for target, source in mapping.items():
            require(
                source in raw, f"MCP field missing: {source}; adapt schema explicitly"
            )
            value = raw[source]
            # Google int64 JSON values can be decimal strings. Only integer metrics are coerced.
            if target in INTEGERS and isinstance(value, str):
                require(value.isascii() and value.isdigit(), f"Invalid int64: {source}")
                value = int(value)
            row[target] = value
        rows.append(row)
    result["rows"] = rows
    result["raw_row_count"] = len(raw_rows)
    if "request" in spec:
        result["query"] = spec["request"]
    validate_dataset(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response", type=Path)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = normalize(
            json.loads(args.response.read_text()), json.loads(args.spec.read_text())
        )
        # No overwrite, even if a previous run left a partial file.
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, f"MCP adaptation failed: {exc}\n")


if __name__ == "__main__":
    main()
