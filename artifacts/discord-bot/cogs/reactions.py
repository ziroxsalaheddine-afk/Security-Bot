"""
Auto-Reaction Cog.

Adds a ✅ reaction to the invoking message whenever a user successfully
completes an add/remove/list-style management command for users, roles, or
aliases — a lightweight visual confirmation the action actually went through.

`on_command_completion` only fires once a command has run to completion
*without* raising — but a command can still "complete" after replying with
its own in-body denial (e.g. `+owner add` replying "Access Denied" and
returning). Those commands are excluded below via `NO_REACT_QUALNAMES` so a
✅ is never shown next to a failed/denied attempt.
"""

from discord.ext import commands

CONFIRM_EMOJI = "✅"

# Exact qualified command names that represent a genuine add/remove/list
# mutation on users, roles, or aliases. Deliberately explicit (rather than
# matching on subcommand name alone) so bare group invocations like
# `+alias`, `+alias self`, or `+bypass` (which just show usage text, not a
# mutation) never get a reaction.
REACT_QUALNAMES = {
    "whitelist add", "whitelist remove", "whitelist list",
    "bypass add", "bypass remove", "bypass list",
    "dj add", "dj remove", "dj list",
    "alias add", "alias remove", "alias list",
    "alias self add", "alias self remove", "alias self list",
    "coowners",
}

# Commands whose *body* can still perform an in-line access-denial reply
# ("Access Denied") and return normally rather than raising — a completion
# there is not a genuine success, so never react to these.
NO_REACT_QUALNAMES = {"owner add", "owner remove"}


class Reactions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        if ctx.command is None:
            return
        qualname = ctx.command.qualified_name

        if qualname in NO_REACT_QUALNAMES or qualname not in REACT_QUALNAMES:
            return

        try:
            await ctx.message.add_reaction(CONFIRM_EMOJI)
        except Exception:
            pass  # missing permission / message deleted — non-critical


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
