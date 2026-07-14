"""
Shared notification helpers — DM warnings to users + owner alerts.

Used by antinuke, antiraid, and any other security cog that needs to
notify the perpetrator and/or the bot owner about a security event.
"""

import logging

import discord

from utils import db

log = logging.getLogger("guardian.notifications")

COL_WARN  = 0xC0392B   # red — shown to the offending user
COL_ALERT = 0xFF8C00   # orange — shown to the owner
FOOTER    = "Guardian Security System"


async def dm_warn_user(
    bot: discord.Client,
    user: discord.User | discord.Member,
    guild_name: str,
    reason: str,
) -> None:
    """
    Send a warning DM to the user who triggered a security action.
    Silently swallows all errors (user has DMs closed, bot blocked, etc.).
    The embed explicitly says 'warned' / 'warning' — never 'jailed'.
    """
    try:
        e = discord.Embed(
            title="⚠️  You Have Been Warned",
            description=(
                f"You have received a **warning** in **{guild_name}**.\n\n"
                f"**Reason:** {reason}\n\n"
                "Your actions were flagged by the Guardian security system "
                "and a moderation action has been applied to your account. "
                "Further violations may result in stricter enforcement."
            ),
            color=COL_WARN,
        )
        e.set_footer(text=FOOTER)
        await user.send(embed=e)
    except Exception as exc:
        log.debug("Could not send warning DM to user %s: %s", getattr(user, "id", "?"), exc)


async def dm_owner_alert(
    bot: discord.Client,
    title: str,
    description: str,
) -> None:
    """
    Send an alert DM to every configured bot owner.
    Silently swallows failures (owner unreachable, DMs closed, etc.).
    """
    owner_ids = db.get_owners()
    for owner_id in owner_ids:
        try:
            owner = await bot.fetch_user(owner_id)
            e = discord.Embed(
                title=title,
                description=description,
                color=COL_ALERT,
            )
            e.set_footer(text=FOOTER)
            await owner.send(embed=e)
        except Exception as exc:
            log.debug("Could not send alert DM to owner %d: %s", owner_id, exc)
