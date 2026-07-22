"""
Auto-Backup Cog
═══════════════
+auto backup on   — Enable a 5-minute rolling backup for this server.
+auto backup off  — Stop the backup loop and confirm in chat.

How it works
────────────
• When enabled for a server, an asyncio Task runs a loop that sleeps for
  exactly 300 seconds (5 minutes) between each cycle.

• Each cycle captures:
    – Every channel's name, type, topic, NSFW flag, category, and position.
    – The last 25 messages of every text channel (author, content, timestamp,
      attachment URLs) — enough for a meaningful audit trail.

• Rotation — "delete old, write new":
    On every cycle the previous backup file is deleted atomically and replaced
    with the freshest snapshot.  The server's backup is always ≤ 5 minutes old.

• Persistence — backup state (on/off per guild) is stored in:
    backups/autobackup_state.json
  so the correct guilds resume auto-backup if the bot restarts.

• On-disk format:
    backups/autobackup_{guild_id}.json

Permissions
───────────
The command requires the invoker to be the global bot owner or a server
co-owner (the same bar used by the rest of the Backup cog).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from utils import db
from utils import coowners

log = logging.getLogger("guardian.autobackup")

# ── Constants ──────────────────────────────────────────────────────────────────

COL       = 0x2B2D31
COL_OK    = 0x2ECC71
COL_WARN  = 0xE67E22
FOOTER    = "© 2026 — developed by zrx.gg"

BACKUPS_DIR   = Path(__file__).parent.parent / "backups"
STATE_FILE    = BACKUPS_DIR / "autobackup_state.json"
INTERVAL      = 300   # seconds between backups (5 minutes)
HISTORY_LIMIT = 25    # messages per text channel

BACKUPS_DIR.mkdir(exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _has_elevated(ctx: commands.Context) -> bool:
    """Returns True if the invoker is the global owner or a server co-owner."""
    if not ctx.guild:
        return db.is_owner(ctx.author.id)
    return db.is_owner(ctx.author.id) or coowners.is_coowner(ctx.guild.id, ctx.author.id)


def _embed(description: str, *, color: int = COL) -> discord.Embed:
    e = discord.Embed(
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=FOOTER)
    return e


# ── State persistence ──────────────────────────────────────────────────────────

def _load_state() -> dict[str, bool]:
    """
    Load the per-guild enabled map from disk.
    Returns {guild_id_str: enabled_bool}.
    """
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, bool]) -> None:
    """Atomically write the enabled-map to disk."""
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _set_guild_state(guild_id: int, enabled: bool) -> None:
    state = _load_state()
    state[str(guild_id)] = enabled
    _save_state(state)


def _get_guild_state(guild_id: int) -> bool:
    return _load_state().get(str(guild_id), False)


# ── Backup path helper ─────────────────────────────────────────────────────────

def _backup_path(guild_id: int) -> Path:
    return BACKUPS_DIR / f"autobackup_{guild_id}.json"


# ── Core snapshot function ─────────────────────────────────────────────────────

async def _snapshot(guild: discord.Guild) -> dict:
    """
    Capture a lightweight but thorough snapshot of the guild's current state.

    Captured data:
      meta     – guild name, id, member count, snapshot timestamp
      channels – every channel (name, type, topic, NSFW, category, position)
                 plus the last 25 messages of every text channel
    """
    now = datetime.now(timezone.utc)

    data: dict = {
        "meta": {
            "guild_id":     guild.id,
            "guild_name":   guild.name,
            "backed_up_at": now.isoformat(),
            "member_count": guild.member_count,
            "channel_count": len(guild.channels),
        },
        "channels": [],
    }

    for ch in sorted(guild.channels, key=lambda c: (c.position, c.name)):
        entry: dict = {
            "id":          ch.id,
            "name":        ch.name,
            "type":        str(ch.type),
            "position":    ch.position,
            "category":    ch.category.name if ch.category else None,
            "category_id": ch.category_id,
            "history":     [],
        }

        if isinstance(ch, discord.TextChannel):
            entry["topic"]    = ch.topic
            entry["nsfw"]     = ch.is_nsfw()
            entry["slowmode"] = ch.slowmode_delay

            # Capture the last HISTORY_LIMIT messages.
            try:
                async for msg in ch.history(limit=HISTORY_LIMIT, oldest_first=False):
                    entry["history"].append({
                        "author":      str(msg.author),
                        "author_id":   msg.author.id,
                        "content":     msg.content,
                        "timestamp":   msg.created_at.isoformat(),
                        "attachments": [a.url for a in msg.attachments],
                    })
            except (discord.Forbidden, discord.HTTPException):
                # Bot lacks Read Message History permission in this channel — skip.
                pass

        elif isinstance(ch, discord.VoiceChannel):
            entry["bitrate"]    = ch.bitrate
            entry["user_limit"] = ch.user_limit

        elif isinstance(ch, discord.StageChannel):
            entry["bitrate"] = ch.bitrate

        data["channels"].append(entry)

    return data


# ══════════════════════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════════════════════

class AutoBackup(commands.Cog, name="AutoBackup"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Active backup tasks keyed by guild_id.
        # Each value is the asyncio.Task running that guild's backup loop.
        self._tasks: dict[int, asyncio.Task] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        Resume auto-backup for every guild whose state was 'enabled' when the
        bot last shut down.  Called once when the bot connects to Discord.
        """
        state = _load_state()
        resumed = 0
        for guild_id_str, enabled in state.items():
            if not enabled:
                continue
            guild_id = int(guild_id_str)
            guild    = self.bot.get_guild(guild_id)
            if guild is None:
                continue  # Bot left that guild while offline.
            if guild_id not in self._tasks or self._tasks[guild_id].done():
                self._tasks[guild_id] = asyncio.create_task(
                    self._backup_loop(guild_id),
                    name=f"autobackup-{guild_id}",
                )
                resumed += 1
        if resumed:
            log.info("Auto-backup resumed for %d guild(s).", resumed)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Stop and clean up the backup task when the bot leaves a guild."""
        self._stop_task(guild.id)
        _set_guild_state(guild.id, False)

    # ── Internal loop ──────────────────────────────────────────────────────────

    async def _backup_loop(self, guild_id: int) -> None:
        """
        Runs forever (until cancelled or the guild disables auto-backup).

        Cycle:
          1. Sleep for INTERVAL seconds.
          2. Verify the guild is still accessible and backup is still enabled.
          3. Take a fresh snapshot.
          4. Delete the old backup file (if it exists).
          5. Atomically write the new backup file in its place.
        """
        log.info("Auto-backup loop started for guild %d.", guild_id)

        while True:
            # ── Wait the full interval before the first capture. ───────────────
            # This means the first snapshot arrives 5 minutes after `+auto backup on`,
            # not immediately (use +backup create for an instant snapshot).
            await asyncio.sleep(INTERVAL)

            # ── Re-check state in case `+auto backup off` ran mid-sleep. ────────
            if not _get_guild_state(guild_id):
                log.info("Auto-backup disabled — stopping loop for guild %d.", guild_id)
                return

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                log.warning("Guild %d not found — stopping auto-backup loop.", guild_id)
                _set_guild_state(guild_id, False)
                return

            # ── Take snapshot ────────────────────────────────────────────────────
            try:
                data = await _snapshot(guild)
            except Exception as exc:
                log.error("Auto-backup snapshot failed for guild %d: %s", guild_id, exc)
                continue   # Skip this cycle, try again next interval.

            # ── Rotation: delete old → write new (atomic via .tmp) ──────────────
            path = _backup_path(guild_id)
            try:
                if path.exists():
                    path.unlink()

                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp.replace(path)   # Atomic rename — never leaves a half-written file.

                ch_count  = len(data["channels"])
                msg_count = sum(len(c["history"]) for c in data["channels"])
                size_kb   = round(path.stat().st_size / 1024, 1)

                log.info(
                    "Auto-backup complete: guild %d (%s) — %d channels, %d messages, %.1f KB",
                    guild_id, guild.name, ch_count, msg_count, size_kb,
                )
            except Exception as exc:
                log.error("Auto-backup write failed for guild %d: %s", guild_id, exc)

    def _stop_task(self, guild_id: int) -> bool:
        """Cancel the backup task for a guild.  Returns True if a task was running."""
        task = self._tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # ══════════════════════════════════════════════════════════════════════════
    #  Commands
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(name="auto", invoke_without_command=True)
    @commands.guild_only()
    async def auto(self, ctx: commands.Context) -> None:
        """
        Auto-utility group.  Shows usage when invoked without a subcommand.
        """
        if not _has_elevated(ctx):
            return
        await ctx.send(embed=_embed(
            "• __**Auto Utilities**__\n\n"
            "• `+auto backup on`  — Enable 5-minute rolling backup for this server.\n"
            "• `+auto backup off` — Stop the auto-backup loop for this server.\n\n"
            "*Each backup replaces the previous one so it is always up to date.*"
        ))

    # ── +auto backup <on|off> ─────────────────────────────────────────────────

    @auto.command(name="backup")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def auto_backup(self, ctx: commands.Context, state: str) -> None:
        """
        Toggle the 5-minute auto-backup for this server.

        Usage:
          +auto backup on   — start the loop (first backup in 5 minutes)
          +auto backup off  — stop the loop immediately
        """
        if not _has_elevated(ctx):
            return

        state = state.lower().strip()
        if state not in ("on", "off"):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n"
                "`+auto backup on` — enable auto-backup\n"
                "`+auto backup off` — disable auto-backup",
                color=COL_WARN,
            ))
            return

        guild_id = ctx.guild.id

        if state == "on":
            # ── Enable ────────────────────────────────────────────────────────
            if guild_id in self._tasks and not self._tasks[guild_id].done():
                await ctx.send(embed=_embed(
                    "• __**Already Running**__\n"
                    "Auto-backup is already active for this server.\n"
                    f"Next snapshot in `{INTERVAL // 60}` minutes.\n\n"
                    "Use `+auto backup off` to stop it first.",
                    color=COL_WARN,
                ))
                return

            # Persist state and spawn the loop task.
            _set_guild_state(guild_id, True)
            self._tasks[guild_id] = asyncio.create_task(
                self._backup_loop(guild_id),
                name=f"autobackup-{guild_id}",
            )

            log.info(
                "Auto-backup ENABLED for guild '%s' (%d) by %s",
                ctx.guild.name, guild_id, ctx.author,
            )
            await ctx.send(embed=_embed(
                f"• __**Auto-Backup Enabled — {ctx.guild.name}**__\n\n"
                f"A fresh backup will be taken every `{INTERVAL // 60} minutes`.\n"
                f"Each run **deletes** the previous backup and saves a new one.\n\n"
                f"• First snapshot: `{INTERVAL // 60} minutes from now`\n"
                f"• Backup file: `backups/autobackup_{guild_id}.json`\n"
                f"• Captures: channel names · topics · last `{HISTORY_LIMIT}` messages\n\n"
                f"Use `+auto backup off` to stop the loop at any time.",
                color=COL_OK,
            ))

        else:
            # ── Disable ───────────────────────────────────────────────────────
            was_running = self._stop_task(guild_id)
            _set_guild_state(guild_id, False)

            log.info(
                "Auto-backup DISABLED for guild '%s' (%d) by %s",
                ctx.guild.name, guild_id, ctx.author,
            )

            if was_running:
                msg = (
                    f"• __**Auto-Backup Disabled — {ctx.guild.name}**__\n\n"
                    "The backup loop has been **stopped**.\n"
                    "No further automatic snapshots will be taken.\n\n"
                    f"The most recent backup file (`autobackup_{guild_id}.json`) "
                    "is still available on disk.\n"
                    "Use `+auto backup on` to restart the loop."
                )
            else:
                msg = (
                    f"• __**Auto-Backup Was Not Running**__\n\n"
                    f"Auto-backup was already off for **{ctx.guild.name}**.\n"
                    "Use `+auto backup on` to enable it."
                )

            await ctx.send(embed=_embed(msg, color=COL_WARN if not was_running else COL))

    # ── Error handler ──────────────────────────────────────────────────────────

    @auto_backup.error
    async def _backup_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n"
                "`+auto backup on`  — Enable 5-minute auto-backup\n"
                "`+auto backup off` — Disable auto-backup",
                color=COL_WARN,
            ), delete_after=12)
        elif isinstance(error, commands.NoPrivateMessage):
            pass   # guild_only check — silently ignore DM attempts
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                f"• __**Cooldown**__\nPlease wait `{error.retry_after:.0f}s` before toggling again.",
                color=COL_WARN,
            ), delete_after=8)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoBackup(bot))
