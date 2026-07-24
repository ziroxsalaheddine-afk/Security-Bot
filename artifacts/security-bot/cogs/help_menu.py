"""
Help Menu Cog — Interactive dropdown help with discord.ui.Select + discord.ui.View.
No emojis anywhere. Clean professional text only.

+help sends the Overview embed with a category dropdown.
Selecting a category updates the embed in-place (message edit, not new message).
"""

from __future__ import annotations

import discord
from discord.ext import commands

from utils import FOOTER

# ── Embed builders ────────────────────────────────────────────────────────────

def _overview_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Trossard",
        description=(
            "Welcome, I'm Trossard a premium security bot for admins and I have powerful tools "
            "so I hope you're happy with my service. If you need any help, just use "
            "`+support` to reach the developers."
        ),
        color=0x5865F2,
    )
    embed.set_footer(text=FOOTER)
    return embed


def _security_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Security Modules",
        description="Complete reference for all Trossard security commands.",
        color=0x5865F2,
    )

    embed.add_field(
        name="Whitelist / Bypass",
        value=(
            "`+wl` — Show whitelisted users and roles.\n"
            "`+wl @user / @role` — Add a user or role to the bypass list.\n"
            "`+wl remove @user / @role` — Remove from the bypass list.\n\n"
            "Whitelisted targets bypass all security enforcement. "
            "A member is whitelisted if their user ID or any of their roles appear in the list."
        ),
        inline=False,
    )

    embed.add_field(
        name="Danger Roles",
        value=(
            "`+danger roles` — List all protected roles.\n"
            "`+danger roles add @role` — Mark a role as protected.\n"
            "`+danger roles remove @role` — Unmark a protected role.\n\n"
            "If an unauthorized user is granted a protected or Administrator role, "
            "the role is stripped immediately and a security alert is logged."
        ),
        inline=False,
    )

    embed.add_field(
        name="Danger Tag — Mass Mention Guard",
        value=(
            "`+danger tag` — List users allowed to use @everyone / @here.\n"
            "`+danger tag add @user` — Grant mass-mention permission.\n"
            "`+danger tag remove @user` — Revoke mass-mention permission.\n\n"
            "Any @everyone or @here from an unauthorized user is deleted instantly "
            "and a 5-second warning is posted in the channel."
        ),
        inline=False,
    )

    embed.add_field(
        name="Auto-Restore",
        value=(
            "Automatic — no command required.\n\n"
            "- Role deleted by non-whitelisted user: role is recreated with identical "
            "permissions, color, hoist, and name. All original members are re-assigned "
            "automatically using the SQLite role cache.\n"
            "- Channel deleted by non-whitelisted user: channel is recreated preserving "
            "name, type, topic, category, permission overwrites, and position.\n"
            "- Role membership cache syncs every 5 minutes and updates instantly on "
            "every role change."
        ),
        inline=False,
    )

    embed.set_footer(text=FOOTER)
    return embed


# ── Dropdown select menu ──────────────────────────────────────────────────────

class HelpSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Overview",
                value="overview",
                description="General information and bot overview",
            ),
            discord.SelectOption(
                label="Security Modules",
                value="security",
                description="Whitelist, Danger Roles, Danger Tag and Auto-Restore",
            ),
        ]
        super().__init__(
            placeholder="Select a category",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        embed = _overview_embed() if choice == "overview" else _security_embed()

        # Keep the dropdown on "Overview" option visually selected by default.
        await interaction.response.edit_message(embed=embed, view=self.view)


# ── View ──────────────────────────────────────────────────────────────────────

class HelpView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(HelpSelect())

    async def on_timeout(self) -> None:
        # Disable the select menu after the view expires so it is clear it is inactive.
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        # The message reference is not stored here; Discord handles the timeout gracefully.


# ── Cog ───────────────────────────────────────────────────────────────────────

class HelpMenu(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    @commands.guild_only()
    async def help_cmd(self, ctx: commands.Context) -> None:
        """Interactive help menu with category dropdown."""
        view = HelpView()
        await ctx.send(embed=_overview_embed(), view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpMenu(bot))
