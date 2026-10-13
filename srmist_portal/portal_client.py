from __future__ import annotations
import re
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from .config import PORTAL_HOST, PORTAL_ROOT
from .auth import require_authenticated_page, _same_portal, browser_task

_nav_lock = threading.RLock()

SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "profile": ("profile", "student details", "personal details", "student information", "my profile"),
    "attendance": ("attendance", "course wise attendance", "attendance report"),
    "marks": ("internal marks", "marks", "assessment", "cat 1", "cat 2", "grade", "result"),
    "results": ("semester result", "results", "grade sheet", "grades", "result"),
    "timetable": ("timetable", "time table", "class schedule", "course schedule", "schedule"),
    "calendar": ("academic calendar", "calendar", "academic dates", "exam schedule", "examination schedule"),
}


def _frame_docs(page: Page) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for frame in page.frames:
        try:
            if not _same_portal(frame.url):
                continue
            out.append((frame.url, frame.content()))
        except Exception:
            continue
    return out


def extract_tables(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    tables: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        title = None
        prev = table.find_previous(["h1", "h2", "h3", "h4", "caption"])
        if prev:
            title = " ".join(prev.get_text(" ", strip=True).split()) or None
        rows_raw: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all(["th", "td"], recursive=False)]
            if cells and any(cells):
                rows_raw.append(cells)
        if not rows_raw:
            continue
        header_index = next((i for i, row in enumerate(rows_raw[:3]) if len(row) and any(x for x in row)), 0)
        headers = [h or f"column_{i+1}" for i, h in enumerate(rows_raw[header_index])]
        data_rows = []
        for raw in rows_raw[header_index + 1:]:
            if not raw or (len(raw) == 1 and not raw[0]):
                continue
            normalized = {headers[i] if i < len(headers) else f"column_{i+1}": (raw[i] if i < len(raw) else "") for i in range(max(len(headers), len(raw)))}
            data_rows.append(normalized)
        if data_rows or headers:
            tables.append({"title": title, "headers": headers, "rows": data_rows})
    return tables


def collect_links(page: Page) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for frame in page.frames:
        if not _same_portal(frame.url):
            continue
        try:
            items = frame.locator("a").evaluate_all("els => els.map(a => ({text:(a.innerText||a.textContent||'').trim(), href:a.href||''}))")
        except Exception:
            continue
        for item in items:
            text = " ".join(str(item.get("text", "")).split())
            href = urljoin(frame.url, str(item.get("href", "")))
            if not text or not _same_portal(href) or href.endswith("#"):
                continue
            key = (text.lower(), href)
            if key not in seen:
                seen.add(key)
                links.append({"text": text[:180], "url": href, "frame_url": frame.url})
    return links


def _navigate_section(page: Page, section: str, extra_query: str | None = None) -> dict[str, Any]:
    if section not in SECTION_KEYWORDS:
        raise ValueError(f"Unsupported section '{section}'.")
    keywords = list(SECTION_KEYWORDS[section])
    if extra_query:
        keywords.insert(0, extra_query.lower().strip())
    links = collect_links(page)
    scored: list[tuple[int, dict[str, str]]] = []
    for link in links:
        text = link["text"].lower()
        path = urlparse(link["url"]).path.lower()
        score = 0
        for kw in keywords:
            if kw and kw in text:
                score += 4 + len(kw) / 20
            if kw and kw.replace(" ", "") in path.replace("_", "").replace("-", ""):
                score += 2
        if score:
            scored.append((int(score * 10), link))
    scored.sort(key=lambda x: x[0], reverse=True)
    tried: list[str] = []
    for _, candidate in scored[:6]:
        if candidate["url"] in tried:
            continue
        tried.append(candidate["url"])
        try:
            page.goto(candidate["url"], wait_until="domcontentloaded", timeout=18000)
            try:
                page.wait_for_load_state("networkidle", timeout=2500)
            except PlaywrightTimeoutError:
                pass
            # Some portal navigation links are shells; still inspect page frames for relevant tables.
            snap = snapshot_page(page)
            text_for_check = (snap.get("text", "") + " " + " ".join((t.get("title") or "") + " " + " ".join(t.get("headers", [])) for t in snap.get("tables", []))).lower()
            if any(k in text_for_check for k in keywords) or snap.get("tables"):
                return {"navigated": True, "matched_link": candidate, "snapshot": snap, "links_considered": len(scored)}
        except Exception:
            continue
    snap = snapshot_page(page)
    page_text = snap.get("text", "").lower()
    if any(k in page_text for k in keywords) or snap.get("tables"):
        return {"navigated": False, "matched_link": None, "snapshot": snap, "links_considered": len(scored),
                "warning": "Could not confirm a dedicated section link; returning tables available on the current authenticated page."}
    return {"navigated": False, "matched_link": None, "snapshot": snap, "links_considered": len(scored),
            "warning": f"No matching portal section link was discoverable for '{section}'. No data was fabricated."}


