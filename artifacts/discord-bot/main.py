import os
import asyncio
import logging
import discord
import wavelink
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
intents.members        = True
intents.message_content = True
intents.moderation     = True
intents.guilds         = True
intents.voice_states   = True      # Required for all voice/music operations

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
    "cogs.music",
    "cogs.dj",
]


def _get_prefix(bot: "Guardian", message: discord.Message) -> str:
    return db.get_prefix()


class Guardian(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=_get_prefix,
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

        # ── Connect Lavalink v4 node (wavelink 3.x) ───────────────────────────
        lava_uri  = os.environ.get("LAVALINK_URI")
        lava_pass = os.environ.get("LAVALINK_PASSWORD")
        if lava_uri and lava_pass:
            try:
                nodes = [wavelink.Node(
                    identifier="MAIN",
                    uri=lava_uri,
                    password=lava_pass,
                )]
                await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)
                log.info("Lavalink node connecting to %s…", lava_uri)
            except Exception as exc:
                log.error("Failed to initialise Lavalink node: %s", exc)
        else:
            log.warning(
                "LAVALINK_URI or LAVALINK_PASSWORD not set — music commands disabled."
            )

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
