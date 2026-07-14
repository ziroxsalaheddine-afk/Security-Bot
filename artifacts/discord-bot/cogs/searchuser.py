"""
Search User Cog
───────────────
+searchuser [length]  —  Find available Discord usernames of the given length.

Attempt-counting rules:
  • Only a Discord 200 OK (taken: true/false) counts as one of the 15 attempts.
  • 403 / 429 / timeout / connection error → discard proxy, pick a new one,
    retry the SAME username immediately. Attempt counter does NOT advance.
  • After 10 consecutive proxy failures with no 200 OK, the search stops.

Proxy priority:
  1. PROXY_URL env-var (always tried first; never permanently evicted)
  2. Merged pool from three public sources (GitHub × 2 + proxyscrape), shuffled
  3. Direct connection — last resort when pool is fully dead
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

# ── Constants ──────────────────────────────────────────────────────────────────

COL_LAVENDER = 0xC8B6FF
FOOTER       = "© 2026 — developed by zrx.gg"

DISCORD_API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    (
        "https://api.proxyscrape.com/v2/"
        "?request=displayproxies&protocol=http&timeout=2000"
        "&country=all&ssl=yes&anonymity=anonymous"
    ),
]

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
}

MAX_ATTEMPTS            = 15   # only 200 OK responses count toward this
TARGET_FOUND            = 5    # stop early once this many available names found
MAX_CONSECUTIVE_FAILS   = 10   # bail if this many proxies in a row all fail
PROXY_REQUEST_TIMEOUT   = 8    # seconds per proxied Discord request
SOURCE_FETCH_TIMEOUT    = 12   # seconds to fetch one proxy-list source
INTER_ATTEMPT_GAP       = 0.15 # seconds between attempts (after a 200 OK)

# Characters valid anywhere in a Discord username
_ALL_CHARS  = string.ascii_lowercase + string.digits + "_."
# Safe for first/last position (period not allowed at edges)
_SAFE_CHARS = string.ascii_lowercase + string.digits + "_"


# ── Username generator ─────────────────────────────────────────────────────────

def _gen_username(length: int) -> str:
    """Generate a random Discord-valid username of *length* characters."""
    while True:
        chars: list[str] = []
        for i in range(length):
            pool = _SAFE_CHARS if (i == 0 or i == length - 1) else _ALL_CHARS
            chars.append(random.choice(pool))
        name = "".join(chars)
        if ".." not in name:
            return name


# ── Proxy fetching ─────────────────────────────────────────────────────────────

async def _fetch_one_source(session: aiohttp.ClientSession, url: str) -> list[str]:
    """Fetch one proxy-list source. Returns a list of 'http://ip:port' strings."""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=SOURCE_FETCH_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.debug("proxy source %s → HTTP %d", url, resp.status)
                return []
            text = await resp.text()
            results = []
            for line in text.splitlines():
                line = line.strip()
                # Accept bare ip:port or lines that already start with http://
                if not line or line.startswith("#"):
                    continue
                if line.startswith("http://") or line.startswith("https://"):
                    results.append(line)
                elif ":" in line:
                    results.append(f"http://{line}")
            log.debug("proxy source %s → %d proxies", url, len(results))
            return results
    except Exception as exc:
        log.debug("proxy source %s fetch failed: %s", url, exc)
        return []


async def _build_proxy_pool(session: aiohttp.ClientSession) -> list[str]:
    """
    Fetch all proxy sources concurrently, merge, deduplicate, and shuffle.
    Returns a flat list of 'http://ip:port' strings.
    """
    lists = await asyncio.gather(*[_fetch_one_source(session, u) for u in PROXY_SOURCES])
    merged = list({p for sublist in lists for p in sublist})  # deduplicate via set
    random.shuffle(merged)
    log.info("searchuser: proxy pool built — %d unique proxies from %d sources",
             len(merged), len(PROXY_SOURCES))
    return merged


# ── Proxy pool with round-robin + dead-proxy eviction ─────────────────────────

class _ProxyPool:
    """
    Tracks live proxies, evicts dead ones, round-robins through the rest.
    The PROXY_URL env proxy is always tried first and only evicted temporarily
    when it fails (it re-enters on the next command invocation).
    """

    def __init__(self, env_proxy: str | None, fetched: list[str]) -> None:
        self._env    = env_proxy
        self._pool   = list(fetched)
        self._dead:  set[str] = set()
        self._idx    = 0

    def next(self) -> str | None:
        """Return the next candidate proxy URL, or None for a direct request."""
        if self._env and self._env not in self._dead:
            return self._env
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
    def has_live(self) -> bool:
        env_live     = bool(self._env and self._env not in self._dead)
        fetched_live = any(p for p in self._pool if p not in self._dead)
        return env_live or fetched_live

    @property
    def total(self) -> int:
        return (1 if self._env else 0) + len(self._pool)


# ── Single-request probe ───────────────────────────────────────────────────────

class _Probe:
    """Outcome of one POST to the Discord username endpoint."""
    __slots__ = ("ok", "available", "rate_limited", "bad_proxy")

    def __init__(self, *, ok=False, available=False, rate_limited=False, bad_proxy=False):
        self.ok           = ok           # got a 200 — counts as one attempt
        self.available    = available    # taken: false
        self.rate_limited = rate_limited # Discord returned 429 through a live proxy
        self.bad_proxy    = bad_proxy    # 403 / 5xx / timeout / connection error


async def _probe(
    session: aiohttp.ClientSession,
    username: str,
    proxy: str | None,
) -> _Probe:
    """POST one username check; never raises."""
    try:
        kw: dict = dict(
            json={"username": username},
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=PROXY_REQUEST_TIMEOUT),
        )
        if proxy:
            kw["proxy"] = proxy

        async with session.post(DISCORD_API_URL, **kw) as resp:
            if resp.status == 200:
                data  = await resp.json()
                taken = data.get("taken", True)
                return _Probe(ok=True, available=not taken)
            if resp.status == 429:
                log.debug("searchuser: 429 via proxy=%s", proxy)
                return _Probe(rate_limited=True)
            # 403, 502, 503, etc. — proxy is blocked or broken
            log.debug("searchuser: HTTP %d via proxy=%s for %s", resp.status, proxy, username)
            return _Probe(bad_proxy=True)

    except (
        aiohttp.ClientProxyConnectionError,
        aiohttp.ClientConnectorError,
        aiohttp.ServerConnectionError,
        asyncio.TimeoutError,
        aiohttp.ClientOSError,
    ) as exc:
        log.debug("searchuser: proxy conn error (%s) proxy=%s", type(exc).__name__, proxy)
        return _Probe(bad_proxy=True)
    except Exception as exc:
        log.warning("searchuser: unexpected probe error proxy=%s: %s", proxy, exc)
        return _Probe(bad_proxy=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class SearchUser(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="searchuser")
    async def searchuser(self, ctx: commands.Context, length: int = 5):
        """Find available Discord usernames of a given character length (3–15)."""

        if not 3 <= length <= 15:
            return await ctx.send(
                embed=discord.Embed(
                    description="❌ Length must be between **3** and **15**.",
                    color=0xC0392B,
                ),
                delete_after=8,
            )

        msg = await ctx.send(
            embed=discord.Embed(
                title="Username Search Results",
                description="🔍  Loading proxies and searching — please wait...",
                color=COL_LAVENDER,
            )
        )

        env_proxy = os.environ.get("PROXY_URL") or None

        available:           list[str] = []
        attempts:            int       = 0   # only counts 200 OK responses
        consecutive_fails:   int       = 0
        stopped_rate_limited: bool     = False
        stopped_no_proxies:   bool     = False

        async with aiohttp.ClientSession() as session:
            fetched = await _build_proxy_pool(session)
            pool    = _ProxyPool(env_proxy, fetched)

            while len(available) < TARGET_FOUND and attempts < MAX_ATTEMPTS:
                username = _gen_username(length)

                # ── Inner loop: keep trying proxies until we get a 200 OK ──────
                while True:
                    proxy  = pool.next()
                    result = await _probe(session, username, proxy)

                    if result.ok:
                        # ✅ Real Discord response — counts as one attempt
                        attempts          += 1
                        consecutive_fails  = 0
                        if result.available:
                            available.append(username)
                        break  # move to next username

                    if result.rate_limited:
                        # Discord is actually rate-limiting us through a live proxy
                        consecutive_fails += 1
                        pool.mark_dead(proxy)
                        if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                            stopped_rate_limited = True
                            break
                        # Try another proxy for the same username
                        continue

                    # bad_proxy: 403 / 5xx / timeout
                    pool.mark_dead(proxy)
                    consecutive_fails += 1

                    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        stopped_no_proxies = True
                        break

                    if not pool.has_live:
                        # No proxies left at all — one last direct attempt
                        direct = await _probe(session, username, None)
                        if direct.ok:
                            attempts          += 1
                            consecutive_fails  = 0
                            if direct.available:
                                available.append(username)
                        else:
                            stopped_no_proxies = True
                        break

                # ── Check outer-loop exit conditions ──────────────────────────
                if stopped_rate_limited or stopped_no_proxies:
                    break

                if attempts < MAX_ATTEMPTS and len(available) < TARGET_FOUND:
                    await asyncio.sleep(INTER_ATTEMPT_GAP)

        # ── Build result embed ─────────────────────────────────────────────────
        embed = discord.Embed(
            title="Username Search Results",
            color=COL_LAVENDER,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Length Searched",   value=f"`{length}` characters",         inline=True)
        embed.add_field(name="Valid Attempts",     value=f"`{attempts}` / `{MAX_ATTEMPTS}`", inline=True)
        embed.add_field(name="Proxies in Pool",    value=f"`{pool.total}`",                inline=True)

        proxy_src = (
            f"custom `PROXY_URL` + {len(fetched)} fetched" if env_proxy
            else f"{len(fetched)} from {len(PROXY_SOURCES)} sources" if fetched
            else "none available — direct connection"
        )
        embed.add_field(name="Proxy Source", value=proxy_src, inline=False)

        if stopped_rate_limited:
            embed.add_field(
                name="⚠️  Rate Limited by Discord",
                value=(
                    f"`{MAX_CONSECUTIVE_FAILS}` proxies in a row received `429`. "
                    "Discord is actively rate-limiting. Wait a few minutes and try again, "
                    "or set a `PROXY_URL` secret with a premium/residential proxy."
                ),
                inline=False,
            )
        elif stopped_no_proxies:
            embed.add_field(
                name="⚠️  Proxy Pool Exhausted",
                value=(
                    f"`{MAX_CONSECUTIVE_FAILS}` consecutive proxy failures — "
                    "all fetched proxies are blocked or unreachable from this host. "
                    "Set a `PROXY_URL` secret with a reliable private proxy and try again."
                ),
                inline=False,
            )

        if available:
            embed.add_field(
                name=f"✅  Available Usernames — {len(available)} found",
                value="\n".join(f"• `{n}`" for n in available),
                inline=False,
            )
        else:
            embed.add_field(
                name="❌  No Available Usernames Found",
                value=(
                    "None confirmed available within the attempt limit.\n"
                    "Try again or use a **larger length** — `5` or `6` is recommended."
                ),
                inline=False,
            )

        embed.set_footer(
            text=(
                "Note: 3-4 char usernames are highly competitive. "
                "Set PROXY_URL for a reliable private proxy."
            )
        )

        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            await ctx.send(embed=embed)

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
