"""
Auto-Reaction Cog.

Two independent layers:

1. Command-confirmation reactions — a ✅ is added to the invoking message
   whenever a user successfully runs a command whose name contains "add",
   "remove", or "list". Gated by the same global on/off switch as (2).

2. Per-user auto-react — binds a specific emoji to a specific user per guild.
   While the global switch is ON, every message that user sends gets
   auto-reacted with their assigned emoji.

Commands:
  +autoreact [on|off]               — toggle global auto-react switch
  +autoreact <@user|id> <emoji>     — set / update a user's emoji (shorthand)
  +autoreact add <@user|id> <emoji> — same as above (explicit subcommand)
  +autoreact remove <@user|id>      — remove a user
  +autoreact list                   — show all configured users
  +unautoreact <@user|id>           — alias for +autoreact remove

Persistence:
  Per-guild autoreact entries live in guardian.db.json under the top-level
  "autoreact" key: { guild_id_str: { user_id_str: emoji_str } }.
  The legacy config.autoreact (global switch) and config.autoreactUsers
  keys are still used for the on/off toggle and remain unchanged.
"""

import logging

import discord
from discord.ext import commands

from utils import db

log = logging.getLogger("guardian.reactions")

CONFIRM_EMOJI = "✅"
_KEYWORDS = ("add", "remove", "list")

COL_INFO    = 0x2B2D31
COL_SUCCESS = 0x2ECC71
COL_DANGER  = 0xC0392B
COL_WARN    = 0xE67E22


# ── Config helpers ──────────────────────────────────────────────────────────

def _is_enabled() -> bool:
    return db.get_config().get("autoreact", True)


def _set_enabled(value: bool):
    db.set_config(["autoreact"], value)


def _embed(title: str, description: str, color: int = COL_INFO) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text="Guardian Security System")
    return e


