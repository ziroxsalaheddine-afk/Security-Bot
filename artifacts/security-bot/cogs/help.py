"""
Help Cog — Single dropdown help menu for Trossard.
No emojis in any user-facing text or menu options.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from utils import FOOTER

# ── Embed builders ────────────────────────────────────────────────────────────

def _base_embed(title: str) -> discord.Embed:
    e = discord.Embed(title=title, color=0x5865F2)
    e.set_footer(text=FOOTER)
    return e


def _security_embed() -> discord.Embed:
    e = _base_embed("Security Modules")
    e.description = (
        "All active protection systems and their commands. "
        "Administrator permission is required for every command below."
    )

    e.add_field(
        name="Whitelist System",
        value=(
            "`+whitelist [@user / @role]` — Whitelist a user or role for full bypass.\n"
            "`+whitelist remove [@user / @role]` — Remove from whitelist.\n"
            "`+whitelist` — View current whitelisted users and roles."
        ),
        inline=False,
    )
    e.add_field(
        name="Danger Roles",
        value=(
            "`+danger roles` — View protected roles.\n"
            "`+danger roles add @role [@role ...]` — Protect a role or administrator permission.\n"
            "`+danger roles remove @role [@role ...]` — Unmark protected role."
        ),
        inline=False,
    )
    e.add_field(
        name="Danger Tag",
        value=(
            "`+danger tag` — View users allowed to tag @everyone / @here.\n"
            "`+danger tag add @user` — Grant tag permission.\n"
            "`+danger tag remove @user` — Revoke tag permission."
        ),
        inline=False,
    )
    e.add_field(
        name="Auto-Restore",
        value=(
            "Automatic background restoration for deleted channels and roles "
            "+ member reassignment."
        ),
        inline=False,
    )
    return e


def _music_embed() -> discord.Embed:
    e = _base_embed("Music")
    e.description = (
        "Music playback is handled by the Guardian bot. "
        "The following access controls apply to music features."
    )
    e.add_field(
        name="DJ Whitelist",
        value=(
            "Only users with the DJ role or explicit DJ whitelist entry may use music commands.\n"
            "Server administrators and whitelisted members are always exempt.\n\n"
            "DJ whitelist is managed through the Guardian bot (`+dj` commands)."
        ),
        inline=False,
    )
    return e


# ── Select menu ───────────────────────────────────────────────────────────────

class HelpSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Security Modules",
                value="security",
                description="Whitelist, Danger Roles, Danger Tag, Auto-Restore",
            ),
            discord.SelectOption(
                label="Music",
                value="music",
                description="DJ Whitelist and music access controls",
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
        if choice == "security":
            embed = _security_embed()
        else:
            embed = _music_embed()
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(HelpSelect())

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]


# ── Cog ───────────────────────────────────────────────────────────────────────

class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    @commands.guild_only()
    async def help_cmd(self, ctx: commands.Context) -> None:
        embed = _base_embed("Trossard")
        embed.description = (
            "Select a category from the dropdown below to view available commands."
        )
        await ctx.send(embed=embed, view=HelpView())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
