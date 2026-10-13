from __future__ import annotations
import re
from typing import Any
from .portal_client import get_section
from .attendance import compute_subject_margins
from .models import utc_now


def result_from_section(section_result: dict[str, Any], data_key: str | None = None) -> dict[str, Any]:
    return section_result


def subject_filter(section_result: dict[str, Any], subject: str) -> dict[str, Any]:
    if not subject.strip():
        return {**section_result, "ok": False, "error": "Provide a non-empty subject name or code."}
    data = section_result.get("data") or {}
    query = subject.strip().lower()
    tables = []
    for table in data.get("tables", []):
        rows = [r for r in table.get("rows", []) if query in " ".join(map(str, r.values())).lower()]
        if rows:
            tables.append({**table, "rows": rows})
    return {**section_result, "ok": bool(tables), "data": {**data, "subject_filter": subject, "tables": tables},
            "error": None if tables else f"No rows matching '{subject}' were found in the available marks tables."}


def cgpa_summary(section_results: list[dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, str]] = []
    for result in section_results:
        data = result.get("data") or {}
        text = str(data.get("text_excerpt", ""))
        for label, value in re.findall(r"\b(CGPA|SGPA|GPA|Cumulative Grade Point Average|Semester Grade Point Average)\b[^\d\n]{0,35}(\d{1,2}(?:\.\d{1,3})?)", text, re.I):
            n = float(value)
            if 0 <= n <= 10:
                matches.append({"label": label.upper(), "value": str(n), "source_url": str(result.get("portal_url") or "")})
    return {"ok": bool(matches), "retrieval_timestamp": utc_now().isoformat(), "data": matches,
            "error": None if matches else "No displayed CGPA/SGPA could be verified on the available portal pages.",
            "warnings": [] if matches else ["Missing displayed GPA is not treated as zero."]}
