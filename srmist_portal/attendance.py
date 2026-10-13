from __future__ import annotations
import math
import re
from typing import Any


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", str(value).lower()).strip()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _column(headers: list[str], patterns: tuple[str, ...]) -> str | None:
    for h in headers:
        n = _norm(h)
        if any(p in n for p in patterns):
            return h
    return None


def calculate_margin(attended: float, total: float, target_percent: float, count_unit: str = "classes") -> dict[str, Any]:
    if not (0 < target_percent <= 100):
        raise ValueError("target_percent must be greater than 0 and at most 100.")
    if attended < 0 or total < 0 or attended > total:
        raise ValueError("Attendance counts must satisfy 0 <= attended <= total.")
    target = target_percent / 100.0
    current = (attended / total * 100.0) if total else None
    # Inclusive target: find largest number of future absences x for which A/(T+x) >= target.
    if total == 0:
        absences_allowed = None
    else:
        absences_allowed = 0
        while attended / (total + absences_allowed + 1) + 1e-12 >= target:
            absences_allowed += 1
    if current is None:
        classes_needed = None
    elif current / 100.0 + 1e-12 >= target:
        classes_needed = 0
    elif target >= 1:
        classes_needed = None
    else:
        classes_needed = max(0, math.ceil(((target * total - attended) / (1 - target)) - 1e-12))
        while (attended + classes_needed) / (total + classes_needed) + 1e-12 < target:
            classes_needed += 1
    unit = count_unit if count_unit in {"classes", "hours", "sessions", "units"} else "units"
    label = {"classes": "class", "hours": "hour", "sessions": "session", "units": "attendance unit"}[unit]
    return {
        "attended_units": attended,
        "total_units": total,
        "count_unit": unit,
        "current_attendance_percent": round(current, 2) if current is not None else None,
        "target_percent": target_percent,
        "additional_absence_units_allowed_at_or_above_target": absences_allowed,
        "consecutive_attendance_units_to_reach_target": classes_needed,
        "assumptions": [f"Each future {label} counts equally as one {label}.", "The target threshold is inclusive (>= target).",
                        "If current counts are unavailable or zero, the result may be undefined."],
    }


def compute_subject_margins(tables: list[dict[str, Any]], target_percent: float) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    attended_aliases = ("attended", "att hours", "hours present", "present hours", "hours attended", "classes attended")
    total_aliases = ("max hours", "maximum hours", "total classes", "classes conducted", "total hours", "conducted", "scheduled hours")
    subject_aliases = ("subject", "course", "description", "course name", "subject name", "code")
    for ti, table in enumerate(tables):
        headers = list(table.get("headers", []))
        ac = _column(headers, attended_aliases)
        tc = _column(headers, total_aliases)
        if not ac or not tc:
            continue
        sc = _column(headers, subject_aliases)
        for ri, row in enumerate(table.get("rows", [])):
            a, t = _number(row.get(ac)), _number(row.get(tc))
            if a is None or t is None or t <= 0 or a < 0 or a > t:
                continue
            name = str(row.get(sc, f"row {ri + 1}")).strip() if sc else f"row {ri + 1}"
            count_unit = "hours" if "hour" in (ac + " " + tc).lower() else "classes"
            try:
                record = {"subject": name, "source_attended_column": ac, "source_total_column": tc,
                          **calculate_margin(a, t, target_percent, count_unit=count_unit)}
                output.append(record)
            except ValueError as exc:
                skipped.append({"row": name, "reason": str(exc)})
    if not output:
        return {"ok": False, "subjects": [], "skipped": skipped,
                "error": "Attendance tables with identifiable attended and total-count columns were not found. No estimate was made.",
                "warnings": ["Portal column labels may need a verified adapter based on a real, redacted attendance table."]}
    return {"ok": True, "subjects": output, "skipped": skipped, "error": None, "warnings": []}
