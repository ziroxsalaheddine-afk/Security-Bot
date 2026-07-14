"""
Search User Cog
───────────────
+searchuser [length]  —  Find available Discord usernames of the given length.

• Length range: 3–15 (default 5)
• Checks availability via Discord's public username-attempt endpoint
• Max 15 API attempts per invocation; stops early once 5 are found
• Gracefully handles 429 / 403 (rate-limit / datacenter block) responses
"""

import asyncio
import logging
import random
import string
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

log = logging.getLogger("guardian.searchuser")

COL_LAVENDER = 0xC8B6FF
FOOTER = "© 2026 — developed by zrx.gg"

API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}

MAX_ATTEMPTS  = 15   # hard ceiling on API calls per command run
TARGET_FOUND  = 5    # stop early once this many available names are collected
REQUEST_DELAY = 0.35 # seconds between requests to stay polite

# Characters valid anywhere in a username
_ALL_CHARS  = string.ascii_lowercase + string.digits + "_."
# Characters safe for position 0 or position -1 (no period)
_SAFE_CHARS = string.ascii_lowercase + string.digits + "_"


def _gen_username(length: int) -> str:
    """
    Generate a single random Discord-valid username of *length* characters.

    Rules enforced:
      • Only a-z, 0-9, _, . allowed
      • First and last character cannot be a period
      • No two consecutive periods
    """
    while True:
        chars: list[str] = []
        for i in range(length):
            pool = _SAFE_CHARS if (i == 0 or i == length - 1) else _ALL_CHARS
            chars.append(random.choice(pool))
        name = "".join(chars)
        if ".." not in name:
            return name


class SearchUser(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="searchuser")
    async def searchuser(self, ctx: commands.Context, length: int = 5):
        """Find available Discord usernames of a given character length (3–15)."""

        # ── Validate length ────────────────────────────────────────────────────
        if not 3 <= length <= 15:
            err = discord.Embed(
                description="❌ Length must be between **3** and **15**.",
                color=0xC0392B,
            )
            return await ctx.send(embed=err, delete_after=8)

        # ── Placeholder while working ──────────────────────────────────────────
        placeholder = discord.Embed(
            title="Username Search Results",
            description="🔍  Searching for available usernames — please wait...",
            color=COL_LAVENDER,
        )
        msg = await ctx.send(embed=placeholder)

        # ── Check usernames ────────────────────────────────────────────────────
        available:    list[str] = []
        attempts:     int       = 0
        rate_limited: bool      = False

        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as session:
                while len(available) < TARGET_FOUND and attempts < MAX_ATTEMPTS:
                    username = _gen_username(length)
                    attempts += 1

                    try:
                        async with session.post(
                            API_URL,
                            json={"username": username},
                            timeout=aiohttp.ClientTimeout(total=6),
                        ) as resp:
                            if resp.status in (429, 403):
                                log.warning(
                                    "searchuser: API returned %d (rate-limit/block) after %d attempts",
                                    resp.status, attempts,
                                )
                                rate_limited = True
                                break

                            if resp.status == 200:
                                data = await resp.json()
                                if not data.get("taken", True):
                                    available.append(username)

                    except asyncio.TimeoutError:
                        log.debug("searchuser: timeout on %s", username)
                    except aiohttp.ClientError as exc:
                        log.warning("searchuser: request error for %s: %s", username, exc)

                    if attempts < MAX_ATTEMPTS and len(available) < TARGET_FOUND:
                        await asyncio.sleep(REQUEST_DELAY)

        except Exception as exc:
            log.error("searchuser: unexpected error: %s", exc)

        # ── Build result embed ────────────────────────────────────────────────
        result = discord.Embed(
            title="Username Search Results",
            color=COL_LAVENDER,
            timestamp=datetime.now(timezone.utc),
        )
        result.add_field(name="Length Searched", value=f"`{length}` characters", inline=True)
        result.add_field(name="Attempts Made",   value=f"`{attempts}` / `{MAX_ATTEMPTS}`", inline=True)
        result.add_field(name="\u200b",          value="\u200b", inline=True)  # spacer

        if rate_limited:
            result.add_field(
                name="⚠️  Rate Limited / Blocked",
                value=(
                    "Discord's API blocked further requests (datacenter IP limit or 403).\n"
                    "Wait a few minutes before trying again, or choose a longer length."
                ),
                inline=False,
            )

        if available:
            names_block = "\n".join(f"• `{n}`" for n in available)
            result.add_field(
                name=f"✅  Available Usernames — {len(available)} found",
                value=names_block,
                inline=False,
            )
        else:
            result.add_field(
                name="❌  No Available Usernames Found",
                value=(
                    "None were found within the attempt limit.\n"
                    "Try again or use a **larger length** — `5` or `6` is recommended."
                ),
                inline=False,
            )

        result.set_footer(
            text=(
                "Note: 3-4 character usernames are highly competitive and mostly taken. "
                "Datacenter IP rate limits may apply."
            )
        )

        try:
            await msg.edit(embed=result)
        except discord.HTTPException:
            await ctx.send(embed=result)

    @searchuser.error
    async def _searchuser_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.BadArgument):
            err = discord.Embed(
                description="❌ Length must be a whole number between **3** and **15**.",
                color=0xC0392B,
            )
            await ctx.send(embed=err, delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(SearchUser(bot))
