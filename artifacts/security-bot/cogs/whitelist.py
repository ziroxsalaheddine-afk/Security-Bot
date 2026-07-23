"""
Whitelist Cog — Dual user+role whitelist with per-guild isolation.

Commands:
  +wl                        — list all whitelisted users and roles
  +wl @user/@role            — add a user or role to the whitelist
  +wl remove @user/@role     — remove a user or role from the whitelist
"""

from __future__ import annotations

import logging
from typing import Union

import discord
from discord.ext import commands

from database import Database
from utils import error_embed, info_embed, is_whitelisted, success_embed, warn_embed

log = logging.getLogger("secbot.whitelist")


def _require_admin(ctx: commands.Context) -> bool:
    return (
        ctx.author.id == ctx.guild.owner_id
        or ctx.author.guild_permissions.administrator
    )


class Whitelist(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    # ── +wl ──────────────────────────────────────────────────────────────────

    @commands.group(name="wl", invoke_without_command=True)
    @commands.guild_only()
    async def whitelist(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.Role] = None,
    ) -> None:
        """
        +wl                 → show current whitelist
        +wl @user/@role     → add to whitelist
        """
        if not _require_admin(ctx):
            return await ctx.send(
                embed=error_embed("Permission Denied", "Administrator permission required."),
                delete_after=8,
            )

        if target is None:
            return await self._show_list(ctx)

        # Add target to whitelist.
        kind = "user" if isinstance(target, discord.Member) else "role"
        await self.db.wl_add(ctx.guild.id, target.id, kind)

        label = target.mention
        log.info("Whitelist add: guild=%d %s=%d by %s", ctx.guild.id, kind, target.id, ctx.author)
        await ctx.send(
            embed=success_embed(
                "Whitelist Updated",
                f"{label} (`{target.id}`) has been added to the **{kind}** whitelist.",
            )
        )

    # ── +wl remove ───────────────────────────────────────────────────────────

    @whitelist.command(name="remove", aliases=["rm", "del", "delete"])
    @commands.guild_only()
    async def whitelist_remove(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.Role],
    ) -> None:
        """Remove a user or role from the whitelist."""
        if not _require_admin(ctx):
            return await ctx.send(
                embed=error_embed("Permission Denied", "Administrator permission required."),
                delete_after=8,
            )

        kind = "user" if isinstance(target, discord.Member) else "role"
        removed = await self.db.wl_remove(ctx.guild.id, target.id, kind)

        if not removed:
            return await ctx.send(
                embed=warn_embed(
                    "Not Found",
                    f"{target.mention} was not in the {kind} whitelist.",
                ),
                delete_after=8,
            )

        log.info("Whitelist remove: guild=%d %s=%d by %s", ctx.guild.id, kind, target.id, ctx.author)
        await ctx.send(
            embed=success_embed(
                "Whitelist Updated",
                f"{target.mention} has been **removed** from the {kind} whitelist.",
            )
        )

    # ── Display helper ────────────────────────────────────────────────────────

    async def _show_list(self, ctx: commands.Context) -> None:
        rows = await self.db.wl_list(ctx.guild.id)

        users = [r for r in rows if r["target_type"] == "user"]
        roles = [r for r in rows if r["target_type"] == "role"]

        embed = discord.Embed(
            title="🛡️  Server Whitelist",
            color=0x5865F2,
        )
        embed.set_footer(text=f"Security Bot • {ctx.guild.name}")

        if users:
            lines = []
            for row in users:
                member = ctx.guild.get_member(row["target_id"])
                label = member.mention if member else f"`{row['target_id']}`"
                lines.append(f"• {label}")
            embed.add_field(name="👤 Whitelisted Users", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="👤 Whitelisted Users", value="*None configured.*", inline=False)

        if roles:
            lines = []
            for row in roles:
                role = ctx.guild.get_role(row["target_id"])
                label = role.mention if role else f"`{row['target_id']}`"
                lines.append(f"• {label}")
            embed.add_field(name="🏷️ Whitelisted Roles", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="🏷️ Whitelisted Roles", value="*None configured.*", inline=False)

        embed.description = (
            f"**{len(users)}** user(s) and **{len(roles)}** role(s) whitelisted.\n"
            "Whitelisted targets bypass all security enforcement."
        )
        await ctx.send(embed=embed)

    # ── Error handlers ────────────────────────────────────────────────────────

    @whitelist.error
    async def _wl_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, (commands.MemberNotFound, commands.RoleNotFound, commands.BadUnionArgument)):
            await ctx.send(
                embed=error_embed(
                    "Invalid Target",
                    "Could not find that user or role. Mention them or use their ID.\n"
                    "**Usage:** `+wl @user` or `+wl @role`",
                ),
                delete_after=10,
            )

    @whitelist_remove.error
    async def _wl_remove_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, (commands.MemberNotFound, commands.RoleNotFound, commands.BadUnionArgument)):
            await ctx.send(
                embed=error_embed(
                    "Invalid Target",
                    "Could not find that user or role.\n"
                    "**Usage:** `+wl remove @user` or `+wl remove @role`",
                ),
                delete_after=10,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Whitelist(bot))
