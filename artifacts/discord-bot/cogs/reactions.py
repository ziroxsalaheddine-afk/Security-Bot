"""
Auto-Reaction Cog.

Two independent layers:

1. Command-confirmation reactions — a ✅ is added to the invoking message
   whenever a user successfully runs a command whose name contains "add",
   "remove", or "list". Gated by the same global on/off switch as (2).

2. Per-user auto-react — `+autoreact add @user :emoji:` binds a specific
   emoji to a specific user. While the global switch is ON, every message
   that user sends gets auto-reacted with their assigned emoji.

Persistence: both the global switch and the per-user emoji map live in
guardian.db.json via utils.db, under config.autoreact / config.autoreactUsers.
"""

import logging

import discord
from discord.ext import commands

from utils import db

log = logging.getLogger("guardian.reactions")

CONFIRM_EMOJI = "✅"
_KEYWORDS = ("add", "remove", "list")

COL_INFO = 0x2B2D31
COL_SUCCESS = 0x2ECC71
COL_DANGER = 0xC0392B


# ── Config helpers ──────────────────────────────────────────────────────────

def _is_enabled() -> bool:
    return db.get_config().get("autoreact", True)


def _set_enabled(value: bool):
    db.set_config(["autoreact"], value)


def _get_user_map() -> dict:
    """{user_id (str) -> emoji (str)}"""
    return db.get_config().get("autoreactUsers", {})


def _set_user_emoji(user_id: int, emoji: str):
    users = dict(_get_user_map())
    users[str(user_id)] = emoji
    db.set_config(["autoreactUsers"], users)


def _remove_user(user_id: int) -> bool:
    users = dict(_get_user_map())
    if str(user_id) not in users:
        return False
    del users[str(user_id)]
    db.set_config(["autoreactUsers"], users)
    return True


def _embed(title: str, description: str, color: int = COL_INFO) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text="Guardian Security System")
    return e


class Reactions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Command-confirmation reactions ──────────────────────────────────────
    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        if not _is_enabled():
            return
        if ctx.command is None:
            return

        qualname = ctx.command.qualified_name.lower()
        if not any(keyword in qualname for keyword in _KEYWORDS):
            return

        try:
            await ctx.message.add_reaction(CONFIRM_EMOJI)
        except Exception:
            pass  # missing permission / message deleted — non-critical

    # ── Per-user auto-react ──────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not _is_enabled():
            return

        emoji = _get_user_map().get(str(message.author.id))
        if not emoji:
            return

        try:
            await message.add_reaction(emoji)
        except Exception as exc:
            log.warning(
                "Failed to auto-react with %r for %s: %s", emoji, message.author, exc
            )

    # ── +autoreact command group ────────────────────────────────────────────
    @commands.group(name="autoreact", invoke_without_command=True)
    async def autoreact(self, ctx: commands.Context, state: str = None):
        if not db.is_whitelisted(ctx.author.id):
            return

        if state is None:
            status = "`ON`" if _is_enabled() else "`OFF`"
            return await ctx.send(embed=_embed(
                "Auto-React — Status",
                f"• __**Global Auto-React**__\n{status}\n\n"
                "Use `+autoreact on`/`off` to toggle it, `+autoreact add @user :emoji:` "
                "to bind a reaction, `+autoreact remove @user` to unbind it, or "
                "`+autoreact list` to see every configured user."
            ))

        state = state.lower()
        if state not in ("on", "off"):
            return await ctx.send(embed=_embed(
                "Auto-React — Invalid Usage",
                "• __**Usage**__\n`+autoreact on` / `+autoreact off`\n"
                "`+autoreact add @user :emoji:`\n`+autoreact remove @user`\n`+autoreact list`",
                color=COL_DANGER,
            ), delete_after=8)

        enabled = state == "on"
        _set_enabled(enabled)
        await ctx.send(embed=_embed(
            "Auto-React — Global Switch",
            f"Auto-react is now {'`ENABLED`' if enabled else '`DISABLED`'} "
            "for both command confirmations and per-user reactions.",
            color=COL_SUCCESS if enabled else COL_DANGER,
        ))

    @autoreact.command(name="add")
    async def autoreact_add(self, ctx: commands.Context, user: discord.User, emoji: str):
        if not db.is_whitelisted(ctx.author.id):
            return

        # Validate the emoji by attempting a throwaway reaction check —
        # cheapest reliable way to confirm discord.py/the API accepts it
        # (covers unicode emoji, custom guild emoji, and animated emoji).
        try:
            await ctx.message.add_reaction(emoji)
            await ctx.message.remove_reaction(emoji, ctx.me)
        except (discord.HTTPException, discord.NotFound):
            return await ctx.send(embed=_embed(
                "Auto-React — Invalid Emoji",
                f"• __**Emoji**__\n{emoji}\n\n"
                "That doesn't look like a valid emoji I can react with. "
                "Use a standard unicode emoji or a custom emoji from a server I'm in.",
                color=COL_DANGER,
            ), delete_after=8)

        _set_user_emoji(user.id, emoji)
        await ctx.send(embed=_embed(
            "Auto-React — User Added",
            f"• __**User**__\n{user.mention}\n\n• __**Emoji**__\n{emoji}\n\n"
            "I will now react to every message they send with this emoji "
            "while auto-react is globally `ON`.",
            color=COL_SUCCESS,
        ))

    @autoreact.command(name="remove")
    async def autoreact_remove(self, ctx: commands.Context, user: discord.User):
        if not db.is_whitelisted(ctx.author.id):
            return

        if not _remove_user(user.id):
            return await ctx.send(embed=_embed(
                "Auto-React — Not Found",
                f"• __**User**__\n{user.mention}\n\nThis user has no auto-react configured.",
                color=COL_DANGER,
            ), delete_after=5)

        await ctx.send(embed=_embed(
            "Auto-React — User Removed",
            f"• __**User**__\n{user.mention}\n\nAuto-react has been removed for this user.",
            color=COL_DANGER,
        ))

    @autoreact.command(name="list")
    async def autoreact_list(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return

        users = _get_user_map()
        status = "`ON`" if _is_enabled() else "`OFF`"

        if not users:
            desc = f"• __**Global Status**__\n{status}\n\nNo users are configured yet."
        else:
            lines = []
            for uid, emoji in users.items():
                member = ctx.guild.get_member(int(uid)) if ctx.guild else None
                label = member.mention if member else f"`{uid}`"
                lines.append(f"{emoji} — {label}")
            desc = f"• __**Global Status**__\n{status}\n\n• __**Configured Users**__\n" + "\n".join(lines)

        await ctx.send(embed=_embed("Auto-React — Configured Users", desc))

    @autoreact_add.error
    async def _autoreact_add_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.UserNotFound):
            await ctx.send(embed=_embed(
                "Auto-React — User Not Found",
                "Couldn't find that user. Mention them or use their ID.",
                color=COL_DANGER,
            ), delete_after=5)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "Auto-React — Missing Argument",
                "• __**Usage**__\n`+autoreact add @user :emoji:`",
                color=COL_DANGER,
            ), delete_after=5)

    @autoreact_remove.error
    async def _autoreact_remove_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.UserNotFound):
            await ctx.send(embed=_embed(
                "Auto-React — User Not Found",
                "Couldn't find that user. Mention them or use their ID.",
                color=COL_DANGER,
            ), delete_after=5)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "Auto-React — Missing Argument",
                "• __**Usage**__\n`+autoreact remove @user`",
                color=COL_DANGER,
            ), delete_after=5)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
