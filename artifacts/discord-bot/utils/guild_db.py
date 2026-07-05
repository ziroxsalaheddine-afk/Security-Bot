"""
Per-guild configuration storage for the Fast Setup wizard.
All data is persisted to guilds.db.json alongside guardian.db.json.
"""
import json
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).parent.parent / "guilds.db.json"

_GUILD_DEFAULT: dict = {
    "antinuke": {
        "enabled": True,
        "threshold": 3,
        "interval": 10,
        "action": "ban",
        "autoRestore": True,
        "clearRoles": True,
    },
    "automod": {
        "antiSpam": {"enabled": True, "messageLimit": 5, "interval": 3},
        "antiLink": {
            "enabled": True,
            "scanInvites": True,
            "allowedDomains": ["discord.com", "discord.gg"],
        },
        "antiRaid": {
            "enabled": True,
            "joinThreshold": 10,
            "joinInterval": 10,
            "action": "kick",
        },
    },
    "whitelist": {"users": [], "roles": []},
    "logs": {"channelId": None},
}

_cache: Optional[dict] = None


def _load() -> dict:
    global _cache
    if _DB_PATH.exists():
        try:
            _cache = json.loads(_DB_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save():
    global _cache
    tmp = _DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    tmp.replace(_DB_PATH)


def _db() -> dict:
    global _cache
    if _cache is None:
        _load()
    return _cache


def get_guild(guild_id: int) -> dict:
    db = _db()
    gid = str(guild_id)
    if gid not in db:
        db[gid] = json.loads(json.dumps(_GUILD_DEFAULT))
        _save()
    return db[gid]


def get_guild_value(guild_id: int, path: list, default: Any = None) -> Any:
    node = get_guild(guild_id)
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def set_guild_value(guild_id: int, path: list, value: Any):
    guild = get_guild(guild_id)
    node = guild
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    _save()


def toggle(guild_id: int, path: list) -> bool:
    current = get_guild_value(guild_id, path, False)
    new_val = not bool(current)
    set_guild_value(guild_id, path, new_val)
    return new_val


def add_whitelisted_user(guild_id: int, user_id: int):
    guild = get_guild(guild_id)
    users: list = guild.setdefault("whitelist", {}).setdefault("users", [])
    if user_id not in users:
        users.append(user_id)
        _save()


def remove_whitelisted_user(guild_id: int, user_id: int):
    guild = get_guild(guild_id)
    users: list = guild.get("whitelist", {}).get("users", [])
    if user_id in users:
        users.remove(user_id)
        _save()


def get_whitelisted_users(guild_id: int) -> list:
    return get_guild(guild_id).get("whitelist", {}).get("users", [])


def add_whitelisted_domain(guild_id: int, domain: str):
    guild = get_guild(guild_id)
    domains: list = (
        guild.setdefault("automod", {})
        .setdefault("antiLink", {})
        .setdefault("allowedDomains", [])
    )
    clean = domain.lower().strip().lstrip("https://").lstrip("http://").split("/")[0]
    if clean not in domains:
        domains.append(clean)
        _save()
