"""
Alias Cog — server & personal (cross-server) command aliases.

  +alias add <alias> <command>       Create a server alias (per-guild)
  +alias remove <alias>              Remove a server alias
  +alias list                        List server aliases

  +alias self add <alias> <command>  Create a personal alias (works everywhere)
  +alias self remove <alias>         Remove a personal alias
  +alias self list                   List personal aliases

Rules
  • 30s cooldown per user on `add` (both scopes)
  • Max 3 aliases may point at the same target command (per scope)
  • Alias names must be alphanumeric (+ `_`/`-`), 1-32 chars, and must not
    shadow an existing real command name
  • The target command's root must be a real, registered bot command

Resolution of `+<alias>` into the real command happens in `Guardian.on_message`
(main.py), which calls `utils.alias_db.resolve()` before dispatching.
"""

from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import alias_db
from utils import db

COL = 0x2B2D31
FOOTER = "Guardian Security System"


def _embed(title: str, description: str, *, error: bool = False) -> discord.Embed:
    e = discord.Embed(
        title=title,
        description=description,
        color=0xC0392B if error else COL,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=FOOTER)
    return e


def _validate_target(bot: commands.Bot, command: str) -> tuple[bool, str]:
    """Ensure the target command's root actually exists on the bot."""
    root = command.strip().split(" ")[0] if command.strip() else ""
    if not root or bot.get_command(root) is None:
        return False, f"`{root or command}` is not a real bot command."
    return True, ""


def _validate_alias_name(bot: commands.Bot, alias: str) -> tuple[bool, str]:
    if not alias_db.is_valid_alias_name(alias):
        return False, "Alias names must be 1-32 characters: letters, numbers, `_` or `-` only."
    # Normalize BEFORE the shadowing check — aliases are stored lowercase, so
    # a case-variant like "Ping" must not slip past the real-command guard.
    normalized = alias_db.normalize_alias_name(alias)
    if bot.get_command(normalized) is not None:
        return False, f"`{normalized}` is already a real bot command name and cannot be used as an alias."
    return True, ""


