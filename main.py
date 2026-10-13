from __future__ import annotations
from datetime import datetime, timezone
import re
from typing import Any, Literal
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from srmist_portal.auth import login, status, logout, refresh_page
from srmist_portal.portal_client import get_section, snapshot_page
from srmist_portal.attendance import compute_subject_margins
from srmist_portal.academics import cgpa_summary
from srmist_portal.marks import get_internal_marks as fetch_internal_marks, get_subject_marks as fetch_subject_marks, get_semester_results as fetch_semester_results

mcp = FastMCP(
    name="SRMIST Student Portal",
    instructions=("Read-only, local-first connector for the authenticated user's SRMIST Student Portal. "
                  "The user must log in manually in the local browser and complete any CAPTCHA/MFA. "
                  "Never ask for or output passwords, cookies, or session tokens. Portal sections and extraction "
                  "are dynamically discovered; do not invent data when a section is absent or unverified.")
)


class AttendanceMarginInput(BaseModel):
    target_percent: float = Field(default=75.0, gt=0, le=100, description="Target attendance percentage, inclusive.")


class SubjectInput(BaseModel):
    subject: str = Field(min_length=1, max_length=160, description="Subject name or course code as displayed in the portal.")


def _section_or_error(section: str, query: str | None = None) -> dict[str, Any]:
    try:
        return get_section(section, query=query)
    except Exception as exc:
        return {"ok": False, "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                "portal_url": None, "semester": None, "data": None, "warnings": [],
                "error": f"{type(exc).__name__}: {exc}"}


def _extract_overall_attendance(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") or {}
    matches: list[dict[str, Any]] = []
    for table in data.get("tables", []):
        headers = table.get("headers", [])
        for row in table.get("rows", []):
            label = " ".join(str(v) for v in row.values()).lower()
            if any(word in label for word in ("overall", "grand total", "total attendance", "total hours")):
                for key, value in row.items():
                    if "percent" in key.lower() or "attendance %" in key.lower() or key.strip().endswith("%"):
                        matches.append({"label": label[:140], "field": key, "value": value, "source_url": table.get("source_url")})
    text = str(data.get("text_excerpt", ""))
    for m in re.finditer(r"(?:overall\s+attendance|attendance\s+percentage|total\s+percentage)\s*[:\-]?\s*(\d{1,3}(?:\.\d{1,2})?)\s*%", text, re.I):
        value = float(m.group(1))
        if 0 <= value <= 100:
            matches.append({"label": m.group(0), "field": "detected_text_percentage", "value": f"{value}%", "source_url": result.get("portal_url")})
    data["verified_overall_attendance_candidates"] = matches
    if not matches:
        result.setdefault("warnings", []).append("No unambiguous overall attendance percentage was detected; inspect the returned source tables rather than assuming a value.")
    result["data"] = data
    return result



@mcp.tool
def login_to_srmist_portal():
    """Open the official SRMIST login page in a local browser; the student enters credentials and CAPTCHA manually."""
    try:
        return login()
    except Exception as exc:
        return {"ok": False, "status": "error", "error": f"{type(exc).__name__}: {exc}",
                "hint": "Run locally with a desktop session and install Chromium using `uv run playwright install chromium`."}

@mcp.tool
def check_login_status():
    """Check whether the current local browser appears to be logged into the student portal."""
    try:
        return status()
    except Exception as exc:
        return {"ok": False, "authenticated": False, "status": "error", "error": f"{type(exc).__name__}: {exc}"}

@mcp.tool
def get_student_profile():
    """Retrieve available profile/student-detail tables from the authenticated portal."""
    return _section_or_error("profile")

@mcp.tool
def get_overall_attendance():
    """Retrieve available attendance tables and any unambiguous overall percentage shown by the portal."""
    return _extract_overall_attendance(_section_or_error("attendance"))

@mcp.tool
def get_subject_attendance():
    """Retrieve available subject-wise attendance tables from the authenticated portal."""
    return _section_or_error("attendance", query="subject attendance")

@mcp.tool
def calculate_attendance_margin(target_percent: float = 75.0):
    """Calculate missed-class margin and classes needed using actual parsed attended/total columns. Target is inclusive."""
    if not (0 < target_percent <= 100):
        return {"ok": False, "error": "target_percent must be greater than 0 and at most 100.", "subjects": []}
    attendance = _section_or_error("attendance", query="subject attendance")
    if not attendance.get("ok"):
        return {"ok": False, "retrieval_timestamp": attendance.get("retrieval_timestamp"),
                "portal_url": attendance.get("portal_url"), "subjects": [],
                "error": attendance.get("error") or "Attendance data could not be retrieved.",
                "warnings": attendance.get("warnings", [])}
    tables = (attendance.get("data") or {}).get("tables", [])
    computed = compute_subject_margins(tables, target_percent)
    return {"ok": computed["ok"], "retrieval_timestamp": attendance.get("retrieval_timestamp"),
            "portal_url": attendance.get("portal_url"), "semester": attendance.get("semester"),
            "target_percent": target_percent, "subjects": computed["subjects"],
            "skipped": computed["skipped"], "error": computed["error"],
            "warnings": list(attendance.get("warnings", [])) + computed["warnings"]}

@mcp.tool
def get_internal_marks():
    """Retrieve available internal assessment, CAT, assignment, quiz, and lab marks tables."""
    try:
        return fetch_internal_marks()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "retrieval_timestamp": datetime.now(timezone.utc).isoformat()}

