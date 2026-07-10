"""
Auto-Reaction Cog.

Adds a ✅ reaction to the invoking message whenever a user successfully runs
a command whose name contains "add", "remove", or "list" — a lightweight
visual confirmation the action went through.

`on_command_completion` fires once a command has finished running without
raising, which is what we key off here.

`+autoreact on/off` toggles this behavior bot-wide (persisted in
guardian.db.json), and `+autoreact` with no argument reports current status.
"""

from discord.ext import commands

from utils import db

CONFIRM_EMOJI = "✅"
_KEYWORDS = ("add", "remove", "list")


def _is_enabled() -> bool:
    return db.get_config().get("autoreact", True)


class Reactions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        if not _is_enabled():
            return
        if ctx.command is None:
            return

        qualname = ctx.command.qualified_name.lower()
        if not any(keyword in qualname for keyword in _KEYWORDS):
            return

        try:
            await ctx.message.add_reaction(CONFIRM_EMOJI)
        except Exception:
            pass  # missing permission / message deleted — non-critical

    @commands.command(name="autoreact")
    async def autoreact(self, ctx: commands.Context, state: str = None):
        if not db.is_whitelisted(ctx.author.id):
            return

        if state is None:
            status = "`ON`" if _is_enabled() else "`OFF`"
            return await ctx.send(
                f"• __**Auto-React Status**__\n"
                f"Currently {status}. Use `+autoreact on` or `+autoreact off` to change it."
            )

        state = state.lower()
        if state not in ("on", "off"):
            return await ctx.send(
                "• __**Usage**__\n`+autoreact on` / `+autoreact off`",
                delete_after=5,
            )

        enabled = state == "on"
        db.set_config(["autoreact"], enabled)
        await ctx.send(
            f"• __**Auto-React**__\n"
            f"✅ reactions on add/remove/list commands are now "
            f"{'`ENABLED`' if enabled else '`DISABLED`'}."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
