import json
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "guardian.db.json"

_DEFAULT: dict = {
    "whitelist": [],
    "owners": [],
    "config": {
        "prefix": "+",
        "antinuke": {
            "enabled": True,
            "threshold": 3,
            "interval": 10,
            "action": "ban",
        },
        "automod": {
            "antiSpam": {"enabled": True, "messageLimit": 5, "interval": 3},
            "antiLink": {
                "enabled": True,
                "allowedDomains": [
                    "youtube.com",
                    "youtu.be",
                    "discord.com",
                    "discord.gg",
                    "twitch.tv",
                    "twitter.com",
                    "x.com",
                    "github.com",
                ],
            },
            "antiRaid": {
                "enabled": True,
                "joinThreshold": 10,
                "joinInterval": 10,
                "action": "kick",
            },
        },
        "altProtection": {"enabled": True, "minAccountAge": 7, "action": "kick"},
    },
    "logs": {"channelId": None},
    "quarantine": {"users": {}, "role": None},
    "backups": {},
}

_cache: Optional[dict] = None


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _migrate_legacy(legacy: dict) -> dict:
    """Convert the old JS guardian.db.json format to the new Python format."""
    d = json.loads(json.dumps(_DEFAULT))

    # owners / coowners
    if isinstance(legacy.get("owners"), list):
        d["owners"] = [int(x) for x in legacy["owners"]]

    # whitelist — old format has whitelist.users list
    wl = legacy.get("whitelist", {})
    if isinstance(wl, dict) and "users" in wl:
        d["whitelist"] = [int(x) for x in wl.get("users", [])]
    elif isinstance(wl, list):
        d["whitelist"] = [int(x) for x in wl]

    # logs
    if legacy.get("logs", {}).get("channelId"):
        d["logs"]["channelId"] = legacy["logs"]["channelId"]

    # config overrides
    cfg = legacy.get("config", {})
    if cfg.get("antinuke"):
        d["config"]["antinuke"].update(cfg["antinuke"])
    if cfg.get("automod"):
        d["config"]["automod"].update(cfg["automod"])
    if cfg.get("altProtection"):
        d["config"]["altProtection"].update(cfg["altProtection"])

    return d


def _load() -> dict:
    global _cache
    if DB_PATH.exists():
        try:
            raw = json.loads(DB_PATH.read_text(encoding="utf-8"))
            # Detect legacy JS format: owners contains strings, or whitelist is a dict
            is_legacy = isinstance(raw.get("whitelist"), dict) or (
                raw.get("owners") and isinstance(raw["owners"][0], str)
                if raw.get("owners")
                else False
            )
            if is_legacy:
                _cache = _migrate_legacy(raw)
                _save()
            else:
                _cache = _deep_merge(json.loads(json.dumps(_DEFAULT)), raw)
        except Exception:
            _cache = json.loads(json.dumps(_DEFAULT))
    else:
        _cache = json.loads(json.dumps(_DEFAULT))
        _save()
    return _cache


def _save():
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)


def get() -> dict:
    global _cache
    if _cache is None:
        _load()
    return _cache


def save():
    _save()


# ── Whitelist ─────────────────────────────────────────────────────────────────

def is_whitelisted(user_id: int) -> bool:
    d = get()
    return user_id in d.get("whitelist", []) or user_id in d.get("owners", [])


def add_whitelist(user_id: int):
    d = get()
    if user_id not in d["whitelist"]:
        d["whitelist"].append(user_id)
        save()


def remove_whitelist(user_id: int):
    d = get()
    d["whitelist"] = [x for x in d["whitelist"] if x != user_id]
    save()


def get_whitelist() -> list:
    return get().get("whitelist", [])


# ── Owners ────────────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id in get().get("owners", [])


def add_owner(user_id: int):
    d = get()
    if user_id not in d["owners"]:
        d["owners"].append(user_id)
        save()


def remove_owner(user_id: int):
    d = get()
    d["owners"] = [x for x in d["owners"] if x != user_id]
    save()


def get_owners() -> list:
    return get().get("owners", [])


# ── Config ────────────────────────────────────────────────────────────────────

def get_config() -> dict:
    return get().get("config", {})


def set_config(path: list, value):
    d = get()
    node = d["config"]
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    save()


# ── Logs ──────────────────────────────────────────────────────────────────────

def set_log_channel(channel_id: Optional[int]):
    d = get()
    d["logs"]["channelId"] = channel_id
    save()


def get_log_channel() -> Optional[int]:
    return get()["logs"].get("channelId")


# ── Prefix ────────────────────────────────────────────────────────────────────

def get_prefix() -> str:
    return get_config().get("prefix", "+")


def set_prefix(prefix: str):
    set_config(["prefix"], prefix)
