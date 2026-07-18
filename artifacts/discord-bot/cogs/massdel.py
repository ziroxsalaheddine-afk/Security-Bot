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

class Recovery(commands.Cog):
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
        bot_role = ctx.guild.me.top_role if ctx.guild.me else None

        print(f"[Debug] +deleteroles invoked in '{ctx.guild.name}' by {ctx.author}")
        print(f"[Debug] Bot top role: '{bot_role.name if bot_role else 'None'}' at position {bot_top}")
        print(f"[Debug] Total roles in guild: {len(ctx.guild.roles)}")

        deletable = []
        for r in ctx.guild.roles:
            if r.is_default():
                print(f"[Debug] Skipped '{r.name}': is @everyone")
                continue
            if r.managed:
                print(f"[Debug] Skipped '{r.name}': is managed (bot/integration)")
                continue
            if r.is_premium_subscriber():
                print(f"[Debug] Skipped '{r.name}': is premium subscriber (Nitro booster) role")
                continue
            if r.position >= bot_top:
                print(f"[Debug] Skipped '{r.name}': bot's top role ({bot_top}) is lower than or equal to role position ({r.position})")
                continue
            print(f"[Debug] Queued  '{r.name}': position {r.position} — will be deleted")
            deletable.append(r)

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

    # ── +massrole ──────────────────────────────────────────────────────────────

    @commands.command(name="massrole")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 60, commands.BucketType.guild)
    async def massrole(self, ctx: commands.Context, role: discord.Role) -> None:
        """Assign a role to every member currently in the server."""

        members = ctx.guild.members
        if not members:
            await ctx.send(embed=_embed(
                "• __**No Members Found**__\n"
                "The member list is empty — ensure the bot has the `members` intent.",
                color=COL_WARN,
            ))
            return

        # Guard: role must be below the bot's top role
        bot_top = ctx.guild.me.top_role.position if ctx.guild.me else 0
        if role.position >= bot_top:
            await ctx.send(embed=_embed(
                f"• __**Hierarchy Error**__\n"
                f"**{role.name}** is at or above the bot's top role and cannot be assigned.",
                color=COL_ERR,
            ))
            return

        progress = await ctx.send(embed=_embed(
            f"• __**Mass Role Assignment — Starting**__\n\n"
            f"Assigning **{role.name}** to `{len(members)}` member(s)…\n"
            f"*This may take a while — please wait.*",
            color=COL_WARN,
        ))

        assigned = 0
        skipped  = 0
        already  = 0

        for member in members:
            if role in member.roles:
                already += 1
                continue
            try:
                await member.add_roles(role, reason=f"[Guardian +massrole] by {ctx.author}")
                assigned += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not assign role '%s' to '%s': %s", role.name, member, exc)
                skipped += 1
            await asyncio.sleep(0.2)

        await progress.edit(embed=_embed(
            f"• __**Mass Role Assignment — Complete**__\n\n"
            f"`✅` Assigned **{role.name}** to: **{assigned}** member(s)\n"
            + (f"`⏭️` Already had role: **{already}** member(s)\n" if already else "")
            + (f"`⚠️` Could not assign: **{skipped}** member(s)\n" if skipped else ""),
            color=COL_OK if not skipped else COL_WARN,
        ))

    # ── +massroleusers ─────────────────────────────────────────────────────────

    @commands.command(name="massroleusers")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def massroleusers(self, ctx: commands.Context,
                            role: discord.Role,
                            members: commands.Greedy[discord.Member]) -> None:
        """Assign a role to a specific list of members (mentions or IDs)."""

        if not members:
            await ctx.send(embed=_embed(
                "• __**No Members Provided**__\n"
                "Usage: `+massroleusers <@role> <@user1> <@user2> …`",
                color=COL_WARN,
            ))
            return

        # Guard: role must be below the bot's top role
        bot_top = ctx.guild.me.top_role.position if ctx.guild.me else 0
        if role.position >= bot_top:
            await ctx.send(embed=_embed(
                f"• __**Hierarchy Error**__\n"
                f"**{role.name}** is at or above the bot's top role and cannot be assigned.",
                color=COL_ERR,
            ))
            return

        assigned = 0
        skipped  = 0
        already  = 0

        for member in members:
            if role in member.roles:
                already += 1
                continue
            try:
                await member.add_roles(role, reason=f"[Guardian +massroleusers] by {ctx.author}")
                assigned += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not assign role '%s' to '%s': %s", role.name, member, exc)
                skipped += 1
            await asyncio.sleep(0.2)

        await ctx.send(embed=_embed(
            f"• __**Role Assignment — Complete**__\n\n"
            f"Role: **{role.name}** · Target members: **{len(members)}**\n\n"
            f"`✅` Assigned: **{assigned}** member(s)\n"
            + (f"`⏭️` Already had role: **{already}** member(s)\n" if already else "")
            + (f"`⚠️` Could not assign: **{skipped}** member(s)\n" if skipped else ""),
            color=COL_OK if not skipped else COL_WARN,
        ))

    # ── +massreactchannel ──────────────────────────────────────────────────────

    @commands.command(name="massreactchannel")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def massreactchannel(self, ctx: commands.Context,
                               limit: int, *emojis: str) -> None:
        """Add one or more emoji reactions to the last <limit> messages in this channel."""

        if not emojis:
            await ctx.send(embed=_embed(
                "• __**Missing Emojis**__\n"
                "Usage: `+massreactchannel <limit> <emoji1> [emoji2 …]`",
                color=COL_WARN,
            ))
            return

        limit = max(1, min(limit, 100))   # cap at 100 to stay reasonable

        progress = await ctx.send(embed=_embed(
            f"• __**Mass React — Starting**__\n\n"
            f"Fetching last `{limit}` message(s) and adding "
            f"`{len(emojis)}` reaction(s) each…\n"
            f"*Please wait — reactions are rate-limited by Discord.*",
            color=COL_WARN,
        ))

        messages = [m async for m in ctx.channel.history(limit=limit, before=progress)]

        reacted  = 0
        failed   = 0

        for message in messages:
            for emoji in emojis:
                try:
                    await message.add_reaction(emoji)
                    reacted += 1
                except (discord.HTTPException, discord.Forbidden) as exc:
                    log.warning("Could not react with '%s' on msg %s: %s",
                                emoji, message.id, exc)
                    failed += 1
                await asyncio.sleep(0.3)   # Discord reaction rate-limit buffer

        await progress.edit(embed=_embed(
            f"• __**Mass React — Complete**__\n\n"
            f"Messages processed: **{len(messages)}** · "
            f"Emojis per message: **{len(emojis)}**\n\n"
            f"`✅` Reactions added: **{reacted}**\n"
            + (f"`⚠️` Failed (unknown emoji / deleted message): **{failed}**\n" if failed else ""),
            color=COL_OK if not failed else COL_WARN,
        ))

    # ── +massreactuser ─────────────────────────────────────────────────────────

    @commands.command(name="massreactuser")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def massreactuser(self, ctx: commands.Context,
                            target: discord.Member,
                            limit: int, *emojis: str) -> None:
        """Scan channel history up to <limit> and react to every message from <user>."""

        if not emojis:
            await ctx.send(embed=_embed(
                "• __**Missing Emojis**__\n"
                "Usage: `+massreactuser <@user> <limit> <emoji1> [emoji2 …]`",
                color=COL_WARN,
            ))
            return

        limit = max(1, min(limit, 500))   # allow deeper scans since we filter by author

        progress = await ctx.send(embed=_embed(
            f"• __**Mass React (by User) — Starting**__\n\n"
            f"Scanning last `{limit}` message(s) for messages by "
            f"**{target.display_name}** and adding `{len(emojis)}` reaction(s)…\n"
            f"*Please wait — reactions are rate-limited by Discord.*",
            color=COL_WARN,
        ))

        # Collect only messages sent by the target user
        target_messages = [
            m async for m in ctx.channel.history(limit=limit, before=progress)
            if m.author.id == target.id
        ]

        if not target_messages:
            await progress.edit(embed=_embed(
                f"• __**No Messages Found**__\n\n"
                f"No messages from **{target.display_name}** in the last `{limit}` message(s).",
                color=COL_WARN,
            ))
            return

        reacted = 0
        failed  = 0

        for message in target_messages:
            for emoji in emojis:
                try:
                    await message.add_reaction(emoji)
                    reacted += 1
                except (discord.HTTPException, discord.Forbidden) as exc:
                    log.warning("Could not react with '%s' on msg %s: %s",
                                emoji, message.id, exc)
                    failed += 1
                await asyncio.sleep(0.3)   # Discord reaction rate-limit buffer

        await progress.edit(embed=_embed(
            f"• __**Mass React (by User) — Complete**__\n\n"
            f"User: **{target.display_name}** · "
            f"Messages found: **{len(target_messages)}** · "
            f"Emojis per message: **{len(emojis)}**\n\n"
            f"`✅` Reactions added: **{reacted}**\n"
            + (f"`⚠️` Failed (unknown emoji / deleted message): **{failed}**\n" if failed else ""),
            color=COL_OK if not failed else COL_WARN,
        ))

    # ── Error handlers ─────────────────────────────────────────────────────────

    @massreactchannel.error
    async def _massreactchannel_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=_embed(
                "• __**Permission Denied**__\n"
                "You need **Manage Messages** or **Administrator** to use this command.",
                color=COL_ERR,
            ), delete_after=10)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                f"• __**Cooldown**__\nTry again in `{error.retry_after:.0f}s`.",
                color=COL_WARN,
            ), delete_after=8)
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=_embed(
                "• __**Bad Argument**__\n"
                "Usage: `+massreactchannel <limit> <emoji1> [emoji2 …]`\n"
                "`limit` must be a whole number.",
                color=COL_ERR,
            ), delete_after=10)

    @massreactuser.error
    async def _massreactuser_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=_embed(
                "• __**Permission Denied**__\n"
                "You need **Manage Messages** or **Administrator** to use this command.",
                color=COL_ERR,
            ), delete_after=10)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_embed(
                f"• __**Cooldown**__\nTry again in `{error.retry_after:.0f}s`.",
                color=COL_WARN,
            ), delete_after=8)
        elif isinstance(error, commands.BadArgument) or isinstance(error, commands.MemberNotFound):
            await ctx.send(embed=_embed(
                "• __**Bad Argument**__\n"
                "Usage: `+massreactuser <@user> <limit> <emoji1> [emoji2 …]`\n"
                "Ensure the user is a member of this server.",
                color=COL_ERR,
            ), delete_after=10)

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

    @massrole.error
    async def _massrole_error(self, ctx: commands.Context, error) -> None:
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
        elif isinstance(error, commands.BadArgument) or isinstance(error, commands.RoleNotFound):
            await ctx.send(embed=_embed(
                "• __**Role Not Found**__\n"
                "Could not find that role. Usage: `+massrole <@role>`",
                color=COL_ERR,
            ), delete_after=10)

    @massroleusers.error
    async def _massroleusers_error(self, ctx: commands.Context, error) -> None:
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
        elif isinstance(error, commands.BadArgument) or isinstance(error, commands.RoleNotFound):
            await ctx.send(embed=_embed(
                "• __**Invalid Arguments**__\n"
                "Usage: `+massroleusers <@role> <@user1> <@user2> …`",
                color=COL_ERR,
            ), delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Recovery(bot))
