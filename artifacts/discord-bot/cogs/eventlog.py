"""
Event Log Cog — member join/leave logging.

Feeds the centralized logging system (`utils.logs`) with membership churn
events so the configured log channel captures the full moderation picture
alongside whitelist/bypass/DJ changes and warnings.
"""

import logging

import discord
from discord.ext import commands

from utils import logs

log = logging.getLogger("guardian.eventlog")


class EventLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        age_days = (discord.utils.utcnow() - member.created_at).days
        await logs.send(
            self.bot, member.guild, "📥  Member Joined",
            f"• __**User**__\n{member.mention}\n\n"
            f"• __**Account Age**__\n`{age_days}d`\n\n"
            f"• __**Member Count**__\n`{member.guild.member_count}`",
            user=member, color=logs.COL_INFO,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await logs.send(
            self.bot, member.guild, "📤  Member Left",
            f"• __**User**__\n{member.mention}\n\n"
            f"• __**Member Count**__\n`{member.guild.member_count}`",
            user=member, color=logs.COL_INFO,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(EventLog(bot))
