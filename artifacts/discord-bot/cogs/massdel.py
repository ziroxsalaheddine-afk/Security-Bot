"""
MassDel Cog — Mass-deletion utility commands
══════════════════════════════════════════════

+deleteroles    — Delete all non-managed, below-bot roles in this server
+deletechannels — Delete all channels and categories (preserves invocation channel)
+deleteemojis   — Delete all custom emojis in this server

Permissions : requires Discord administrator permission
Rate limits : sequential loop with asyncio.sleep(0.3) between every delete
              to stay well within Discord's per-guild rate limits and avoid
              API abuse / temporary bans
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

log = logging.getLogger("guardian.massdel")

COL      = 0x2B2D31
COL_ERR  = 0xC0392B
COL_WARN = 0xE67E22
COL_OK   = 0x27AE60
FOOTER   = "© 2026 — developed by zrx.gg"

DEL_SLEEP = 0.3   # seconds between each deletion — safe for Discord rate limits


# ── Helpers ────────────────────────────────────────────────────────────────────

def _embed(description: str, *, color: int = COL) -> discord.Embed:
    e = discord.Embed(description=description, color=color,
                      timestamp=datetime.now(timezone.utc))
    e.set_footer(text=FOOTER)
    return e


# ── Confirmation View ──────────────────────────────────────────────────────────

class _ConfirmView(discord.ui.View):
    """
    Simple two-button (Confirm / Cancel) prompt.
    Sets ``confirmed`` to True/False on interaction, then stops.
    """

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed: Optional[bool] = None
        self.message:   Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.confirmed = False
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(
                    embed=_embed("• __**Timed Out**__\nNo action was taken.",
                                 color=COL_WARN),
                    view=self,
                )
            except Exception:
                pass

    @discord.ui.button(label="Confirm — Delete All", style=discord.ButtonStyle.danger,
                       emoji="🗑️", row=0)
    async def confirm(self, interaction: discord.Interaction,
                      button: discord.ui.Button) -> None:
        self.confirmed = True
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            embed=_embed("• __**Confirmed**__\nExecuting — please wait…",
                         color=COL_WARN),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary,
                       emoji="❌", row=0)
    async def cancel(self, interaction: discord.Interaction,
                     button: discord.ui.Button) -> None:
        self.confirmed = False
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            embed=_embed("• __**Cancelled**__\nNo changes were made.",
                         color=COL_WARN),
            view=self,
        )
        self.stop()


# ── Cog ────────────────────────────────────────────────────────────────────────

class MassDel(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── +deleteroles ───────────────────────────────────────────────────────────

    @commands.command(name="deleteroles")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def deleteroles(self, ctx: commands.Context) -> None:
        """Delete all deletable roles in this server (skips @everyone, managed, and above-bot roles)."""

        # ── Determine which roles are safe to delete ──
        bot_top = ctx.guild.me.top_role.position if ctx.guild.me else 0
        deletable = [
            r for r in ctx.guild.roles
            if not r.is_default()        # skip @everyone
            and not r.managed            # skip Nitro boosts / bot-integration roles
            and r.position < bot_top     # skip roles at or above the bot's own role
        ]

        if not deletable:
            await ctx.send(embed=_embed(
                "• __**No Roles to Delete**__\n"
                "There are no deletable roles in this server.",
                color=COL_WARN,
            ))
            return

        # ── Confirmation prompt ──
        view = _ConfirmView(ctx.author.id)
        view.message = await ctx.send(
            embed=_embed(
                f"• __**Delete All Roles — Confirmation**__\n\n"
                f"This will permanently delete **{len(deletable)} role(s)**.\n"
                f"Skipped: @everyone · managed roles · roles above the bot.\n\n"
                f"⚠️ **This action cannot be undone.** Click **Confirm** to proceed.",
                color=COL_WARN,
            ),
            view=view,
        )

        await view.wait()
        if not view.confirmed:
            return   # timed out or cancelled — view already updated the message

        # ── Execute sequential deletion ──
        progress = view.message
        deleted = 0
        failed  = 0

        for role in sorted(deletable, key=lambda r: r.position, reverse=True):
            try:
                await role.delete(reason=f"[Guardian +deleteroles] by {ctx.author}")
                deleted += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                log.warning("Could not delete role '%s': %s", role.name, exc)
                failed += 1
            await asyncio.sleep(DEL_SLEEP)

        await progress.edit(
            embed=_embed(
                f"• __**Roles Deleted**__\n\n"
                f"`✅` Successfully deleted: **{deleted}** role(s)\n"
                + (f"`⚠️` Could not delete: **{failed}** role(s)\n" if failed else "")
                + "\n*@everyone, managed, and above-bot roles were preserved.*",
                color=COL_OK if not failed else COL_WARN,
            ),
            view=None,
        )

    # ── +deletechannels ────────────────────────────────────────────────────────

    @commands.command(name="deletechannels")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def deletechannels(self, ctx: commands.Context) -> None:
        """Delete all channels and categories in this server (preserves the command channel)."""

        invoke_id = ctx.channel.id

        # Non-categories first, then categories; always skip the invocation channel
        non_cats = [
            c for c in ctx.guild.channels
            if not isinstance(c, discord.CategoryChannel)
            and c.id != invoke_id
        ]
        cats = list(ctx.guild.categories)

        total = len(non_cats) + len(cats)
        if total == 0:
            await ctx.send(embed=_embed(
                "• __**No Channels to Delete**__\n"
                "There are no channels to delete (the current channel is always preserved).",
                color=COL_WARN,
            ))
            return

        # ── Confirmation prompt ──
        view = _ConfirmView(ctx.author.id)
        view.message = await ctx.send(
            embed=_embed(
                f"• __**Delete All Channels — Confirmation**__\n\n"
                f"This will permanently delete **{total} channel(s)/categor{'ies' if len(cats) != 1 else 'y'}** "
                f"(`{len(non_cats)}` channels · `{len(cats)}` categories).\n"
                f"⚠️ **This channel is preserved** so the bot can report completion.\n\n"
                f"⚠️ **This action cannot be undone.** Click **Confirm** to proceed.",
                color=COL_WARN,
            ),
            view=view,
        )

        await view.wait()
        if not view.confirmed:
            return

        # ── Execute sequential deletion — non-cats first, then cats ──
        progress = view.message
        deleted = 0
        failed  = 0

        for ch in non_cats:
            try:
                await ch.delete(reason=f"[Guardian +deletechannels] by {ctx.author}")
                deleted += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                log.warning("Could not delete channel '%s': %s", ch.name, exc)
                failed += 1
            await asyncio.sleep(DEL_SLEEP)

        for cat in cats:
            try:
                await cat.delete(reason=f"[Guardian +deletechannels] by {ctx.author}")
                deleted += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                log.warning("Could not delete category '%s': %s", cat.name, exc)
                failed += 1
            await asyncio.sleep(DEL_SLEEP)

        await progress.edit(
            embed=_embed(
                f"• __**Channels Deleted**__\n\n"
                f"`✅` Successfully deleted: **{deleted}** channel(s)/categor{'ies' if deleted != 1 else 'y'}\n"
                + (f"`⚠️` Could not delete: **{failed}**\n" if failed else "")
                + f"\n⚠️ *This channel was preserved — delete it manually when done.*",
                color=COL_OK if not failed else COL_WARN,
            ),
            view=None,
        )

    # ── +deleteemojis ──────────────────────────────────────────────────────────

    @commands.command(name="deleteemojis")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def deleteemojis(self, ctx: commands.Context) -> None:
        """Delete all custom emojis in this server."""

        emojis = list(ctx.guild.emojis)
        if not emojis:
            await ctx.send(embed=_embed(
                "• __**No Emojis to Delete**__\n"
                "This server has no custom emojis.",
                color=COL_WARN,
            ))
            return

        # ── Confirmation prompt ──
        view = _ConfirmView(ctx.author.id)
        view.message = await ctx.send(
            embed=_embed(
                f"• __**Delete All Emojis — Confirmation**__\n\n"
                f"This will permanently delete all **{len(emojis)} custom emoji(s)** "
                f"from this server.\n\n"
                f"⚠️ **This action cannot be undone.** Click **Confirm** to proceed.",
                color=COL_WARN,
            ),
            view=view,
        )

        await view.wait()
        if not view.confirmed:
            return

        # ── Execute sequential deletion ──
        progress = view.message
        deleted = 0
        failed  = 0

        for emoji in emojis:
            try:
                await emoji.delete(reason=f"[Guardian +deleteemojis] by {ctx.author}")
                deleted += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                log.warning("Could not delete emoji '%s': %s", emoji.name, exc)
                failed += 1
            await asyncio.sleep(DEL_SLEEP)

        await progress.edit(
            embed=_embed(
                f"• __**Emojis Deleted**__\n\n"
                f"`✅` Successfully deleted: **{deleted}** emoji(s)\n"
                + (f"`⚠️` Could not delete: **{failed}** emoji(s)\n" if failed else ""),
                color=COL_OK if not failed else COL_WARN,
            ),
            view=None,
        )

    # ── Error handlers ─────────────────────────────────────────────────────────

    @deleteroles.error
    async def _deleteroles_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=_embed(
                "• __**Permission Denied**__\n"
                "You need the **Administrator** permission to use this command.",
                color=COL_ERR,
            ), delete_after=10)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                f"• __**Cooldown**__\nTry again in `{error.retry_after:.0f}s`.",
                color=COL_WARN,
            ), delete_after=8)

    @deletechannels.error
    async def _deletechannels_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=_embed(
                "• __**Permission Denied**__\n"
                "You need the **Administrator** permission to use this command.",
                color=COL_ERR,
            ), delete_after=10)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                f"• __**Cooldown**__\nTry again in `{error.retry_after:.0f}s`.",
                color=COL_WARN,
            ), delete_after=8)

    @deleteemojis.error
    async def _deleteemojis_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=_embed(
                "• __**Permission Denied**__\n"
                "You need the **Administrator** permission to use this command.",
                color=COL_ERR,
            ), delete_after=10)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                f"• __**Cooldown**__\nTry again in `{error.retry_after:.0f}s`.",
                color=COL_WARN,
            ), delete_after=8)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MassDel(bot))