@browser_task
def snapshot_page(page: Page | None = None) -> dict[str, Any]:
    if page is None:
        page = require_authenticated_page()
    # Combine first-party frames, including legacy JSP frames used by older portals.
    docs = _frame_docs(page)
    tables: list[dict[str, Any]] = []
    full_text: list[str] = []
    table_seen: set[str] = set()
    for frame_url, html in docs:
        soup = BeautifulSoup(html, "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())
        if text:
            full_text.append(text[:30000])
        for table in extract_tables(html):
            # Keep source URL on each extracted table for traceability.
            table["source_url"] = frame_url
            signature = repr((table.get("headers"), table.get("rows")[:2]))
            if signature not in table_seen:
                table_seen.add(signature)
                tables.append(table)
    links = collect_links(page)
    return {"portal_url": page.url, "title": page.title(), "text": " ".join(full_text)[:60000],
            "tables": tables, "links": links, "retrieval_timestamp": datetime.now(timezone.utc).isoformat()}


@browser_task
def _table_matches_section(table: dict[str, Any], section: str, keys: list[str]) -> bool:
    headers = table.get("headers", [])
    values = []
    for row in table.get("rows", [])[:3]:
        values.extend(str(v) for v in row.values())
    combo = " ".join([table.get("title") or "", *headers, *values]).lower()
    if any(k in combo for k in keys if k):
        return True
    header_text = " ".join(headers).lower()
    if section == "attendance":
        return any(term in header_text for term in ("att hours", "attended", "max hours", "absent hours", "attendance %", "total percentage"))
    if section == "marks":
        return any(term in header_text for term in ("cat 1", "cat 2", "internal", "assessment", "marks", "score", "quiz", "assignment"))
    if section == "results":
        return any(term in header_text for term in ("grade", "sgpa", "cgpa", "result", "semester result"))
    if section == "profile":
        return any(term in combo for term in ("student name", "registration number", "register no", "department", "programme", "program", "campus", "semester", "section"))
    if section == "timetable":
        return (any(term in header_text for term in ("day", "weekday", "time", "slot", "room", "classroom"))
                and any(term in header_text for term in ("course", "subject", "faculty", "room", "time", "day")))
    if section == "calendar":
        return any(term in header_text for term in ("date", "event", "exam", "examination", "academic year", "holiday"))
    return False


@browser_task
def get_section(section: str, query: str | None = None) -> dict[str, Any]:
    page = require_authenticated_page()
    with _nav_lock:
        found = _navigate_section(page, section, query)
    snap = found["snapshot"]
    keys = list(SECTION_KEYWORDS.get(section, ())) + ([query.lower()] if query else [])
    relevant = [table for table in snap.get("tables", []) if _table_matches_section(table, section, keys)]
    ok = bool(relevant or any(k in snap.get("text", "").lower() for k in keys))
    warnings = []
    if found.get("warning"):
        warnings.append(found["warning"])
    if not relevant:
        warnings.append("No tabular records were detected. This portal section may render data in a different format or may not be available to this account.")
    return {"ok": ok, "retrieval_timestamp": snap["retrieval_timestamp"], "portal_url": snap["portal_url"],
            "semester": None, "data": {"section": section, "tables": relevant, "page_title": snap.get("title"),
                                       "text_excerpt": snap.get("text", "")[:12000], "links": snap.get("links", [])[:100]},
            "warnings": warnings, "error": None if ok else f"Could not verify available '{section}' data in the current authenticated portal session."}
