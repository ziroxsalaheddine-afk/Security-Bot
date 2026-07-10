"""
Bypass storage — JSON-backed, global.
Structure: { "bypass": [user_id1, user_id2, ...] }

Bypass = partial immunity. Users may perform anti-nuke-level actions up to
the abuse threshold defined in the Warden cog. Exceeding it auto-revokes
bypass and triggers punishment + warning DM.
"""

import json
from pathlib import Path
from typing import Optional

BYPASS_PATH = Path(__file__).parent.parent / "bypass.db.json"
_DEFAULT: dict = {"bypass": []}
_cache: Optional[dict] = None


def _load() -> dict:
    global _cache
    if BYPASS_PATH.exists():
        try:
            raw = json.loads(BYPASS_PATH.read_text(encoding="utf-8"))
            _cache = {"bypass": [int(x) for x in raw.get("bypass", [])]}
        except Exception:
            _cache = {"bypass": []}
    else:
        _cache = {"bypass": []}
        _save()
    return _cache


def _save():
    tmp = BYPASS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    tmp.replace(BYPASS_PATH)


def get() -> dict:
    global _cache
    if _cache is None:
        _load()
    return _cache


def is_bypassed(user_id: int) -> bool:
    return user_id in get()["bypass"]


def add_bypass(user_id: int):
    d = get()
    if user_id not in d["bypass"]:
        d["bypass"].append(user_id)
        _save()


def remove_bypass(user_id: int):
    d = get()
    d["bypass"] = [x for x in d["bypass"] if x != user_id]
    _save()


def get_bypass_list() -> list[int]:
    return list(get()["bypass"])
