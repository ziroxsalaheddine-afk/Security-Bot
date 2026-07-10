"""
DJ Whitelist database
════════════════════════════════════════════════
Flat JSON file  →  dj.db.json

Functions
─────────
is_dj(user_id)              True if user is in the DJ whitelist
add_dj(user_id)             Add a user to the whitelist
remove_dj(user_id)          Remove a user from the whitelist
get_dj_list() → list[int]   Return all DJ user IDs
"""

import json
from pathlib import Path
from typing import Optional

DJ_PATH = Path(__file__).parent.parent / "dj.db.json"

_DEFAULT: dict = {"dj_users": []}
_cache: Optional[dict] = None


def _load() -> dict:
    global _cache
    if DJ_PATH.exists():
        try:
            raw = json.loads(DJ_PATH.read_text(encoding="utf-8"))
            _cache = {**_DEFAULT, **raw}
        except Exception:
            _cache = dict(_DEFAULT)
    else:
        _cache = dict(_DEFAULT)
        _save()
    return _cache


def _save():
    tmp = DJ_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    tmp.replace(DJ_PATH)


def get() -> dict:
    global _cache
    if _cache is None:
        _load()
    return _cache


def is_dj(user_id: int) -> bool:
    return int(user_id) in get().get("dj_users", [])


def add_dj(user_id: int):
    d = get()
    uid = int(user_id)
    if uid not in d["dj_users"]:
        d["dj_users"].append(uid)
        _save()


def remove_dj(user_id: int):
    d = get()
    d["dj_users"] = [x for x in d["dj_users"] if x != int(user_id)]
    _save()


def get_dj_list() -> list[int]:
    return get().get("dj_users", [])
