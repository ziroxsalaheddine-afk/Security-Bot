"""
Security Bot — Entry point.

Loads all cogs, initialises the SQLite database, then connects to Discord.
The DISCORD_TOKEN environment variable is read from the Replit Secret of the
same name (shared with the Guardian Bot workflow).
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
log = logging.getLogger("secbot")

# ── Constants ──────────────────────────────────────────────────────────────────
PREFIX = "+"
TOKEN = os.environ.get("DISCORD_TOKEN", "")

COGS = [
    "cogs.whitelist",
    "cogs.antinuke",
    "cogs.danger",
    "cogs.help_menu",
]


# ── Bot subclass ──────────────────────────────────────────────────────────────

class SecurityBot(commands.Bot):
    db: Database

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            # Suppress the built-in help command so our +help cog takes over.
            help_command=None,
        )

    # ── Setup hook — runs before the bot connects ─────────────────────────────

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

    # ── Events ────────────────────────────────────────────────────────────────

    async def on_ready(self) -> None:
        if self.user:
            log.info("Security Bot ready — logged in as %s (ID: %d)", self.user, self.user.id)
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
        # Silently ignore unknown commands so the bot doesn't spam errors.
        if isinstance(error, commands.CommandNotFound):
            return
        # For everything else, propagate to the cog-level error handler.
        # If there's no cog handler, log it.
        if hasattr(ctx.command, "on_error"):
            return
        if ctx.cog and commands.Cog._get_overridden_hook(ctx.cog.cog_command_error):
            return
        log.error("Unhandled command error in %s: %s", ctx.command, error, exc_info=error)

    # ── Teardown ──────────────────────────────────────────────────────────────

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

    bot = SecurityBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
