"""
Server Co-Owner storage — JSON-backed, per-guild.
Structure on disk: { "guild_id": [user_id_1, user_id_2, ...] }
"""

import json
from pathlib import Path
from typing import Optional

COOWNERS_PATH = Path(__file__).parent.parent / "server_coowners.json"
_cache: Optional[dict] = None


def _load() -> dict:
    global _cache
    if COOWNERS_PATH.exists():
        try:
            _cache = json.loads(COOWNERS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    else:
        _cache = {}
        _save()
    return _cache


def _save():
    tmp = COOWNERS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    tmp.replace(COOWNERS_PATH)


def get() -> dict:
    global _cache
    if _cache is None:
        _load()
    return _cache


def is_coowner(guild_id: int, user_id: int) -> bool:
    return user_id in get().get(str(guild_id), [])


def add_coowner(guild_id: int, user_id: int):
    d = get()
    key = str(guild_id)
    if key not in d:
        d[key] = []
    if user_id not in d[key]:
        d[key].append(user_id)
        _save()


def remove_coowner(guild_id: int, user_id: int):
    d = get()
    key = str(guild_id)
    if key in d:
        d[key] = [x for x in d[key] if x != user_id]
        _save()


def get_coowners(guild_id: int) -> list[int]:
    return get().get(str(guild_id), [])
