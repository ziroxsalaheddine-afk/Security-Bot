"""
Search User Cog
───────────────
+searchuser [length]  —  Find available Discord usernames of the given length.

Attempt-counting rules:
  • Only a Discord 200 OK (taken: true/false) counts as one of the 15 attempts.
  • 403 / 429 / timeout / connection error → discard proxy, pick a new one,
    retry the SAME username immediately. Attempt counter does NOT advance.
  • After 100 consecutive proxy failures with no 200 OK, the search stops.

Proxy priority:
  1. PROXY_URL env-var (always tried first; never permanently evicted)
  2. Merged pool from three public sources (GitHub × 2 + proxyscrape)
     — HTTPS proxies sorted before HTTP; each group shuffled independently
  3. Direct connection — last resort when pool is fully dead

Performance:
  • 1.5 s per-proxy timeout so dead proxies are discarded in under 2 s
  • Typing keepalive fires every 5 s so Discord never considers the bot frozen
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

DISCORD_API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/https.txt",
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

MAX_ATTEMPTS          = 15    # only 200 OK responses count toward this
TARGET_FOUND          = 5     # stop early once this many available names found
MAX_CONSECUTIVE_FAILS = 100   # bail after this many consecutive proxy failures
PROXY_REQUEST_TIMEOUT = 1.5   # seconds per proxied request (fast discard)
SOURCE_FETCH_TIMEOUT  = 12    # seconds to fetch one proxy-list source
INTER_ATTEMPT_GAP     = 0.15  # seconds between 200 OK attempts
TYPING_INTERVAL       = 5.0   # seconds between typing keepalives

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
    """Fetch one proxy-list source. Returns a list of proxy URL strings."""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=SOURCE_FETCH_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.debug("proxy source %s → HTTP %d", url, resp.status)
                return []
            text = await resp.text()
            results: list[str] = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("https://") or line.startswith("http://"):
                    results.append(line)
                elif ":" in line:
                    # bare ip:port — assume http
                    results.append(f"http://{line}")
            log.debug("proxy source %s → %d proxies", url, len(results))
            return results
    except Exception as exc:
        log.debug("proxy source %s fetch failed: %s", url, exc)
        return []


async def _build_proxy_pool(session: aiohttp.ClientSession) -> list[str]:
    """
    Fetch all proxy sources concurrently, deduplicate, then sort so HTTPS
    proxies come before HTTP (each group shuffled independently).
    Returns a flat list of proxy URL strings.
    """
    lists   = await asyncio.gather(*[_fetch_one_source(session, u) for u in PROXY_SOURCES])
    merged  = list({p for sublist in lists for p in sublist})  # deduplicate

    https_pool = [p for p in merged if p.startswith("https://")]
    http_pool  = [p for p in merged if not p.startswith("https://")]
    random.shuffle(https_pool)
    random.shuffle(http_pool)
    ordered = https_pool + http_pool  # HTTPS first — better Cloudflare bypass

    log.info(
        "searchuser: proxy pool — %d HTTPS + %d HTTP = %d total",
        len(https_pool), len(http_pool), len(ordered),
    )
    return ordered


# ── Proxy pool with round-robin + dead-proxy eviction ─────────────────────────

class _ProxyPool:
    """
    Tracks live proxies, evicts dead ones, round-robins through the rest.
    The PROXY_URL env proxy re-enters the rotation on the next command invocation.
    """

    def __init__(self, env_proxy: str | None, fetched: list[str]) -> None:
        self._env   = env_proxy
        self._pool  = list(fetched)
        self._dead: set[str] = set()
        self._idx   = 0

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

    @property
    def dead_count(self) -> int:
        return len(self._dead)


# ── Single-request probe ───────────────────────────────────────────────────────

class _Probe:
    """Outcome of one POST to the Discord username endpoint."""
    __slots__ = ("ok", "available", "rate_limited", "bad_proxy")

    def __init__(self, *, ok=False, available=False, rate_limited=False, bad_proxy=False):
        self.ok           = ok
        self.available    = available
        self.rate_limited = rate_limited  # Discord 429 through a live proxy
        self.bad_proxy    = bad_proxy     # 403 / 5xx / timeout / conn error


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
            # 403, 502, 503, etc.
            log.debug("searchuser: HTTP %d via proxy=%s for %s", resp.status, proxy, username)
            return _Probe(bad_proxy=True)

    except (
        aiohttp.ClientProxyConnectionError,
        aiohttp.ClientConnectorError,
        aiohttp.ServerConnectionError,
        aiohttp.ClientOSError,
        asyncio.TimeoutError,
    ) as exc:
        log.debug("searchuser: proxy conn err (%s) proxy=%s", type(exc).__name__, proxy)
        return _Probe(bad_proxy=True)
    except Exception as exc:
        log.warning("searchuser: unexpected probe error proxy=%s: %s", proxy, exc)
        return _Probe(bad_proxy=True)


# ── Typing keepalive ───────────────────────────────────────────────────────────

async def _typing_keepalive(channel: discord.TextChannel, stop: asyncio.Event) -> None:
    """
    Trigger the typing indicator every TYPING_INTERVAL seconds until *stop* is set.
    Prevents Discord from showing the bot as unresponsive during long proxy cycling.
    """
    while not stop.is_set():
        try:
            await channel.trigger_typing()
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=TYPING_INTERVAL)
        except asyncio.TimeoutError:
            pass


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

        available:             list[str] = []
        attempts:              int       = 0
        consecutive_fails:     int       = 0
        stopped_rate_limited:  bool      = False
        stopped_no_proxies:    bool      = False

        # Typing keepalive — fires every TYPING_INTERVAL seconds in the background
        stop_event   = asyncio.Event()
        typing_task  = asyncio.create_task(_typing_keepalive(ctx.channel, stop_event))

        try:
            async with aiohttp.ClientSession() as session:
                fetched = await _build_proxy_pool(session)
                pool    = _ProxyPool(env_proxy, fetched)

                while len(available) < TARGET_FOUND and attempts < MAX_ATTEMPTS:
                    username = _gen_username(length)

                    # ── Inner loop: rotate proxies until we get a 200 OK ──────
                    while True:
                        proxy  = pool.next()
                        result = await _probe(session, username, proxy)

                        if result.ok:
                            attempts          += 1
                            consecutive_fails  = 0
                            if result.available:
                                available.append(username)
                            break  # move to next username

                        if result.rate_limited:
                            pool.mark_dead(proxy)
                            consecutive_fails += 1
                            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                                stopped_rate_limited = True
                                break
                            continue  # retry same username with next proxy

                        # bad_proxy: 403 / 5xx / timeout
                        pool.mark_dead(proxy)
                        consecutive_fails += 1

                        if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                            stopped_no_proxies = True
                            break

                        if not pool.has_live:
                            # Pool fully dead — one last direct attempt
                            direct = await _probe(session, username, None)
                            if direct.ok:
                                attempts          += 1
                                consecutive_fails  = 0
                                if direct.available:
                                    available.append(username)
                            else:
                                stopped_no_proxies = True
                            break

                    if stopped_rate_limited or stopped_no_proxies:
                        break

                    if attempts < MAX_ATTEMPTS and len(available) < TARGET_FOUND:
                        await asyncio.sleep(INTER_ATTEMPT_GAP)

        finally:
            stop_event.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        # ── Build result embed ─────────────────────────────────────────────────
        embed = discord.Embed(
            title="Username Search Results",
            color=COL_LAVENDER,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Length Searched", value=f"`{length}` characters",            inline=True)
        embed.add_field(name="Valid Attempts",  value=f"`{attempts}` / `{MAX_ATTEMPTS}`",  inline=True)
        embed.add_field(name="Proxies Tested",  value=f"`{pool.dead_count}` / `{pool.total}`", inline=True)

        https_count = sum(1 for p in fetched if p.startswith("https://"))
        http_count  = len(fetched) - https_count
        proxy_src = (
            f"custom `PROXY_URL` + {len(fetched)} fetched "
            f"({https_count} HTTPS, {http_count} HTTP)" if env_proxy
            else f"{len(fetched)} proxies ({https_count} HTTPS, {http_count} HTTP)" if fetched
            else "none — direct connection only"
        )
        embed.add_field(name="Proxy Pool", value=proxy_src, inline=False)

        if stopped_rate_limited:
            embed.add_field(
                name="⚠️  Rate Limited by Discord",
                value=(
                    f"`{MAX_CONSECUTIVE_FAILS}` proxies in a row received `429`. "
                    "Discord is actively rate-limiting all these IPs. "
                    "Wait a few minutes, or set a `PROXY_URL` secret with a premium proxy."
                ),
                inline=False,
            )
        elif stopped_no_proxies:
            embed.add_field(
                name="⚠️  Proxy Pool Exhausted",
                value=(
                    f"`{MAX_CONSECUTIVE_FAILS}` consecutive proxy failures. "
                    "All fetched proxies are blocked or unreachable. "
                    "Set a `PROXY_URL` secret with a reliable private/residential proxy."
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
