"""
Trossard — Production Discord Security Bot.

Loads all cogs, initialises the SQLite database, then connects to Discord.
DISCORD_TOKEN is read from the environment (Replit Secret).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands

from database import Database

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("trossard")

# ── Config ────────────────────────────────────────────────────────────────────
PREFIX = "+"
TOKEN = os.environ.get("DISCORD_TOKEN", "")

COGS = [
    "cogs.whitelist",
    "cogs.antinuke",
    "cogs.danger",
]


# ── Bot subclass ──────────────────────────────────────────────────────────────

class Trossard(commands.Bot):
    db: Database

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.moderation = True

        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            help_command=None,  # replaced by our custom +help cog
        )

    async def setup_hook(self) -> None:
        self.db = Database()
        await self.db.init()
        log.info("Database initialised.")

        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as exc:
                log.error("Failed to load cog %s: %s", cog, exc, exc_info=True)

    async def on_ready(self) -> None:
        if self.user:
            log.info(
                "Trossard ready — logged in as %s (ID: %d)",
                self.user, self.user.id,
            )
            log.info("Serving %d guild(s).", len(self.guilds))
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} server(s) | {PREFIX}help",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild: %s (ID: %d)", guild.name, guild.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} server(s) | {PREFIX}help",
            )
        )

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if hasattr(ctx.command, "on_error"):
            return
        if ctx.cog and commands.Cog._get_overridden_hook(ctx.cog.cog_command_error):
            return
        log.error(
            "Unhandled command error in %s: %s",
            ctx.command, error, exc_info=error,
        )

    async def close(self) -> None:
        log.info("Shutting down — closing database connection.")
        await self.db.close()
        await super().close()


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    if not TOKEN:
        log.critical(
            "DISCORD_TOKEN is not set. "
            "Add it as a Replit Secret named DISCORD_TOKEN and restart the workflow."
        )
        sys.exit(1)

    bot = Trossard()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
