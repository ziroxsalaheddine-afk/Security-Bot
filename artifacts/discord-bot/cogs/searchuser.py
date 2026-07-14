"""
Search User Cog
───────────────
+searchuser [length]  —  Find available Discord usernames of the given length.

Attempt-counting rules:
  • Only a Discord 200 OK (taken: true/false) counts as one of the 15 attempts.
  • 403 / 429 / timeout / connection error → discard proxy, pick a new one,
    retry the SAME username immediately. Attempt counter does NOT advance.
  • After 100 consecutive proxy failures with no 200 OK, the search stops.

Proxy priority (per request):
  1. PROXY_URL env-var (tried first, never permanently evicted)
  2. SOCKS5 proxies  — most reliable for bypassing Cloudflare/Discord blocks
  3. HTTPS proxies   — support TLS CONNECT tunnels
  4. HTTP proxies    — last resort
  5. Direct          — when pool is fully dead

SOCKS5 requests use aiohttp_socks.ProxyConnector (one connector per probe).
HTTP/HTTPS requests use aiohttp's built-in proxy= parameter on the shared session.
"""

import asyncio
import logging
import os
import random
import string
from datetime import datetime, timezone

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyConnectionError as SocksConnError
import discord
from discord.ext import commands

log = logging.getLogger("guardian.searchuser")

# ── Constants ──────────────────────────────────────────────────────────────────

COL_LAVENDER = 0xC8B6FF

DISCORD_API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"

PROXY_SOURCES: list[tuple[str, str]] = [
    # (url, protocol_prefix_for_bare_ip:port_lines)
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",  "socks5"),
    ("https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",       "socks5"),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/https.txt",   "https"),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",    "http"),
    ("https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",         "http"),
    (
        "https://api.proxyscrape.com/v2/"
        "?request=displayproxies&protocol=http&timeout=2000"
        "&country=all&ssl=yes&anonymity=anonymous",
        "http",
    ),
]

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept":        "application/json",
}

MAX_ATTEMPTS          = 15    # only 200 OK responses count toward this
TARGET_FOUND          = 5     # stop early once this many available names found
MAX_CONSECUTIVE_FAILS = 100   # bail after this many consecutive proxy failures
PROXY_REQUEST_TIMEOUT = 1.5   # seconds per proxied request
SOURCE_FETCH_TIMEOUT  = 12    # seconds to fetch one proxy-list source
INTER_ATTEMPT_GAP     = 0.15  # seconds between successful 200 OK attempts
TYPING_INTERVAL       = 5.0   # seconds between typing keepalives

_ALL_CHARS  = string.ascii_lowercase + string.digits + "_."
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