class Alias(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Server aliases ───────────────────────────────────────────────────────────

    @commands.group(name="alias", invoke_without_command=True)
    async def alias_group(self, ctx: commands.Context):
        await ctx.send(embed=_embed(
            "Alias",
            "Use `+alias add/remove/list` (server) or `+alias self add/remove/list` (personal)."
        ))

    @alias_group.command(name="add")
    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.guild_only()
    async def alias_add(self, ctx: commands.Context, alias: str, *, command: str):
        ok, err = _validate_alias_name(self.bot, alias)
        if not ok:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed("Invalid Alias", err, error=True))

        ok, err = _validate_target(self.bot, command)
        if not ok:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed("Invalid Command", err, error=True))

        target_root = command.strip().split(" ")[0]
        if alias_db.count_guild_aliases_for_command(ctx.guild.id, target_root) >= alias_db.MAX_ALIASES_PER_COMMAND:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed(
                "Limit Reached", f"`{target_root}` already has the maximum of "
                f"`{alias_db.MAX_ALIASES_PER_COMMAND}` aliases in this server.", error=True,
            ))

        if alias_db.get_guild_alias(ctx.guild.id, alias) is not None:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed("Already Exists", f"`{alias}` is already an alias here.", error=True))

        alias_db.add_guild_alias(ctx.guild.id, alias, target_root, ctx.author.id)
        await ctx.send(embed=_embed(
            "Alias Created",
            f"• __**Alias**__\n`{ctx.prefix}{alias}`\n\n• __**Maps To**__\n`{ctx.prefix}{target_root}`\n\n"
            f"*Server-wide — anyone in this server can use it.*",
        ))

    @alias_group.command(name="remove")
    @commands.guild_only()
    async def alias_remove(self, ctx: commands.Context, alias: str):
        if alias_db.remove_guild_alias(ctx.guild.id, alias):
            await ctx.send(embed=_embed("Alias Removed", f"`{alias}` has been removed from this server."))
        else:
            await ctx.send(embed=_embed("Not Found", f"No server alias named `{alias}` exists.", error=True))

    @alias_group.command(name="list")
    @commands.guild_only()
    async def alias_list(self, ctx: commands.Context):
        rows = alias_db.list_guild_aliases(ctx.guild.id)
        if not rows:
            return await ctx.send(embed=_embed("Server Aliases", "No aliases have been created here yet."))
        lines = "\n".join(f"`{ctx.prefix}{r['alias']}` → `{ctx.prefix}{r['command']}`" for r in rows)
        await ctx.send(embed=_embed(f"Server Aliases — {len(rows)}", lines))

    # ── Personal (cross-server) aliases ────────────────────────────────────────

    @alias_group.group(name="self", invoke_without_command=True)
    async def alias_self(self, ctx: commands.Context):
        await ctx.send(embed=_embed("Personal Aliases", "Use `+alias self add/remove/list`."))

    @alias_self.command(name="add")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def alias_self_add(self, ctx: commands.Context, alias: str, *, command: str):
        ok, err = _validate_alias_name(self.bot, alias)
        if not ok:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed("Invalid Alias", err, error=True))

        ok, err = _validate_target(self.bot, command)
        if not ok:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed("Invalid Command", err, error=True))

        target_root = command.strip().split(" ")[0]
        if alias_db.count_personal_aliases_for_command(ctx.author.id, target_root) >= alias_db.MAX_ALIASES_PER_COMMAND:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed(
                "Limit Reached", f"`{target_root}` already has the maximum of "
                f"`{alias_db.MAX_ALIASES_PER_COMMAND}` personal aliases.", error=True,
            ))

        if alias_db.get_personal_alias(ctx.author.id, alias) is not None:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(embed=_embed("Already Exists", f"`{alias}` is already one of your aliases.", error=True))

        alias_db.add_personal_alias(ctx.author.id, alias, target_root)
        await ctx.send(embed=_embed(
            "Personal Alias Created",
            f"• __**Alias**__\n`{ctx.prefix}{alias}`\n\n• __**Maps To**__\n`{ctx.prefix}{target_root}`\n\n"
            f"*Works for you in every server I'm in, and in DMs.*",
        ))

    @alias_self.command(name="remove")
    async def alias_self_remove(self, ctx: commands.Context, alias: str):
        if alias_db.remove_personal_alias(ctx.author.id, alias):
            await ctx.send(embed=_embed("Personal Alias Removed", f"`{alias}` has been removed."))
        else:
            await ctx.send(embed=_embed("Not Found", f"You have no personal alias named `{alias}`.", error=True))

    @alias_self.command(name="list")
    async def alias_self_list(self, ctx: commands.Context):
        rows = alias_db.list_personal_aliases(ctx.author.id)
        if not rows:
            return await ctx.send(embed=_embed("Personal Aliases", "You have not created any personal aliases yet."))
        lines = "\n".join(f"`{ctx.prefix}{r['alias']}` → `{ctx.prefix}{r['command']}`" for r in rows)
        await ctx.send(embed=_embed(f"Personal Aliases — {len(rows)}", lines))

    # ── Error handlers ───────────────────────────────────────────────────────────

    @alias_add.error
    @alias_self_add.error
    async def _add_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                "Slow Down", f"You can create another alias in `{error.retry_after:.1f}s`.", error=True,
            ), delete_after=8)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed("Usage", f"`{ctx.prefix}{ctx.command.qualified_name} <alias> <command>`", error=True))
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send(embed=_embed("Server Only", "Server aliases can only be created inside a server.", error=True))

    @alias_remove.error
    @alias_self_remove.error
    async def _remove_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed("Usage", f"`{ctx.prefix}{ctx.command.qualified_name} <alias>`", error=True))


async def setup(bot: commands.Bot):
    alias_db.init()
    await bot.add_cog(Alias(bot))
