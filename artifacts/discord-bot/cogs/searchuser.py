"""
Search User Cog
───────────────
+searchuser [length]  —  Find available Discord usernames of the given length.

• Length range: 3–15 (default 5)
• Routes requests through a proxy pool to bypass Replit datacenter blocks
• Proxy priority: PROXY_URL env-var → proxyscrape free list → direct (last resort)
• Up to 3 proxy retries per username attempt before skipping that name
• "Rate limited" flag only fires on genuine Discord 429 through a working proxy
"""

import asyncio
import logging
import os
import random
import string
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

log = logging.getLogger("guardian.searchuser")

COL_LAVENDER = 0xC8B6FF
FOOTER = "© 2026 — developed by zrx.gg"

DISCORD_API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v2/"
    "?request=displayproxies&protocol=http&timeout=2000"
    "&country=all&ssl=yes&anonymity=anonymous"
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
}

MAX_ATTEMPTS      = 15   # hard ceiling on username checks per command run
TARGET_FOUND      = 5    # stop early once this many available names are found
PROXY_RETRIES     = 3    # how many different proxies to try per username before giving up
PROXY_TIMEOUT     = 8    # seconds to wait for a single proxied request
FETCH_TIMEOUT     = 10   # seconds to wait when fetching the proxy list
INTER_REQUEST_GAP = 0.2  # seconds between successful requests

# Characters valid anywhere in a Discord username
_ALL_CHARS  = string.ascii_lowercase + string.digits + "_."
# Safe for position 0 or -1 (period not allowed at edges)
_SAFE_CHARS = string.ascii_lowercase + string.digits + "_"


# ── Username generator ─────────────────────────────────────────────────────────

def _gen_username(length: int) -> str:
    """Generate a random Discord-valid username of *length* characters.

    Rules enforced:
      • a-z, 0-9, _, . only
      • First and last character cannot be a period
      • No two consecutive periods (..)
    """
    while True:
        chars: list[str] = []
        for i in range(length):
            pool = _SAFE_CHARS if (i == 0 or i == length - 1) else _ALL_CHARS
            chars.append(random.choice(pool))
        name = "".join(chars)
        if ".." not in name:
            return name


# ── Proxy pool ─────────────────────────────────────────────────────────────────