def _normalise_proxy_line(line: str, default_proto: str) -> str | None:
    """
    Turn a raw proxy line into a fully-qualified URL string.

    Handles:
      • Lines already prefixed:  socks5://1.2.3.4:1080   → unchanged
      • Bare ip:port lines:      1.2.3.4:1080             → <default_proto>://1.2.3.4:1080
      • Blank / comment lines:   ''  /  '#...'            → None
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    known = ("socks5://", "socks4://", "https://", "http://")
    if any(line.startswith(p) for p in known):
        return line
    if ":" in line:
        return f"{default_proto}://{line}"
    return None


async def _fetch_one_source(
    session: aiohttp.ClientSession,
    url: str,
    default_proto: str,
) -> list[str]:
    """Fetch one proxy-list source. Returns fully-qualified proxy URL strings."""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=SOURCE_FETCH_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                log.debug("proxy source %s → HTTP %d", url, resp.status)
                return []
            text    = await resp.text()
            results = []
            for raw in text.splitlines():
                normalised = _normalise_proxy_line(raw, default_proto)
                if normalised:
                    results.append(normalised)
            log.debug("proxy source %s → %d proxies", url, len(results))
            return results
    except Exception as exc:
        log.debug("proxy source %s fetch failed: %s", url, exc)
        return []


async def _build_proxy_pool(session: aiohttp.ClientSession) -> list[str]:
    """
    Fetch all proxy sources concurrently, deduplicate, then sort by protocol:
      SOCKS5 → HTTPS → HTTP
    Each group is shuffled independently for load distribution.
    """
    tasks   = [_fetch_one_source(session, url, proto) for url, proto in PROXY_SOURCES]
    lists   = await asyncio.gather(*tasks)
    merged  = list({p for sub in lists for p in sub})  # deduplicate

    socks5 = [p for p in merged if p.startswith("socks5://")]
    https  = [p for p in merged if p.startswith("https://")]
    http   = [p for p in merged if p.startswith("http://")]

    random.shuffle(socks5)
    random.shuffle(https)
    random.shuffle(http)

    ordered = socks5 + https + http
    log.info(
        "searchuser: proxy pool — %d SOCKS5 + %d HTTPS + %d HTTP = %d total",
        len(socks5), len(https), len(http), len(ordered),
    )
    return ordered


# ── Proxy pool ────────────────────────────────────────────────────────────────

class _ProxyPool:
    def __init__(self, env_proxy: str | None, fetched: list[str]) -> None:
        self._env   = env_proxy
        self._pool  = list(fetched)
        self._dead: set[str] = set()
        self._idx   = 0

    def next(self) -> str | None:
        if self._env and self._env not in self._dead:
            return self._env
        alive = [p for p in self._pool if p not in self._dead]
        if not alive:
            return None
        proxy = alive[self._idx % len(alive)]
        self._idx += 1
        return proxy

    def mark_dead(self, proxy: str | None) -> None:
        if proxy:
            self._dead.add(proxy)

    @property
    def has_live(self) -> bool:
        return (
            bool(self._env and self._env not in self._dead)
            or any(p for p in self._pool if p not in self._dead)
        )

    @property
    def total(self) -> int:
        return (1 if self._env else 0) + len(self._pool)

    @property
    def dead_count(self) -> int:
        return len(self._dead)


# ── Single-request probe ───────────────────────────────────────────────────────

class _Probe:
    __slots__ = ("ok", "available", "rate_limited", "bad_proxy")

    def __init__(self, *, ok=False, available=False, rate_limited=False, bad_proxy=False):
        self.ok           = ok
        self.available    = available
        self.rate_limited = rate_limited
        self.bad_proxy    = bad_proxy


async def _probe_http(
    session: aiohttp.ClientSession,
    username: str,
    proxy: str | None,
) -> _Probe:
    """Probe via HTTP/HTTPS proxy (or direct) using the shared session."""
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
                data = await resp.json()
                return _Probe(ok=True, available=not data.get("taken", True))
            if resp.status == 429:
                return _Probe(rate_limited=True)
            return _Probe(bad_proxy=True)

    except (
        aiohttp.ClientProxyConnectionError,
        aiohttp.ClientConnectorError,
        aiohttp.ServerConnectionError,
        aiohttp.ClientOSError,
        asyncio.TimeoutError,
    ) as exc:
        log.debug("searchuser: http probe err (%s) proxy=%s", type(exc).__name__, proxy)
        return _Probe(bad_proxy=True)
    except Exception as exc:
        log.warning("searchuser: http probe unexpected error proxy=%s: %s", proxy, exc)
        return _Probe(bad_proxy=True)


async def _probe_socks5(username: str, proxy: str) -> _Probe:
    """Probe via SOCKS5 proxy — creates a dedicated connector per call."""
    try:
        connector = ProxyConnector.from_url(
            proxy,
            rdns=True,
            ssl=False,
        )
        async with aiohttp.ClientSession(
            connector=connector,
            headers=_BROWSER_HEADERS,
        ) as sess:
            async with sess.post(
                DISCORD_API_URL,
                json={"username": username},
                timeout=aiohttp.ClientTimeout(total=PROXY_REQUEST_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return _Probe(ok=True, available=not data.get("taken", True))
                if resp.status == 429:
                    return _Probe(rate_limited=True)
                return _Probe(bad_proxy=True)

    except (SocksConnError, asyncio.TimeoutError) as exc:
        log.debug("searchuser: socks5 probe err (%s) proxy=%s", type(exc).__name__, proxy)
        return _Probe(bad_proxy=True)
    except Exception as exc:
        log.warning("searchuser: socks5 probe unexpected error proxy=%s: %s", proxy, exc)
        return _Probe(bad_proxy=True)


async def _probe(
    session: aiohttp.ClientSession,
    username: str,
    proxy: str | None,
) -> _Probe:
    """Route to the correct probe function based on proxy protocol."""
    if proxy and proxy.startswith("socks5://"):
        return await _probe_socks5(username, proxy)
    return await _probe_http(session, username, proxy)


# ── Typing keepalive ──────────────────────────────────────────────────────────

async def _typing_keepalive(channel: discord.TextChannel, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await channel.trigger_typing()
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=TYPING_INTERVAL)
        except asyncio.TimeoutError:
            pass


# ── Cog ──────────────────────────────────────────────────────────────────────

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

        available:            list[str] = []
        attempts:             int       = 0
        consecutive_fails:    int       = 0
        stopped_rate_limited: bool      = False
        stopped_no_proxies:   bool      = False

        stop_event  = asyncio.Event()
        typing_task = asyncio.create_task(_typing_keepalive(ctx.channel, stop_event))

        try:
            async with aiohttp.ClientSession() as session:
                fetched = await _build_proxy_pool(session)
                pool    = _ProxyPool(env_proxy, fetched)

                # Count protocol breakdown for the embed
                n_socks5 = sum(1 for p in fetched if p.startswith("socks5://"))
                n_https  = sum(1 for p in fetched if p.startswith("https://"))
                n_http   = len(fetched) - n_socks5 - n_https

                while len(available) < TARGET_FOUND and attempts < MAX_ATTEMPTS:
                    username = _gen_username(length)

                    while True:
                        proxy  = pool.next()
                        result = await _probe(session, username, proxy)

                        if result.ok:
                            attempts          += 1
                            consecutive_fails  = 0
                            if result.available:
                                available.append(username)
                            break

                        if result.rate_limited:
                            pool.mark_dead(proxy)
                            consecutive_fails += 1
                            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                                stopped_rate_limited = True
                                break
                            continue

                        # bad_proxy: 403 / 5xx / timeout / conn error
                        pool.mark_dead(proxy)
                        consecutive_fails += 1

                        if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                            stopped_no_proxies = True
                            break

                        if not pool.has_live:
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

        # ── Result embed ──────────────────────────────────────────────────────
        embed = discord.Embed(
            title="Username Search Results",
            color=COL_LAVENDER,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Length Searched", value=f"`{length}` characters",                 inline=True)
        embed.add_field(name="Valid Attempts",  value=f"`{attempts}` / `{MAX_ATTEMPTS}`",       inline=True)
        embed.add_field(name="Proxies Tested",  value=f"`{pool.dead_count}` / `{pool.total}`",  inline=True)

        pool_detail = (
            f"🟣 SOCKS5: `{n_socks5}`  "
            f"🔒 HTTPS: `{n_https}`  "
            f"🌐 HTTP: `{n_http}`"
        ) if fetched else "none — direct connection only"
        if env_proxy:
            pool_detail = f"Custom `PROXY_URL` + {pool_detail}"
        embed.add_field(name="Proxy Pool", value=pool_detail, inline=False)

        if stopped_rate_limited:
            embed.add_field(
                name="⚠️  Rate Limited by Discord",
                value=(
                    f"`{MAX_CONSECUTIVE_FAILS}` proxies in a row received `429`. "
                    "Discord is actively rate-limiting. "
                    "Wait a few minutes or set `PROXY_URL` with a premium/residential proxy."
                ),
                inline=False,
            )
        elif stopped_no_proxies:
            embed.add_field(
                name="⚠️  Proxy Pool Exhausted",
                value=(
                    f"`{MAX_CONSECUTIVE_FAILS}` consecutive proxy failures. "
                    "All fetched proxies are blocked or unreachable from this host. "
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
            text="Note: 3-4 char usernames are highly competitive. Set PROXY_URL for reliable results."
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
