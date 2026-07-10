"""
Gatekeeper — global access-control layer.

Two tiers of restriction, enforced BEFORE a command's body ever runs:

  WHITELIST_RESTRICTED  — requires db.is_whitelisted() OR db.is_owner()
  OWNER_RESTRICTED      — requires db.is_owner() (or per-guild co-owner, for
                           the handful of commands that also accept that tier)

Any unauthorized attempt raises `NotAuthorized`, which `on_command_error`
turns into the standard "You Cannot Use This Bot!" embed — so every
restricted command (including +help) shows one consistent response instead
of each cog silently swallowing the attempt.
"""

from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db
from utils import coowners

COL_DENY = 0xC0392B
WORLD_EMOJI = "<a:world:1500646005371175092>"

# Top-level command (or group) names that require at least whitelist access.
WHITELIST_RESTRICTED = {
    "whitelist", "owner", "setlog", "scaninvites", "antinuke", "automod",
    "userinfo", "checkalt", "botinfo", "bypass", "backup", "restore",
    "cloneroles", "loadrole", "clonerole", "loadchannel", "clonechannel",
    "lockdown", "unlock", "dj",
}

# Top-level command names that require full bot-owner access.
OWNER_RESTRICTED = {
    "help", "setprefix", "setavatar", "setbio",
    "addcoowner", "removecoowner", "coowners",
}

# Commands that accept Owner OR per-guild Co-Owner (kept in sync with owner.py).
CO_OWNER_ELIGIBLE = {"announce", "setnick"}


class NotAuthorized(commands.CheckFailure):
    """Raised by the global gatekeeper check when a user is not authorized."""


def _root_name(ctx: commands.Context) -> str:
    return ctx.command.qualified_name.split(" ")[0] if ctx.command else ""


def is_authorized(ctx: commands.Context) -> bool:
    """Core authorization decision used by the global check."""
    root = _root_name(ctx)

    if root in CO_OWNER_ELIGIBLE:
        if db.is_owner(ctx.author.id):
            return True
        if ctx.guild and coowners.is_coowner(ctx.guild.id, ctx.author.id):
            return True
        return False

    if root in OWNER_RESTRICTED:
        return db.is_owner(ctx.author.id)

    if root in WHITELIST_RESTRICTED:
        return db.is_whitelisted(ctx.author.id) or db.is_owner(ctx.author.id)

    # Not a restricted command — anyone may use it.
    return True


async def check_or_raise(ctx: commands.Context) -> bool:
    """Global check entry point — raises NotAuthorized instead of just
    returning False, so on_command_error can distinguish gatekeeper denials
    from unrelated CheckFailures raised by other decorators (cooldowns,
    guild_only, etc.)."""
    if is_authorized(ctx):
        return True
    raise NotAuthorized("You are not authorized to use this command.")


def denial_embed(bot: commands.Bot) -> discord.Embed:
    owners = db.get_owners()
    owner_mention = f"<@{owners[0]}>" if owners else "`Not configured`"

    e = discord.Embed(
        title="You Cannot Use This Bot!",
        description=f"{WORLD_EMOJI}  **Contact Bot Owner**",
        color=COL_DENY,
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="Owner", value=owner_mention, inline=False)
    if bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)
    e.set_footer(text="Guardian Security System")
    return e


async def setup(bot: commands.Bot):
    """Registers the global check. Call `await bot.load_extension('utils.gatekeeper')`
    is NOT used — instead this is wired directly from main.py via `bot.add_check`."""
    bot.add_check(is_authorized)
