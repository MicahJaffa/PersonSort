"""Lightweight GitHub Releases update checker for desktop distributions."""

from __future__ import annotations

import json
import re
import threading
from typing import Any
import urllib.request

import config
from config import APP_VERSION, GITHUB_RELEASES_REPO

_CACHE_LOCK = threading.Lock()
_CACHED_RESULT: dict[str, Any] | None = None
_CACHED_KEY: tuple[str, str] | None = None


def _parse_version_tuple(tag: str) -> tuple[int, ...]:
    """Convert a version tag like 'v1.2.3' into `(1, 2, 3)` for numeric comparison."""
    cleaned = (tag or "").strip().lstrip("vV")
    nums = [int(part) for part in re.findall(r"\d+", cleaned)]
    return tuple(nums) if nums else (0,)


def _build_mock_latest_version(current_version: str) -> str:
    """Build a synthetic next patch version tag (e.g. '1.0.2' -> 'v1.0.3') for mock update testing."""
    parts = list(_parse_version_tuple(current_version))
    while len(parts) < 3:
        parts.append(0)
    parts[-1] += 1
    return f"v{'.'.join(str(p) for p in parts)}"


def is_newer_version(latest_tag: str, current_version: str) -> bool:
    """Return True if `latest_tag` is strictly newer than `current_version`."""
    latest_parts = _parse_version_tuple(latest_tag)
    curr_parts = _parse_version_tuple(current_version)
    max_len = max(len(latest_parts), len(curr_parts))
    padded_latest = latest_parts + (0,) * (max_len - len(latest_parts))
    padded_curr = curr_parts + (0,) * (max_len - len(curr_parts))
    return padded_latest > padded_curr


def check_for_update(
    current_version: str = APP_VERSION,
    repo: str = GITHUB_RELEASES_REPO,
    timeout: float = 3.5,
    force: bool = False,
    test_update: bool | None = None,
) -> dict[str, Any]:
    """
    Query the GitHub Releases API once per process for newer versions.
    Never raises: fails silently on offline networks or repositories without releases yet.
    """
    global _CACHED_RESULT, _CACHED_KEY
    clean_repo = (repo or "").strip()
    cache_key = (current_version, clean_repo)
    fallback_url = (
        f"https://github.com/{clean_repo}/releases"
        if clean_repo
        else "https://github.com"
    )

    use_mock = bool(config.TEST_UPDATE) if test_update is None else bool(test_update)
    if use_mock:
        mock_latest = _build_mock_latest_version(current_version)
        return {
            "current_version": current_version,
            "latest_version": mock_latest,
            "update_available": True,
            "release_url": fallback_url,
            "release_name": f"PersonSort {mock_latest} (Test Mock Update)",
            "release_notes": "This is a simulated update triggered because TEST_UPDATE = True in config.py.",
            "checked": True,
            "is_mock": True,
            "error": None,
        }

    if not force:
        with _CACHE_LOCK:
            if _CACHED_RESULT is not None and _CACHED_KEY == cache_key:
                return dict(_CACHED_RESULT)

    if not clean_repo:
        return {
            "current_version": current_version,
            "latest_version": current_version,
            "update_available": False,
            "release_url": fallback_url,
            "release_name": "",
            "release_notes": "",
            "checked": False,
            "is_mock": False,
            "error": "No GitHub repository configured.",
        }

    api_url = f"https://api.github.com/repos/{clean_repo}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"PersonSort/{current_version}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        latest_tag = str(payload.get("tag_name") or "").strip()
        release_url = str(payload.get("html_url") or fallback_url).strip()
        release_name = str(payload.get("name") or latest_tag).strip()
        raw_notes = str(payload.get("body") or "").strip()
        release_notes = (
            raw_notes[:597].rstrip() + "..." if len(raw_notes) > 600 else raw_notes
        )
        newer = bool(latest_tag) and is_newer_version(latest_tag, current_version)
        result = {
            "current_version": current_version,
            "latest_version": latest_tag or current_version,
            "update_available": newer,
            "release_url": release_url,
            "release_name": release_name,
            "release_notes": release_notes,
            "checked": True,
            "is_mock": False,
            "error": None,
        }
    except Exception as exc:
        result = {
            "current_version": current_version,
            "latest_version": current_version,
            "update_available": False,
            "release_url": fallback_url,
            "release_name": "",
            "release_notes": "",
            "checked": False,
            "is_mock": False,
            "error": str(exc),
        }

    with _CACHE_LOCK:
        _CACHED_RESULT = dict(result)
        _CACHED_KEY = cache_key
    return result
