from __future__ import annotations
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Any
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError
from .config import LOGIN_URL, PORTAL_HOST, HEADLESS

_lock = threading.RLock()
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="srmist-browser")
_browser_thread_ident: int | None = None


def run_on_browser_thread(fn, *args, **kwargs):
    """Run all Playwright sync calls on a single dedicated thread (Playwright is thread-affine)."""
    global _browser_thread_ident
    if threading.get_ident() == _browser_thread_ident:
        return fn(*args, **kwargs)
    def invoke():
        global _browser_thread_ident
        _browser_thread_ident = threading.get_ident()
        return fn(*args, **kwargs)
    return _browser_executor.submit(invoke).result()


def browser_task(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return run_on_browser_thread(fn, *args, **kwargs)
    return wrapped
_pw: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None
_last_links: list[dict[str, str]] = []


def _ensure_browser() -> Page:
    global _pw, _browser, _context, _page
    with _lock:
        if _page is not None and not _page.is_closed():
            return _page
        if HEADLESS:
            # Explicit opt-in only. Portal CAPTCHA may require a visible browser.
            headless = True
        else:
            headless = False
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=headless)
        # In-memory context: no persistent cookie jar or credentials written by Playwright.
        _context = _browser.new_context(accept_downloads=False)
        _page = _context.new_page()
        _page.set_default_timeout(7000)
        return _page


def _require_page() -> Page:
    with _lock:
        if _page is None or _page.is_closed():
            raise RuntimeError("No browser session. Call login_to_srmist_portal first.")
        return _page


def _same_portal(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "https" and p.hostname == PORTAL_HOST and p.path.startswith("/srmiststudentportal/")


@browser_task
def login() -> dict[str, Any]:
    page = _ensure_browser()
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=25000)
    except PlaywrightTimeoutError:
        pass
    return {
        "ok": True,
        "status": "login_required",
        "portal_url": page.url,
        "instructions": "A local Chromium window is open. Enter your NetID/password directly in that window and solve any CAPTCHA/MFA yourself. Credentials are not requested from or returned to the model. After the portal redirects to your student dashboard, call check_login_status.",
        "browser_headless": HEADLESS,
        "security_note": "Login is performed only in the user's local browser session. Do not paste credentials or cookies into chat."
    }


@browser_task
def status() -> dict[str, Any]:
    page = _require_page()
    try:
        url = page.url
        if not _same_portal(url):
            return {"ok": True, "authenticated": False, "status": "not_on_student_portal", "portal_url": url,
                    "error": "The current page is not within the official SRMIST student-portal path."}
        body = page.locator("body").inner_text(timeout=5000).lower()
        inputs = page.locator("input").evaluate_all("els => els.map(e => ({type:(e.type||'').toLowerCase(), name:e.name||'', id:e.id||'', placeholder:e.placeholder||''}))")
        login_fields = any(("password" in (i.get("type", "") + " " + i.get("name", "") + " " + i.get("id", "") + " " + i.get("placeholder", "")).lower()) for i in inputs)
        has_login_markers = ("captcha" in body and ("netid" in body or "password" in body))
        # Treat uncertain pages conservatively: only a portal page without login controls is authenticated.
        if login_fields or has_login_markers or "loginservlet" in url.lower():
            return {"ok": True, "authenticated": False, "status": "login_required", "portal_url": url,
                    "error": "The portal still appears to show its login form. Complete login/CAPTCHA in the local browser window."}
        if any(term in body for term in ["welcome", "attendance", "timetable", "student", "dashboard", "semester"]):
            return {"ok": True, "authenticated": True, "status": "authenticated_or_dashboard_detected", "portal_url": url,
                    "note": "Portal page detected; extraction tools will still validate each requested section."}
        return {"ok": True, "authenticated": False, "status": "unknown", "portal_url": url,
                "error": "Could not safely determine login status from this page. Navigate to the student's dashboard and retry."}
    except Exception as exc:
        return {"ok": False, "authenticated": False, "status": "error", "error": f"Unable to inspect browser session: {type(exc).__name__}: {exc}"}


def require_authenticated_page() -> Page:
    page = _require_page()
    s = status()
    if not s.get("authenticated"):
        raise RuntimeError(s.get("error") or "Not authenticated. Call login_to_srmist_portal and finish login manually.")
    return page


@browser_task
def logout() -> dict[str, Any]:
    global _pw, _browser, _context, _page, _last_links
    with _lock:
        page = _page
        attempted = False
        if page is not None and not page.is_closed():
            try:
                # Click a visible first-party logout link when available; do not guess endpoints.
                loc = page.get_by_role("link", name=re.compile(r"logout|log out|sign out", re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    attempted = True
            except Exception:
                pass
            try:
                if _context is not None:
                    _context.clear_cookies()
            except Exception:
                pass
        try:
            if _context is not None:
                _context.close()
        except Exception:
            pass
        try:
            if _browser is not None:
                _browser.close()
        except Exception:
            pass
        try:
            if _pw is not None:
                _pw.stop()
        except Exception:
            pass
        _pw = _browser = _context = _page = None
        _last_links = []
        return {"ok": True, "logged_out_locally": True, "portal_logout_link_clicked": attempted,
                "message": "Local browser session and cookies were cleared. If the portal did not expose a logout link, sign out in the official portal separately to invalidate its server-side session."}


@browser_task
def refresh_page() -> dict[str, Any]:
    page = require_authenticated_page()
    before = page.url
    page.reload(wait_until="domcontentloaded", timeout=25000)
    return {"ok": True, "portal_url": page.url, "previous_url": before, "status": status()}