async def _fetch_proxy_list(session: aiohttp.ClientSession) -> list[str]:
    """
    Fetch a fresh list of HTTP proxies from proxyscrape.
    Returns a shuffled list of 'http://ip:port' strings.
    Falls back to an empty list if the fetch fails — callers handle that.
    """
    try:
        async with session.get(
            PROXYSCRAPE_URL,
            timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                log.warning("proxyscrape returned HTTP %d", resp.status)
                return []
            text = await resp.text()
            proxies = []
            for line in text.splitlines():
                line = line.strip()
                if line and ":" in line:
                    proxies.append(f"http://{line}")
            random.shuffle(proxies)
            log.info("searchuser: fetched %d proxies from proxyscrape", len(proxies))
            return proxies
    except Exception as exc:
        log.warning("searchuser: proxy list fetch failed: %s", exc)
        return []


class _ProxyPool:
    """
    Round-robin proxy pool with dead-proxy eviction.

    Priority order:
      1. PROXY_URL environment variable (always tried first if set)
      2. Proxies fetched from proxyscrape
      3. None (direct connection — last resort, likely blocked)
    """

    def __init__(self, env_proxy: str | None, fetched: list[str]) -> None:
        self._env_proxy = env_proxy
        self._pool = list(fetched)
        self._dead: set[str] = set()
        self._idx = 0

    def next(self) -> str | None:
        """Return the next candidate proxy URL, or None for a direct request."""
        # Always try the env proxy first (it never gets permanently evicted)
        if self._env_proxy and self._env_proxy not in self._dead:
            return self._env_proxy

        # Round-robin through the fetched pool, skipping dead ones
        alive = [p for p in self._pool if p not in self._dead]
        if not alive:
            return None  # direct connection
        proxy = alive[self._idx % len(alive)]
        self._idx += 1
        return proxy

    def mark_dead(self, proxy: str | None) -> None:
        if proxy:
            self._dead.add(proxy)

    @property
    def any_alive(self) -> bool:
        alive_fetched = any(p for p in self._pool if p not in self._dead)
        env_alive = bool(self._env_proxy and self._env_proxy not in self._dead)
        return env_alive or alive_fetched


# ── Single check ──────────────────────────────────────────────────────────────

class _CheckResult:
    """Result of a single username availability probe."""
    __slots__ = ("available", "taken", "rate_limited", "proxy_error")

    def __init__(self, *, available=False, taken=False, rate_limited=False, proxy_error=False):
        self.available    = available
        self.taken        = taken
        self.rate_limited = rate_limited
        self.proxy_error  = proxy_error


async def _check_one(
    session: aiohttp.ClientSession,
    username: str,
    proxy: str | None,
) -> _CheckResult:
    """
    POST one username check to the Discord API, optionally through *proxy*.
    Returns a _CheckResult; never raises.
    """
    try:
        kwargs: dict = dict(
            json={"username": username},
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=PROXY_TIMEOUT),
        )
        if proxy:
            kwargs["proxy"] = proxy

        async with session.post(DISCORD_API_URL, **kwargs) as resp:
            if resp.status == 200:
                data = await resp.json()
                taken = data.get("taken", True)
                return _CheckResult(available=not taken, taken=taken)

            if resp.status == 429:
                log.warning("searchuser: Discord 429 via proxy=%s", proxy)
                return _CheckResult(rate_limited=True)

            # 403, 502, 503 etc. — proxy is likely blocked or broken
            log.debug("searchuser: HTTP %d via proxy=%s for %s", resp.status, proxy, username)
            return _CheckResult(proxy_error=True)

    except (aiohttp.ClientProxyConnectionError,
            aiohttp.ClientConnectorError,
            aiohttp.ServerConnectionError,
            asyncio.TimeoutError) as exc:
        log.debug("searchuser: proxy error (%s) proxy=%s: %s", type(exc).__name__, proxy, exc)
        return _CheckResult(proxy_error=True)
    except Exception as exc:
        log.warning("searchuser: unexpected error proxy=%s: %s", proxy, exc)
        return _CheckResult(proxy_error=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class SearchUser(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="searchuser")
    async def searchuser(self, ctx: commands.Context, length: int = 5):
        """Find available Discord usernames of a given character length (3–15)."""

        # ── Validate length ────────────────────────────────────────────────────
        if not 3 <= length <= 15:
            return await ctx.send(
                embed=discord.Embed(
                    description="❌ Length must be between **3** and **15**.",
                    color=0xC0392B,
                ),
                delete_after=8,
            )

        # ── Send placeholder ───────────────────────────────────────────────────
        msg = await ctx.send(
            embed=discord.Embed(
                title="Username Search Results",
                description="🔍  Fetching proxies and searching — please wait...",
                color=COL_LAVENDER,
            )
        )

        # ── Build proxy pool ───────────────────────────────────────────────────
        env_proxy = os.environ.get("PROXY_URL") or None  # e.g. http://user:pass@ip:port

        available:      list[str] = []
        attempts:       int       = 0
        rate_limited:   bool      = False
        all_proxies_dead: bool    = False

        async with aiohttp.ClientSession() as session:
            fetched_proxies = await _fetch_proxy_list(session)
            pool = _ProxyPool(env_proxy, fetched_proxies)

            # ── Main search loop ───────────────────────────────────────────────
            while len(available) < TARGET_FOUND and attempts < MAX_ATTEMPTS:
                username = _gen_username(length)
                attempts += 1

                # Try up to PROXY_RETRIES different proxies for this username
                succeeded = False
                for _retry in range(PROXY_RETRIES):
                    proxy = pool.next()

                    result = await _check_one(session, username, proxy)

                    if result.rate_limited:
                        rate_limited = True
                        break  # genuine 429 — stop everything

                    if result.proxy_error:
                        pool.mark_dead(proxy)
                        if not pool.any_alive:
                            # Fall back to direct for remaining attempts
                            direct = await _check_one(session, username, None)
                            if direct.rate_limited:
                                rate_limited = True
                            elif direct.available:
                                available.append(username)
                            # Whether direct worked or not, move to next username
                            succeeded = True
                        continue  # try next proxy

                    # Proxy worked — record result
                    if result.available:
                        available.append(username)
                    succeeded = True
                    break  # done with this username

                if rate_limited:
                    break

                if not succeeded and not pool.any_alive:
                    all_proxies_dead = True
                    break

                if attempts < MAX_ATTEMPTS and len(available) < TARGET_FOUND:
                    await asyncio.sleep(INTER_REQUEST_GAP)

        # ── Build result embed ─────────────────────────────────────────────────
        result_embed = discord.Embed(
            title="Username Search Results",
            color=COL_LAVENDER,
            timestamp=datetime.now(timezone.utc),
        )
        result_embed.add_field(name="Length Searched", value=f"`{length}` characters", inline=True)
        result_embed.add_field(name="Attempts Made",   value=f"`{attempts}` / `{MAX_ATTEMPTS}`", inline=True)
        result_embed.add_field(name="\u200b",          value="\u200b", inline=True)

        proxy_source = (
            "custom (`PROXY_URL`)" if env_proxy
            else f"{len(fetched_proxies)} from proxyscrape" if fetched_proxies
            else "none — direct connection"
        )
        result_embed.add_field(name="Proxy Source", value=proxy_source, inline=False)

        if rate_limited:
            result_embed.add_field(
                name="⚠️  Rate Limited by Discord",
                value=(
                    "Discord returned a `429` even through the proxy. "
                    "Wait a few minutes before trying again."
                ),
                inline=False,
            )
        elif all_proxies_dead:
            result_embed.add_field(
                name="⚠️  All Proxies Exhausted",
                value=(
                    "Every proxy in the pool was blocked or timed out. "
                    "Set a `PROXY_URL` secret with a reliable proxy, or try again later."
                ),
                inline=False,
            )

        if available:
            result_embed.add_field(
                name=f"✅  Available Usernames — {len(available)} found",
                value="\n".join(f"• `{n}`" for n in available),
                inline=False,
            )
        else:
            result_embed.add_field(
                name="❌  No Available Usernames Found",
                value=(
                    "None were found within the attempt limit.\n"
                    "Try again or use a **larger length** — `5` or `6` is recommended."
                ),
                inline=False,
            )

        result_embed.set_footer(
            text=(
                "Note: 3-4 character usernames are highly competitive and mostly taken. "
                "Set PROXY_URL for a reliable private proxy."
            )
        )

        try:
            await msg.edit(embed=result_embed)
        except discord.HTTPException:
            await ctx.send(embed=result_embed)

    @searchuser.error
    async def _searchuser_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(
                embed=discord.Embed(
                    description="❌ Length must be a whole number between **3** and **15**.",
                    color=0xC0392B,
                ),
                delete_after=8,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SearchUser(bot))
