"""
Database layer — SQLite3 via aiosqlite.
All tables are keyed by guild_id to enforce strict per-server data isolation.
"""

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger("secbot.db")

DB_PATH = Path(__file__).parent / "security_bot.db"

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS whitelist (
    guild_id    INTEGER NOT NULL,
    target_id   INTEGER NOT NULL,
    target_type TEXT    NOT NULL CHECK (target_type IN ('user', 'role')),
    PRIMARY KEY (guild_id, target_id, target_type)
);

CREATE TABLE IF NOT EXISTS danger_roles (
    guild_id INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS danger_tags (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS role_cache (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, role_id)
);

CREATE TABLE IF NOT EXISTS server_settings (
    guild_id INTEGER NOT NULL,
    feature  TEXT    NOT NULL,
    status   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, feature)
);
"""


class Database:
    """Single shared aiosqlite connection used throughout the bot's lifetime."""

    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(str(DB_PATH))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_DDL)
        await self._conn.commit()
        log.info("Database ready: %s", DB_PATH)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _exec(self, sql: str, params: tuple = ()) -> None:
        await self._conn.execute(sql, params)
        await self._conn.commit()

    async def _fetchone(self, sql: str, params: tuple = ()):
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()):
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    # ── Whitelist ─────────────────────────────────────────────────────────────

    async def wl_add(self, guild_id: int, target_id: int, target_type: str) -> None:
        await self._exec(
            "INSERT OR IGNORE INTO whitelist VALUES (?, ?, ?)",
            (guild_id, target_id, target_type),
        )

    async def wl_remove(self, guild_id: int, target_id: int, target_type: str) -> bool:
        async with self._conn.execute(
            "DELETE FROM whitelist WHERE guild_id=? AND target_id=? AND target_type=?",
            (guild_id, target_id, target_type),
        ) as cur:
            changed = cur.rowcount > 0
        await self._conn.commit()
        return changed

    async def wl_check(self, guild_id: int, target_id: int, target_type: str) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM whitelist WHERE guild_id=? AND target_id=? AND target_type=?",
            (guild_id, target_id, target_type),
        )
        return row is not None

    async def wl_list(self, guild_id: int):
        return await self._fetchall(
            "SELECT target_id, target_type FROM whitelist WHERE guild_id=? ORDER BY target_type, target_id",
            (guild_id,),
        )

    # ── Danger roles ──────────────────────────────────────────────────────────

    async def danger_role_add(self, guild_id: int, role_id: int) -> None:
        await self._exec(
            "INSERT OR IGNORE INTO danger_roles VALUES (?, ?)",
            (guild_id, role_id),
        )

    async def danger_role_remove(self, guild_id: int, role_id: int) -> bool:
        async with self._conn.execute(
            "DELETE FROM danger_roles WHERE guild_id=? AND role_id=?",
            (guild_id, role_id),
        ) as cur:
            changed = cur.rowcount > 0
        await self._conn.commit()
        return changed

    async def danger_role_check(self, guild_id: int, role_id: int) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM danger_roles WHERE guild_id=? AND role_id=?",
            (guild_id, role_id),
        )
        return row is not None

    async def danger_role_list(self, guild_id: int):
        return await self._fetchall(
            "SELECT role_id FROM danger_roles WHERE guild_id=?",
            (guild_id,),
        )

    # ── Danger tags ───────────────────────────────────────────────────────────

    async def danger_tag_add(self, guild_id: int, user_id: int) -> None:
        await self._exec(
            "INSERT OR IGNORE INTO danger_tags VALUES (?, ?)",
            (guild_id, user_id),
        )

    async def danger_tag_remove(self, guild_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            "DELETE FROM danger_tags WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ) as cur:
            changed = cur.rowcount > 0
        await self._conn.commit()
        return changed

    async def danger_tag_check(self, guild_id: int, user_id: int) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM danger_tags WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        return row is not None

    async def danger_tag_list(self, guild_id: int):
        return await self._fetchall(
            "SELECT user_id FROM danger_tags WHERE guild_id=?",
            (guild_id,),
        )

    # ── Role cache ────────────────────────────────────────────────────────────

    async def role_cache_sync_member(
        self, guild_id: int, user_id: int, role_ids: list[int]
    ) -> None:
        """Atomically replace a member's cached role list."""
        async with self._conn.execute(
            "DELETE FROM role_cache WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ):
            pass
        if role_ids:
            await self._conn.executemany(
                "INSERT OR IGNORE INTO role_cache VALUES (?, ?, ?)",
                [(guild_id, user_id, rid) for rid in role_ids],
            )
        await self._conn.commit()

    async def role_cache_clear_guild(self, guild_id: int) -> None:
        await self._exec("DELETE FROM role_cache WHERE guild_id=?", (guild_id,))

    async def role_cache_get_members(self, guild_id: int, role_id: int) -> list[int]:
        rows = await self._fetchall(
            "SELECT user_id FROM role_cache WHERE guild_id=? AND role_id=?",
            (guild_id, role_id),
        )
        return [row["user_id"] for row in rows]

    async def role_cache_update_role_id(
        self, guild_id: int, old_role_id: int, new_role_id: int
    ) -> None:
        """Swap the old role ID for the newly recreated one in the cache."""
        await self._exec(
            "UPDATE role_cache SET role_id=? WHERE guild_id=? AND role_id=?",
            (new_role_id, guild_id, old_role_id),
        )

    # ── Server settings ───────────────────────────────────────────────────────

    async def setting_get(self, guild_id: int, feature: str, default: int = 1) -> int:
        row = await self._fetchone(
            "SELECT status FROM server_settings WHERE guild_id=? AND feature=?",
            (guild_id, feature),
        )
        return row["status"] if row else default

    async def setting_set(self, guild_id: int, feature: str, status: int) -> None:
        await self._exec(
            "INSERT INTO server_settings VALUES (?, ?, ?) "
            "ON CONFLICT (guild_id, feature) DO UPDATE SET status=excluded.status",
            (guild_id, feature, status),
        )
