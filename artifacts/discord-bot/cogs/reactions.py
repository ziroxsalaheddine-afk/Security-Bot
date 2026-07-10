"""
Auto-Reaction Cog.

Adds a ✅ reaction to the invoking message whenever a user successfully runs
a command whose name contains "add", "remove", or "list" — a lightweight
visual confirmation the action went through.

`on_command_completion` fires once a command has finished running without
raising, which is what we key off here.
"""

from discord.ext import commands

CONFIRM_EMOJI = "✅"
_KEYWORDS = ("add", "remove", "list")


class Reactions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        if ctx.command is None:
            return

        qualname = ctx.command.qualified_name.lower()
        if not any(keyword in qualname for keyword in _KEYWORDS):
            return

        try:
            await ctx.message.add_reaction(CONFIRM_EMOJI)
        except Exception:
            pass  # missing permission / message deleted — non-critical


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
