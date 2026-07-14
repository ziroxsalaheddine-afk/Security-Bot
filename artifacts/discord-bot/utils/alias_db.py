"""
Alias database — SQLite3-backed, persistent across restarts.

Two independent alias scopes:
  • Server aliases   — scoped to a single guild (guild_id, alias) is unique
  • Personal aliases — scoped to a user, cross-server (user_id, alias) is unique,
                        works in every guild the bot shares with that user (and DMs)

A command may have at most MAX_ALIASES_PER_COMMAND aliases pointing at it,
enforced independently per-scope (per guild for server aliases, per user for
personal aliases).
"""

import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "aliases.sqlite3"
MAX_ALIASES_PER_COMMAND = 10

_lock = threading.Lock()
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    """Create tables if they do not exist yet. Call once at bot startup."""
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_aliases (
                guild_id    INTEGER NOT NULL,
                alias       TEXT    NOT NULL,
                command     TEXT    NOT NULL,
                creator_id  INTEGER NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, alias)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal_aliases (
                user_id     INTEGER NOT NULL,
                alias       TEXT    NOT NULL,
                command     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, alias)
            )
            """
        )
        conn.commit()


def is_valid_alias_name(name: str) -> bool:
    return bool(_ALIAS_RE.match(name))


def normalize_alias_name(name: str) -> str:
    """Aliases are stored/looked-up lowercase; always normalize BEFORE any
    real-command-name collision check so `Ping`/`PING` can't sneak past the
    shadowing guard and silently alias over a real command."""
    return name.lower()


# ── Server aliases ──────────────────────────────────────────────────────────────

def add_guild_alias(guild_id: int, alias: str, command: str, creator_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO guild_aliases (guild_id, alias, command, creator_id) VALUES (?, ?, ?, ?)",
            (guild_id, alias.lower(), command, creator_id),
        )
        conn.commit()


def remove_guild_alias(guild_id: int, alias: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM guild_aliases WHERE guild_id = ? AND alias = ?",
            (guild_id, alias.lower()),
        )
        conn.commit()
        return cur.rowcount > 0


def get_guild_alias(guild_id: int, alias: str) -> Optional[str]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT command FROM guild_aliases WHERE guild_id = ? AND alias = ?",
            (guild_id, alias.lower()),
        ).fetchone()
        return row["command"] if row else None


def list_guild_aliases(guild_id: int) -> list[sqlite3.Row]:
    with _lock, _connect() as conn:
        return conn.execute(
            "SELECT alias, command, creator_id FROM guild_aliases WHERE guild_id = ? ORDER BY alias",
            (guild_id,),
        ).fetchall()


def count_guild_aliases_for_command(guild_id: int, command: str) -> int:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM guild_aliases WHERE guild_id = ? AND command = ?",
            (guild_id, command),
        ).fetchone()
        return int(row["c"])


# ── Personal (cross-server) aliases ─────────────────────────────────────────────

def add_personal_alias(user_id: int, alias: str, command: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO personal_aliases (user_id, alias, command) VALUES (?, ?, ?)",
            (user_id, alias.lower(), command),
        )
        conn.commit()


def remove_personal_alias(user_id: int, alias: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM personal_aliases WHERE user_id = ? AND alias = ?",
            (user_id, alias.lower()),
        )
        conn.commit()
        return cur.rowcount > 0


def get_personal_alias(user_id: int, alias: str) -> Optional[str]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT command FROM personal_aliases WHERE user_id = ? AND alias = ?",
            (user_id, alias.lower()),
        ).fetchone()
        return row["command"] if row else None


def list_personal_aliases(user_id: int) -> list[sqlite3.Row]:
    with _lock, _connect() as conn:
        return conn.execute(
            "SELECT alias, command FROM personal_aliases WHERE user_id = ? ORDER BY alias",
            (user_id,),
        ).fetchall()


def count_personal_aliases_for_command(user_id: int, command: str) -> int:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM personal_aliases WHERE user_id = ? AND command = ?",
            (user_id, command),
        ).fetchone()
        return int(row["c"])


# ── Resolution (used by the message listener in main.py) ───────────────────────

def resolve(prefix: str, content: str, author_id: int, guild_id: Optional[int]) -> Optional[str]:
    """
    Given raw message content, return the rewritten command string if the
    first token matches a registered alias, else None.

    Personal aliases are checked first (they work everywhere), then
    server aliases if the message came from a guild.
    """
    if not content.startswith(prefix):
        return None
    body = content[len(prefix):]
    if not body:
        return None
    first, _, rest = body.partition(" ")
    if not first:
        return None

    target = get_personal_alias(author_id, first)
    if target is None and guild_id is not None:
        target = get_guild_alias(guild_id, first)
    if target is None:
        return None

    return f"{prefix}{target} {rest}".rstrip() if rest else f"{prefix}{target}"