@mcp.tool
def get_subject_marks(subject: str):
    """Retrieve marks rows matching a subject name or course code from available marks tables."""
    try:
        return fetch_subject_marks(subject)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "retrieval_timestamp": datetime.now(timezone.utc).isoformat()}

@mcp.tool
def get_semester_results():
    """Retrieve released results/grade tables available to the authenticated student."""
    try:
        return fetch_semester_results()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "retrieval_timestamp": datetime.now(timezone.utc).isoformat()}

@mcp.tool
def get_cgpa():
    """Find displayed CGPA/SGPA values in profile/results content; missing values remain missing, never zero."""
    results = [_section_or_error("profile"), _section_or_error("results")]
    extracted = cgpa_summary(results)
    extracted["sources"] = [{"portal_url": r.get("portal_url"), "ok": r.get("ok"), "error": r.get("error")} for r in results]
    return extracted

@mcp.tool
def get_timetable(period: Literal["today", "week", "all"] = "today"):
    """Retrieve the available timetable. 'today' filters by a recognizable weekday column; otherwise raw rows are returned with a warning."""
    result = _section_or_error("timetable")
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    if period == "all":
        data["requested_period"] = period
        result["data"] = data
        return result
    from datetime import datetime
    today = datetime.now().strftime("%A").lower()
    if period == "week":
        data["requested_period"] = period
        data["note"] = "Returning all timetable rows discovered; current portal week boundaries were not inferred."
        result["data"] = data
        return result
    filtered_tables = []
    had_day_column = False
    for table in data.get("tables", []):
        day_key = next((h for h in table.get("headers", []) if any(k in h.lower() for k in ("day", "weekday"))), None)
        if not day_key:
            filtered_tables.append(table)
            continue
        had_day_column = True
        rows = [r for r in table.get("rows", []) if today in str(r.get(day_key, "")).lower() or "today" in str(r.get(day_key, "")).lower()]
        filtered_tables.append({**table, "rows": rows})
    data["tables"] = filtered_tables
    data["requested_period"] = period
    if not had_day_column:
        result.setdefault("warnings", []).append("No recognizable weekday column was found; returned unfiltered available timetable rows.")
    data["local_weekday_at_retrieval"] = today
    result["data"] = data
    return result

@mcp.tool
def get_academic_calendar():
    """Retrieve available academic calendar and announced examination-date tables."""
    return _section_or_error("calendar")

@mcp.tool
def refresh_student_data():
    """Reload the authenticated portal page and return a fresh snapshot, without modifying portal data."""
    try:
        refreshed = refresh_page()
        if not refreshed.get("status", {}).get("authenticated"):
            return {"ok": False, "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": "The session may have expired. Call login_to_srmist_portal and authenticate again.",
                    "status": refreshed.get("status")}
        return {"ok": True, "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                "portal_url": refreshed.get("portal_url"), "data": snapshot_page(),
                "warnings": ["Refreshing reloads the current page; each academic section is retrieved when its tool is called."]}
    except Exception as exc:
        return {"ok": False, "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}"}

@mcp.tool
def logout_from_srmist_portal():
    """Clear the local browser session and cookies. Uses a logout link only if the current portal exposes one."""
    try:
        return logout()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

@mcp.tool
def discover_portal_sections():
    """List same-origin student-portal links and tables currently visible after authentication, for safe section discovery."""
    try:
        snap = snapshot_page()
        return {"ok": True, "retrieval_timestamp": snap["retrieval_timestamp"], "portal_url": snap["portal_url"],
                "page_title": snap["title"], "links": snap["links"], "tables": snap["tables"],
                "warning": "Only same-origin student-portal links on the current page/frame are listed. Hidden or inaccessible sections are not inferred."}
    except Exception as exc:
        return {"ok": False, "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}"}

if __name__ == "__main__":
    mcp.run()