def _is_authorized(ctx: commands.Context) -> bool:
    """Allow whitelisted users, server owners, and Manage Guild members."""
    if db.is_whitelisted(ctx.author.id):
        return True
    if ctx.guild and ctx.author.id == ctx.guild.owner_id:
        return True
    if isinstance(ctx.author, discord.Member):
        return ctx.author.guild_permissions.manage_guild
    return False


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

    # ── Per-user auto-react (on_message) ────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not _is_enabled():
            return

        emoji = db.get_autoreact_emoji(message.guild.id, message.author.id)
        if not emoji:
            return

        try:
            await message.add_reaction(emoji)
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            pass  # invalid emoji or missing permissions — silent

    # ── Emoji validation helper ─────────────────────────────────────────────
    async def _validate_emoji(self, ctx: commands.Context, emoji: str) -> bool:
        """Return True if the emoji is usable; send an error embed and return
        False if it is not."""
        try:
            await ctx.message.add_reaction(emoji)
            await ctx.message.remove_reaction(emoji, ctx.guild.me)
            return True
        except (discord.HTTPException, discord.NotFound):
            await ctx.send(embed=_embed(
                "Auto-React — Invalid Emoji",
                f"• __**Emoji**__\n{emoji}\n\n"
                "That doesn't look like a valid emoji I can react with. "
                "Use a standard unicode emoji or a custom emoji from a server I'm in.",
                color=COL_DANGER,
            ), delete_after=8)
            return False

    # ── +autoreact command group ────────────────────────────────────────────
    @commands.group(name="autoreact", invoke_without_command=True)
    @commands.guild_only()
    async def autoreact(self, ctx: commands.Context, target: discord.Member = None, *, emoji: str = None):
        """
        Without subcommand:
          +autoreact               → show status
          +autoreact on|off        → toggle global switch
          +autoreact @user emoji   → shorthand for autoreact add
        """
        if not _is_authorized(ctx):
            return

        # Shorthand: +autoreact @user emoji
        if target is not None and emoji:
            if not await self._validate_emoji(ctx, emoji):
                return
            db.set_autoreact(ctx.guild.id, target.id, emoji)
            log.info("Auto-react set: guild=%d user=%d emoji=%s by %s",
                     ctx.guild.id, target.id, emoji, ctx.author)
            return await ctx.send(embed=_embed(
                "Auto-React — Set",
                f"• __**User**__\n{target.mention}\n\n• __**Emoji**__\n{emoji}\n\n"
                "I'll react to every message they send with this emoji.",
                color=COL_SUCCESS,
            ))

        # Status / on / off
        if target is None:
            status = "`ON`" if _is_enabled() else "`OFF`"
            return await ctx.send(embed=_embed(
                "Auto-React — Status",
                f"• __**Global Auto-React**__\n{status}\n\n"
                "Use `+autoreact on`/`off` to toggle, "
                "`+autoreact add @user :emoji:` to bind, "
                "`+autoreact remove @user` to unbind, "
                "or `+autoreact list` to list all.",
            ))

        # target was parsed as a Member but emoji is missing — treat as on/off
        # (discord.py may parse "on"/"off" as a Member lookup and fail, so we
        #  also handle the string case via the error handler below).
        await ctx.send(embed=_embed(
            "Auto-React — Usage",
            "• __**Usage**__\n`+autoreact on` / `+autoreact off`\n"
            "`+autoreact add @user :emoji:`\n"
            "`+autoreact remove @user`\n`+autoreact list`",
            color=COL_WARN,
        ), delete_after=10)

    @autoreact.error
    async def autoreact_error(self, ctx: commands.Context, error: commands.CommandError):
        """Handle on/off strings that fail the Member converter."""
        if isinstance(error, commands.BadArgument):
            # The first positional arg was not a valid Member — check if it's on/off.
            raw = ctx.message.content.strip().split()
            if len(raw) >= 2:
                state = raw[1].lower()
                if state in ("on", "off"):
                    if not _is_authorized(ctx):
                        return
                    enabled = state == "on"
                    _set_enabled(enabled)
                    return await ctx.send(embed=_embed(
                        "Auto-React — Global Switch",
                        f"Auto-react is now {'`ENABLED`' if enabled else '`DISABLED`'} "
                        "for both command confirmations and per-user reactions.",
                        color=COL_SUCCESS if enabled else COL_DANGER,
                    ))
        # Let other errors fall through silently or be logged.
        log.debug("autoreact error (ignored): %s", error)

    @autoreact.command(name="add")
    async def autoreact_add(self, ctx: commands.Context, user: discord.Member, *, emoji: str):
        if not _is_authorized(ctx):
            return
        if not await self._validate_emoji(ctx, emoji):
            return
        db.set_autoreact(ctx.guild.id, user.id, emoji)
        log.info("Auto-react set: guild=%d user=%d emoji=%s by %s",
                 ctx.guild.id, user.id, emoji, ctx.author)
        await ctx.send(embed=_embed(
            "Auto-React — User Added",
            f"• __**User**__\n{user.mention}\n\n• __**Emoji**__\n{emoji}\n\n"
            "I will now react to every message they send with this emoji "
            "while auto-react is globally `ON`.",
            color=COL_SUCCESS,
        ))

    @autoreact.command(name="remove")
    async def autoreact_remove(self, ctx: commands.Context, user: discord.Member):
        if not _is_authorized(ctx):
            return
        removed = db.remove_autoreact(ctx.guild.id, user.id)
        if not removed:
            return await ctx.send(embed=_embed(
                "Auto-React — Not Found",
                f"• __**User**__\n{user.mention}\n\nThis user has no auto-react configured.",
                color=COL_DANGER,
            ), delete_after=5)
        log.info("Auto-react removed: guild=%d user=%d by %s", ctx.guild.id, user.id, ctx.author)
        await ctx.send(embed=_embed(
            "Auto-React — User Removed",
            f"• __**User**__\n{user.mention}\n\nAuto-react has been removed for this user.",
            color=COL_DANGER,
        ))

    @autoreact.command(name="list")
    async def autoreact_list(self, ctx: commands.Context):
        if not _is_authorized(ctx):
            return
        mapping = db.get_autoreact_map(ctx.guild.id)
        status  = "`ON`" if _is_enabled() else "`OFF`"
        if not mapping:
            desc = f"• __**Global Status**__\n{status}\n\nNo users are configured yet."
        else:
            lines = []
            for uid, emoji in mapping.items():
                member = ctx.guild.get_member(int(uid))
                label  = member.mention if member else f"`{uid}`"
                lines.append(f"{emoji} — {label}")
            desc = (
                f"• __**Global Status**__\n{status}\n\n"
                f"• __**Configured Users**__\n" + "\n".join(lines)
            )
        await ctx.send(embed=_embed("Auto-React — Configured Users", desc))

    # ── +unautoreact (top-level alias for remove) ───────────────────────────
    @commands.command(name="unautoreact")
    @commands.guild_only()
    async def unautoreact(self, ctx: commands.Context, user: discord.Member = None):
        """Remove a user's auto-react entry.  Usage: +unautoreact <@user|id>"""
        if not _is_authorized(ctx):
            return
        if user is None:
            return await ctx.send(embed=_embed(
                "Auto-React — Usage",
                "**Usage:** `+unautoreact <@user|user_id>`",
                color=COL_WARN,
            ), delete_after=8)
        removed = db.remove_autoreact(ctx.guild.id, user.id)
        if not removed:
            return await ctx.send(embed=_embed(
                "Auto-React — Not Found",
                f"• __**User**__\n{user.mention}\n\nThis user has no auto-react configured.",
                color=COL_DANGER,
            ), delete_after=5)
        log.info("Auto-react removed via +unautoreact: guild=%d user=%d by %s",
                 ctx.guild.id, user.id, ctx.author)
        await ctx.send(embed=_embed(
            "Auto-React — Removed",
            f"• __**User**__\n{user.mention}\n\nAuto-react has been removed.",
            color=COL_SUCCESS,
        ))

    # ── Error handlers ──────────────────────────────────────────────────────
    @autoreact_add.error
    async def _autoreact_add_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MemberNotFound):
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
        if isinstance(error, commands.MemberNotFound):
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

    @unautoreact.error
    async def _unautoreact_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(embed=_embed(
                "Auto-React — User Not Found",
                "Couldn't find that user. Mention them or use their ID.",
                color=COL_DANGER,
            ), delete_after=5)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
