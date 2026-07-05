"""
Clone Cog — +loadrole / +clonerole and +loadchannel / +clonechannel
Whitelisted users only. Embed shows exact timing via time.perf_counter().
"""

import time
import asyncio
import logging

import discord
from discord.ext import commands

from utils import db, embeds

log = logging.getLogger("guardian.clone")


def whitelist_only():
    async def predicate(ctx: commands.Context) -> bool:
        return db.is_whitelisted(ctx.author.id)
    return commands.check(predicate)


class Clone(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +loadrole / +clonerole ────────────────────────────────────────────────

    @commands.command(name="loadrole", aliases=["clonerole"])
    @whitelist_only()
    async def load_role(self, ctx: commands.Context, *, role: discord.Role):
        t0 = time.perf_counter()

        original_members = list(role.members)

        try:
            new_role = await ctx.guild.create_role(
                name=role.name,
                color=role.color,
                hoist=role.hoist,
                mentionable=role.mentionable,
                permissions=role.permissions,
                reason=f"[Guardian] +loadrole by {ctx.author} ({ctx.author.id})",
            )
        except discord.Forbidden:
            return await ctx.send(
                embed=embeds.danger("Permission Error", "I don't have permission to create roles.")
            )

        # Copy role icon if present
        if role.icon:
            try:
                icon_bytes = await role.icon.read()
                await new_role.edit(display_icon=icon_bytes)
            except Exception:
                pass

        reassigned = 0
        failed = 0

        async def _assign(member: discord.Member):
            nonlocal reassigned, failed
            try:
                await member.add_roles(new_role, reason="[Guardian] Role clone reassignment")
                reassigned += 1
            except Exception:
                failed += 1

        batch = 10
        for i in range(0, len(original_members), batch):
            await asyncio.gather(*[_assign(m) for m in original_members[i : i + batch]])

        elapsed = time.perf_counter() - t0

        embed = embeds.stats(
            "✅  Role Cloned",
            elapsed,
            fields=[
                ("Original Role", role.mention, True),
                ("New Role", new_role.mention, True),
                ("Color", f"`#{role.color.value:06X}`", True),
                ("Permissions", f"`{role.permissions.value}`", True),
                ("Members Reassigned", f"`{reassigned}`", True),
                ("Failed Assignments", f"`{failed}`", True),
            ],
        )
        await ctx.send(embed=embed)

    # ── +loadchannel / +clonechannel ──────────────────────────────────────────

    @commands.command(name="loadchannel", aliases=["clonechannel"])
    @whitelist_only()
    async def load_channel(self, ctx: commands.Context, *, channel: discord.abc.GuildChannel):
        t0 = time.perf_counter()

        overwrites: dict = {}
        for target, ow in channel.overwrites.items():
            allow, deny = ow.pair()
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)

        ow_count = len(overwrites)
        category = channel.category
        reason = f"[Guardian] +loadchannel by {ctx.author} ({ctx.author.id})"

        try:
            if isinstance(channel, discord.TextChannel):
                new_ch = await ctx.guild.create_text_channel(
                    name=channel.name,
                    topic=channel.topic or "",
                    slowmode_delay=channel.slowmode_delay,
                    nsfw=channel.is_nsfw(),
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                )
            elif isinstance(channel, discord.VoiceChannel):
                new_ch = await ctx.guild.create_voice_channel(
                    name=channel.name,
                    bitrate=channel.bitrate,
                    user_limit=channel.user_limit,
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                )
            elif isinstance(channel, discord.CategoryChannel):
                new_ch = await ctx.guild.create_category(
                    name=channel.name,
                    overwrites=overwrites,
                    reason=reason,
                )
            elif isinstance(channel, discord.ForumChannel):
                new_ch = await ctx.guild.create_forum(
                    name=channel.name,
                    topic=getattr(channel, "topic", "") or "",
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                )
            else:
                new_ch = await ctx.guild.create_text_channel(
                    name=channel.name,
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                )
        except discord.Forbidden:
            return await ctx.send(
                embed=embeds.danger("Permission Error", "I don't have permission to create channels.")
            )

        elapsed = time.perf_counter() - t0

        embed = embeds.stats(
            "✅  Channel Cloned",
            elapsed,
            fields=[
                ("Original", f"`#{channel.name}`", True),
                ("New Channel", new_ch.mention, True),
                ("Type", f"`{channel.type.name}`", True),
                ("Permission Overwrites", f"`{ow_count}` copied", True),
                ("Category", f"`{category.name}`" if category else "`None`", True),
            ],
        )
        await ctx.send(embed=embed)

    # ── Error handling ────────────────────────────────────────────────────────

    @load_role.error
    @load_channel.error
    async def _clone_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, (commands.BadArgument, commands.RoleNotFound, commands.ChannelNotFound)):
            await ctx.send(
                embed=embeds.danger("Not Found", str(error)), delete_after=6
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=embeds.danger("Missing Argument", f"`{error.param.name}` is required."),
                delete_after=6,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Clone(bot))
