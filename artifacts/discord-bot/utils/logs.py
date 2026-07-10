"""
Centralized logging system.

Every moderation-relevant action (whitelist/bypass/DJ changes, warnings/jails,
message deletions, member join/leave, etc.) is funneled through `send()` so
it always produces the same shape of embed in the guild's configured log
channel — timestamp, actor avatar, and a clear description of what happened.

Configure the channel with `+setlog #channel` (see cogs/admin.py); until a
channel is set, `send()` is a no-op.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Union

import discord

from utils import db

log = logging.getLogger("guardian.logs")

FOOTER = "Guardian Security System"

COL_INFO = 0x2B2D31   # neutral (config changes, joins/leaves)
COL_WARN = 0xFFA500   # warnings / jails
COL_DANGER = 0xC0392B  # deletions, invite blocks, punishments
COL_SUCCESS = 0x2ECC71  # additive/positive changes (add, grant)


async def send(
    bot: discord.Client,
    guild: Optional[discord.Guild],
    title: str,
    description: str,
    *,
    user: Optional[Union[discord.User, discord.Member]] = None,
    color: int = COL_INFO,
    fields: Optional[list[tuple[str, str, bool]]] = None,
) -> None:
    """Send a standardized log embed to the guild's configured log channel.

    No-op (but logged locally) if no log channel is configured, the channel
    no longer exists, or the bot lacks permission to post there — logging
    failures must never interrupt the action that triggered them.
    """
    if guild is None:
        return

    ch_id = db.get_log_channel()
    if not ch_id:
        return

    channel = guild.get_channel(ch_id)
    if channel is None:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if user is not None:
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"{FOOTER}  ·  {user}  ({user.id})")
    else:
        embed.set_footer(text=FOOTER)

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    try:
        await channel.send(embed=embed)
    except Exception as exc:
        log.error("Failed to post log embed to channel %d: %s", ch_id, exc)
