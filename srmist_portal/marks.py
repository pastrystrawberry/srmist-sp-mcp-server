from __future__ import annotations
from typing import Any
from .portal_client import get_section
from .academics import subject_filter


def get_internal_marks() -> dict[str, Any]:
    return get_section("marks", query="internal marks")


def get_subject_marks(subject: str) -> dict[str, Any]:
    result = get_internal_marks()
    if not result.get("ok"):
        return result
    return subject_filter(result, subject)


def get_semester_results() -> dict[str, Any]:
    return get_section("results")
