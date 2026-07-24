"""
Shared helpers used across every cog.
No emojis in any user-facing text.
"""

from __future__ import annotations

import discord
from database import Database

FOOTER = "\u00a9 2026 \u2014 developed by zrx.gg"


# ── Embed factories ──────────────────────────────────────────────────────────

def success_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=0x2ECC71)
    e.set_footer(text=FOOTER)
    return e


def error_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=0xE74C3C)
    e.set_footer(text=FOOTER)
    return e


def warn_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=0xE67E22)
    e.set_footer(text=FOOTER)
    return e


def info_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=0x5865F2)
    e.set_footer(text=FOOTER)
    return e


def log_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=0x95A5A6)
    e.set_footer(text=FOOTER)
    return e


# ── Whitelist check ──────────────────────────────────────────────────────────

async def is_whitelisted(db: Database, guild: discord.Guild, member: discord.Member) -> bool:
    """
    Return True if the member is exempt from security enforcement.

    Whitelisted if ANY of the following:
      1. They are the server owner.
      2. Their user_id appears directly in the whitelist table.
      3. Any of their assigned roles appears in the whitelist table.
    """
    if member.id == guild.owner_id:
        return True
    if await db.wl_check(guild.id, member.id, "user"):
        return True
    for role in member.roles:
        if role.is_default():
            continue
        if await db.wl_check(guild.id, role.id, "role"):
            return True
    return False


# ── Audit log helper ─────────────────────────────────────────────────────────

async def get_audit_executor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int,
    limit: int = 5,
) -> discord.User | None:
    """Return the User responsible for *action* on *target_id*, or None."""
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            if entry.target and entry.target.id == target_id:
                return entry.user
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


# ── Log channel helper ────────────────────────────────────────────────────────

async def send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    """Send embed to the first text channel the bot can write to."""
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
            return
