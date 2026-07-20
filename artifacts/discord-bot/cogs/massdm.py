"""
MassDM Cog — Owner-only mass direct-message command
═════════════════════════════════════════════════════

+massdm <server_id> <limit> <message>

  • DM-only: silently ignored if run in a guild channel
  • Owner-only: @commands.is_owner() guard
  • Excludes bots and server administrators from the target list
  • Live progress tracker edited after every send attempt
  • 1 250 ms sleep between every DM to respect Discord rate limits
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

log = logging.getLogger("guardian.massdm")

COL     = 0x2B2D31
COL_ERR = 0xC0392B
COL_OK  = 0x27AE60
FOOTER  = "© 2026 — developed by zrx.gg"

DM_SLEEP = 1.25   # 1 250 ms — safe per-user DM rate limit


# ── Helpers ────────────────────────────────────────────────────────────────────

def _embed(description: str, *, color: int = COL) -> discord.Embed:
    e = discord.Embed(
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=FOOTER)
    return e


# ── Cog ────────────────────────────────────────────────────────────────────────

class MassDM(commands.Cog):
    """Owner-only mass DM tool (DM context only)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Command ────────────────────────────────────────────────────────────────

    @commands.command(name="massdm")
    @commands.is_owner()
    async def massdm(
        self,
        ctx: commands.Context,
        server_id: int,
        limit: int,
        *,
        message: str,
    ) -> None:
        """
        Send a DM to up to <limit> non-bot, non-admin members of <server_id>.
        Must be invoked in the bot's DMs.
        """

        # ── DM-only guard ──────────────────────────────────────────────────────
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        # ── Validate limit ─────────────────────────────────────────────────────
        if limit < 1:
            await ctx.send(embed=_embed("❌ Limit must be at least **1**.", color=COL_ERR))
            return

        # ── Resolve guild ──────────────────────────────────────────────────────
        guild = self.bot.get_guild(server_id)
        if guild is None:
            await ctx.send(embed=_embed(
                f"❌ The bot is not in a server with ID `{server_id}`.\n"
                "Double-check the ID or invite the bot to that server first.",
                color=COL_ERR,
            ))
            return

        # ── Filter members ─────────────────────────────────────────────────────
        # Exclude bots and server administrators; cap at <limit>
        valid: list[discord.Member] = [
            m for m in guild.members
            if not m.bot and not m.guild_permissions.administrator
        ][:limit]

        if not valid:
            await ctx.send(embed=_embed(
                f"❌ No eligible members found in **{discord.utils.escape_markdown(guild.name)}**.\n"
                "(All members are either bots or administrators.)",
                color=COL_ERR,
            ))
            return

        # ── Start announcement ─────────────────────────────────────────────────
        await ctx.send(embed=_embed(
            f"📡 Targeting **{discord.utils.escape_markdown(guild.name)}**\n"
            f"Found **{len(valid)}** valid member(s) (bots & admins excluded).\n"
            f"Starting Mass DM…",
            color=COL,
        ))

        # ── Live tracker ───────────────────────────────────────────────────────
        status_msg = await ctx.send(embed=_embed(
            f"Progress: **0 / {len(valid)}** | ✅ Success: **0** | ❌ Failed: **0**",
            color=COL,
        ))

        success = 0
        failed  = 0

        for i, member in enumerate(valid, start=1):
            # Attempt DM
            try:
                await member.send(message)
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

            # Edit live tracker after every attempt
            try:
                await status_msg.edit(embed=_embed(
                    f"Progress: **{i} / {len(valid)}** | ✅ Success: **{success}** | ❌ Failed: **{failed}**",
                    color=COL,
                ))
            except discord.HTTPException:
                pass

            # 1 250 ms cooldown between every DM — mandatory rate-limit safety
            await asyncio.sleep(DM_SLEEP)

        # ── Completion ─────────────────────────────────────────────────────────
        final = _embed(
            f"✅ **Completed!**\n\n"
            f"**Server :** {discord.utils.escape_markdown(guild.name)}\n"
            f"**Sent    :** {success}\n"
            f"**Failed  :** {failed}",
            color=COL_OK,
        )
        try:
            await status_msg.edit(embed=final)
        except discord.HTTPException:
            await ctx.send(embed=final)

    # ── Error handler ──────────────────────────────────────────────────────────

    @massdm.error
    async def massdm_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        # Silently discard all errors that originate from guild channels
        if ctx.guild is not None:
            return

        if isinstance(error, (commands.NotOwner, commands.CheckFailure)):
            await ctx.send(embed=_embed(
                "❌ This command is restricted to the **bot owner**.",
                color=COL_ERR,
            ))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "❌ Missing argument: `" + error.param.name + "`\n"
                "**Usage:** `+massdm <server_id> <limit> <message>`",
                color=COL_ERR,
            ))
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=_embed(
                "❌ `server_id` and `limit` must be whole numbers.\n"
                "**Usage:** `+massdm <server_id> <limit> <message>`",
                color=COL_ERR,
            ))
        else:
            log.error("Unhandled massdm error: %s", error)
            await ctx.send(embed=_embed(
                f"❌ Unexpected error: `{type(error).__name__}: {error}`",
                color=COL_ERR,
            ))


# ── Setup ──────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MassDM(bot))
