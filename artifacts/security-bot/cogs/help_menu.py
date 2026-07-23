"""
Help Menu Cog — Clean, categorised embed listing all Security Bot commands.
"""

from __future__ import annotations

import discord
from discord.ext import commands


class HelpMenu(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    @commands.guild_only()
    async def help_cmd(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="🛡️  Security Bot — Command Reference",
            description=(
                "Production-grade per-server protection system.\n"
                "All settings are **isolated per server** — changes here don't affect other servers."
            ),
            color=0x5865F2,
        )

        # ── Whitelist ─────────────────────────────────────────────────────────
        embed.add_field(
            name="🔒  Whitelist / Bypass  *(Administrator)*",
            value=(
                "`+wl` — Show all whitelisted users and roles.\n"
                "`+wl @user` / `+wl @role` — Add a user or role to the bypass list.\n"
                "`+wl remove @user` / `+wl remove @role` — Remove from bypass list.\n\n"
                "Whitelisted users/roles bypass **all** security enforcement "
                "(role restore, channel restore, danger roles, mass-mention guard)."
            ),
            inline=False,
        )

        # ── Danger Roles ──────────────────────────────────────────────────────
        embed.add_field(
            name="⚠️  Danger Roles  *(Administrator)*",
            value=(
                "`+danger roles` — List all protected roles.\n"
                "`+danger roles add @role` — Mark a role as protected.\n"
                "`+danger roles remove @role` — Unmark a protected role.\n\n"
                "If an **unauthorized** user is granted a protected (or Administrator) role, "
                "the role is stripped instantly and a warning is logged."
            ),
            inline=False,
        )

        # ── Danger Tag ────────────────────────────────────────────────────────
        embed.add_field(
            name="🏷️  Danger Tag — Mass Mention Guard  *(Administrator)*",
            value=(
                "`+danger tag` — List users allowed to use `@everyone` / `@here`.\n"
                "`+danger tag add @user` — Grant mass-mention permission.\n"
                "`+danger tag remove @user` — Revoke mass-mention permission.\n\n"
                "Any `@everyone` or `@here` by an **unauthorized** user is deleted "
                "instantly (0 ms target) and a temporary warning is posted."
            ),
            inline=False,
        )

        # ── Auto-Restore ──────────────────────────────────────────────────────
        embed.add_field(
            name="🔄  Auto-Restore  *(Automatic — no command needed)*",
            value=(
                "• **Role deleted** by non-whitelisted user → role recreated, "
                "original members re-assigned, audit log warning sent.\n"
                "• **Channel deleted** by non-whitelisted user → channel recreated "
                "with identical name, permissions, topic, and category.\n"
                "• Role membership cache syncs every **5 minutes** (SQLite backed)."
            ),
            inline=False,
        )

        # ── Footer ────────────────────────────────────────────────────────────
        embed.set_footer(
            text="Security Bot • SQLite per-guild isolation • discord.py v2"
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url if self.bot.user else discord.Embed.Empty)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpMenu(bot))
