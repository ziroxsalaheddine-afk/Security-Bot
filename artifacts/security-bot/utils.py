"""
Shared helpers used across every cog.
"""

from __future__ import annotations

import discord
from database import Database

# ── Embed factories ──────────────────────────────────────────────────────────

FOOTER = "Security Bot • per-server isolation"


def success_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=f"✅  {title}", description=description, color=0x2ECC71)
    e.set_footer(text=FOOTER)
    return e


def error_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=f"❌  {title}", description=description, color=0xE74C3C)
    e.set_footer(text=FOOTER)
    return e


def warn_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=f"⚠️  {title}", description=description, color=0xE67E22)
    e.set_footer(text=FOOTER)
    return e


def info_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=f"ℹ️  {title}", description=description, color=0x5865F2)
    e.set_footer(text=FOOTER)
    return e


# ── Whitelist check ──────────────────────────────────────────────────────────

async def is_whitelisted(db: Database, guild: discord.Guild, member: discord.Member) -> bool:
    """
    Return True if the member is exempt from security enforcement.

    A member is whitelisted if ANY of the following is true:
      1. They are the server owner.
      2. Their user_id is directly in the whitelist table for this guild.
      3. Any of their assigned roles has a role_id in the whitelist table.
    """
    if member.id == guild.owner_id:
        return True

    # Direct user whitelist check.
    if await db.wl_check(guild.id, member.id, "user"):
        return True

    # Role-based whitelist check — skip @everyone (default role).
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
    """
    Return the User responsible for *action* on *target_id* from the audit log,
    or None if not found / insufficient permissions.
    """
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            if entry.target and entry.target.id == target_id:
                return entry.user
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None
