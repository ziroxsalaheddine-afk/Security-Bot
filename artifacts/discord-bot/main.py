import os
import asyncio
import logging
import discord
from discord.ext import commands
from utils import db
from keep_alive import keep_alive

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("guardian")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.moderation = True
intents.guilds = True

COGS = [
    "cogs.antinuke",
    "cogs.clone",
    "cogs.antiraid",
    "cogs.automod",
    "cogs.admin",
    "cogs.help",
    "cogs.setup",
    "cogs.voice",
    "cogs.owner",
    "cogs.backup",
    "cogs.warden",
]


class Guardian(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="+",
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self):
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as e:
                log.error("Failed to load cog %s: %s", cog, e)

    async def on_ready(self):
        print()
        print("  ╔══════════════════════════════════════╗")
        print("  ║     G U A R D I A N   B O T   v2    ║")
        print("  ║     Python Security System           ║")
        print("  ╚══════════════════════════════════════╝")
        print()
        print(f"  [ONLINE] {self.user} (ID: {self.user.id})")
        print(f"  [GUILDS] Serving {len(self.guilds)} guild(s)")
        print()

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=discord.Embed(
                    description=f"❌ Missing argument: `{error.param.name}`",
                    color=0xC0392B,
                ),
                delete_after=5,
            )
        else:
            log.error("Command error in %s: %s", ctx.command, error)


bot = Guardian()

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN is not set. Exiting.")
        raise SystemExit(1)
    db.get()
    keep_alive()
    bot.run(token, log_handler=None)
