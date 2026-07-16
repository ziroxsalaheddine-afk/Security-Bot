"""
Search User Cog
───────────────
+searchuser [length]  —  Find available Discord usernames of the given length.

Attempt-counting rules:
  • Only a Discord 200 OK (taken: true/false) counts as a valid attempt.
    — taken: false  →  username is AVAILABLE  ✅
    — taken: true   →  username is TAKEN       ❌
    (Discord does NOT return 404 for available names on this endpoint.)
  • 403 / 429 / timeout / conn error → discard proxy, retry SAME username.
    Attempt counter does NOT advance on proxy failures.

Proxy modes:
  PRIVATE  — PROXY_URL secret is set:
    • Public pool skipped; all requests go through PROXY_URL (5 s timeout).
    • Up to 500 valid attempts before giving up.

  PUBLIC   — no PROXY_URL:
    • Fetches 6 000+ proxies; priority SOCKS5 → HTTPS → HTTP (1.0 s timeout).
    • Up to 100 valid attempts; 300 consecutive-failure threshold.

Live feedback:
  • Every 200 OK is printed to the Replit console with the result.
  • The Discord embed is updated live every 25 attempts (≥ 5 s between edits).
"""

import asyncio
import logging
import os
import random
import string
import time
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
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt", "socks5"),
    ("https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",      "socks5"),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/https.txt",  "https"),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",   "http"),
    ("https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",        "http"),
    (
        "https://api.proxyscrape.com/v2/"
        "?request=displayproxies&protocol=http&timeout=2000"
        "&country=all&ssl=yes&anonymity=anonymous",
        "http",
    ),
]

TARGET_FOUND              = 5     # stop early once this many names confirmed available

PRIVATE_MAX_ATTEMPTS      = 500   # max valid (200 OK) attempts in private-proxy mode
PRIVATE_REQUEST_TIMEOUT   = 5.0   # seconds per request through private proxy
PRIVATE_MAX_CONSEC_FAILS  = 9999  # effectively unlimited for private proxies

PUBLIC_MAX_ATTEMPTS       = 100   # max valid attempts when using public pool
PUBLIC_REQUEST_TIMEOUT    = 1.0   # seconds — dead proxies discarded instantly
PUBLIC_MAX_CONSEC_FAILS   = 300   # consecutive failures before giving up

SOURCE_FETCH_TIMEOUT      = 12    # seconds to fetch one proxy-list source
INTER_ATTEMPT_GAP         = 0.10  # seconds between successful 200 OK attempts
TYPING_INTERVAL           = 5.0   # seconds between typing keepalive triggers

# Live embed update settings
EMBED_UPDATE_EVERY        = 25    # update embed after this many new valid attempts
EMBED_UPDATE_MIN_INTERVAL = 5.0   # minimum seconds between embed edits (rate-limit guard)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept":        "application/json",
}

_ALL_CHARS  = string.ascii_lowercase + string.digits + "_."
_SAFE_CHARS = string.ascii_lowercase + string.digits + "_"


# ── Username generator ─────────────────────────────────────────────────────────

def _gen_username(length: int) -> str:
    """Generate a random Discord-valid username of *length* characters.

    Rules: a-z, 0-9, _, . only; no leading/trailing period; no double periods.
    """
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
                norm = _normalise_proxy_line(raw, default_proto)
                if norm:
                    results.append(norm)
            log.debug("proxy source %s → %d proxies", url, len(results))
            return results
    except Exception as exc:
        log.debug("proxy source %s fetch failed: %s", url, exc)
        return []


async def _build_proxy_pool(session: aiohttp.ClientSession) -> list[str]:
    """Fetch all sources concurrently, deduplicate, sort SOCKS5 → HTTPS → HTTP."""
    tasks  = [_fetch_one_source(session, url, proto) for url, proto in PROXY_SOURCES]
    lists  = await asyncio.gather(*tasks)
    merged = list({p for sub in lists for p in sub})

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
    def __init__(self, primary: str | None, fetched: list[str]) -> None:
        self._primary = primary
        self._pool    = list(fetched)
        self._dead:   set[str] = set()
        self._idx     = 0

    def next(self) -> str | None:
        if self._primary and self._primary not in self._dead:
            return self._primary
        alive = [p for p in self._pool if p not in self._dead]
        if not alive:
            return None
        proxy = alive[self._idx % len(alive)]
        self._idx += 1
        return proxy

    def mark_dead(self, proxy: str | None) -> None:
        # Never permanently evict the private proxy
        if proxy and proxy != self._primary:
            self._dead.add(proxy)

    @property
    def has_live(self) -> bool:
        if self._primary:
            return True
        return any(p for p in self._pool if p not in self._dead)

    @property
    def total(self) -> int:
        return (1 if self._primary else 0) + len(self._pool)

    @property
    def dead_count(self) -> int:
        return len(self._dead)


# ── Per-request probes ─────────────────────────────────────────────────────────

class _Probe:
    __slots__ = ("ok", "available", "status_code", "rate_limited", "bad_proxy")

    def __init__(
        self, *,
        ok=False, available=False, status_code=0,
        rate_limited=False, bad_proxy=False,
    ):
        self.ok          = ok
        self.available   = available
        self.status_code = status_code  # actual HTTP code when ok=True
        self.rate_limited = rate_limited
        self.bad_proxy   = bad_proxy


async def _probe_http(
    session: aiohttp.ClientSession,
    username: str,
    proxy: str | None,
    timeout: float,
) -> _Probe:
    try:
        kw: dict = dict(
            json={"username": username},
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        if proxy:
            kw["proxy"] = proxy

        async with session.post(DISCORD_API_URL, **kw) as resp:
            if resp.status == 200:
                data  = await resp.json()
                taken = data.get("taken", True)
                # taken: false = AVAILABLE, taken: true = TAKEN
                return _Probe(ok=True, available=not taken, status_code=200)
            if resp.status == 429:
                return _Probe(rate_limited=True, status_code=429)
            return _Probe(bad_proxy=True, status_code=resp.status)

    except (
        aiohttp.ClientProxyConnectionError,
        aiohttp.ClientConnectorError,
        aiohttp.ServerConnectionError,
        aiohttp.ClientOSError,
        asyncio.TimeoutError,
    ) as exc:
        log.debug("searchuser: http probe (%s) proxy=%s", type(exc).__name__, proxy)
        return _Probe(bad_proxy=True)
    except Exception as exc:
        log.warning("searchuser: http probe unexpected proxy=%s: %s", proxy, exc)
        return _Probe(bad_proxy=True)


async def _probe_socks5(username: str, proxy: str, timeout: float) -> _Probe:
    try:
        connector = ProxyConnector.from_url(proxy, rdns=True, ssl=False)
        async with aiohttp.ClientSession(
            connector=connector, headers=_BROWSER_HEADERS
        ) as sess:
            async with sess.post(
                DISCORD_API_URL,
                json={"username": username},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data  = await resp.json()
                    taken = data.get("taken", True)
                    return _Probe(ok=True, available=not taken, status_code=200)
                if resp.status == 429:
                    return _Probe(rate_limited=True, status_code=429)
                return _Probe(bad_proxy=True, status_code=resp.status)

    except (SocksConnError, asyncio.TimeoutError) as exc:
        log.debug("searchuser: socks5 probe (%s) proxy=%s", type(exc).__name__, proxy)
        return _Probe(bad_proxy=True)
    except Exception as exc:
        log.warning("searchuser: socks5 probe unexpected proxy=%s: %s", proxy, exc)
        return _Probe(bad_proxy=True)


async def _probe(
    session: aiohttp.ClientSession,
    username: str,
    proxy: str | None,
    timeout: float,
) -> _Probe:
    if proxy and proxy.startswith("socks5://"):
        return await _probe_socks5(username, proxy, timeout)
    return await _probe_http(session, username, proxy, timeout)


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


# ── Live embed builder ────────────────────────────────────────────────────────

def _build_progress_embed(
    length: int,
    attempts: int,
    max_attempts: int,
    last_name: str,
    available: list[str],
    pool_line: str,
) -> discord.Embed:
    """Embed shown during the search for live progress updates."""
    embed = discord.Embed(
        title="🔍  Username Search — In Progress",
        color=COL_LAVENDER,
    )
    embed.add_field(name="Length",   value=f"`{length}` chars",                    inline=True)
    embed.add_field(name="Checked",  value=f"`{attempts}` / `{max_attempts}`",     inline=True)
    embed.add_field(name="Found",    value=f"`{len(available)}` / `{TARGET_FOUND}`", inline=True)
    embed.add_field(name="Last checked", value=f"`{last_name}`",                   inline=False)
    embed.add_field(name="Proxy",    value=pool_line,                               inline=False)
    if available:
        embed.add_field(
            name="✅ Available so far",
            value="\n".join(f"• `{n}`" for n in available),
            inline=False,
        )
    embed.set_footer(text="Updating every ~25 checks — please wait...")
    return embed


def _build_result_embed(
    length: int,
    attempts: int,
    max_attempts: int,
    available: list[str],
    pool: "_ProxyPool",
    pool_line: str,
    stopped_rate_limited: bool,
    stopped_no_proxies: bool,
    max_consec: int,
) -> discord.Embed:
    """Final result embed."""
    embed = discord.Embed(
        title="Username Search Results",
        color=COL_LAVENDER,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Length Searched", value=f"`{length}` characters",                inline=True)
    embed.add_field(name="Valid Attempts",  value=f"`{attempts}` / `{max_attempts}`",      inline=True)
    embed.add_field(name="Proxies Tested",  value=f"`{pool.dead_count}` / `{pool.total}`", inline=True)
    embed.add_field(name="Proxy Mode",      value=pool_line,                               inline=False)

    if stopped_rate_limited:
        embed.add_field(
            name="⚠️  Rate Limited by Discord",
            value=(
                f"`{max_consec}` proxies in a row received `429`. "
                "Discord is actively rate-limiting. "
                "Wait a few minutes or use a premium/residential proxy via `PROXY_URL`."
            ),
            inline=False,
        )
    elif stopped_no_proxies:
        embed.add_field(
            name="⚠️  Proxy Pool Exhausted",
            value=(
                f"`{max_consec}` consecutive proxy failures. "
                "All fetched proxies are blocked or unreachable. "
                "Set a `PROXY_URL` secret with a reliable private proxy."
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
                f"Tested **{attempts}** names — none were available.\n"
                "Short usernames (`4`–`5` chars) are extremely competitive. "
                "Try a longer length or run the command again."
            ),
            inline=False,
        )

    embed.set_footer(
        text="Note: 3-4 char usernames are highly competitive. Set PROXY_URL for reliable results."
    )
    return embed


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
                description="🔍  Loading proxies — please wait...",
                color=COL_LAVENDER,
            )
        )

        # ── Determine proxy mode ───────────────────────────────────────────────
        env_proxy    = os.environ.get("PROXY_URL") or None
        private_mode = env_proxy is not None

        max_attempts = PRIVATE_MAX_ATTEMPTS  if private_mode else PUBLIC_MAX_ATTEMPTS
        req_timeout  = PRIVATE_REQUEST_TIMEOUT  if private_mode else PUBLIC_REQUEST_TIMEOUT
        max_consec   = PRIVATE_MAX_CONSEC_FAILS if private_mode else PUBLIC_MAX_CONSEC_FAILS

        available:            list[str] = []
        attempts:             int       = 0
        consecutive_fails:    int       = 0
        stopped_rate_limited: bool      = False
        stopped_no_proxies:   bool      = False
        last_checked:         str       = "—"

        fetched:  list[str] = []
        n_socks5 = n_https = n_http = 0

        # Live embed update state
        attempts_since_last_edit: int   = 0
        last_edit_time:           float = 0.0

        stop_event  = asyncio.Event()
        typing_task = asyncio.create_task(_typing_keepalive(ctx.channel, stop_event))

        try:
            async with aiohttp.ClientSession() as session:

                if private_mode:
                    pool = _ProxyPool(env_proxy, [])
                    print(f"[Search] Private mode — PROXY_URL set, skipping public pool")
                    log.info("searchuser: private mode — PROXY_URL set, skipping public pool")
                else:
                    fetched  = await _build_proxy_pool(session)
                    n_socks5 = sum(1 for p in fetched if p.startswith("socks5://"))
                    n_https  = sum(1 for p in fetched if p.startswith("https://"))
                    n_http   = len(fetched) - n_socks5 - n_https
                    pool     = _ProxyPool(None, fetched)
                    print(
                        f"[Search] Public pool: {len(fetched)} proxies "
                        f"({n_socks5} SOCKS5, {n_https} HTTPS, {n_http} HTTP)"
                    )

                # Pool description line (reused in both progress and result embeds)
                if private_mode:
                    pool_line = (
                        f"🔑 Private proxy (`PROXY_URL`) — "
                        f"timeout `{req_timeout}s`, cap `{max_attempts}` attempts"
                    )
                elif fetched:
                    pool_line = (
                        f"🟣 SOCKS5: `{n_socks5}`  "
                        f"🔒 HTTPS: `{n_https}`  "
                        f"🌐 HTTP: `{n_http}` — "
                        f"timeout `{req_timeout}s`"
                    )
                else:
                    pool_line = "none — direct connection only"

                # Show initial progress embed now that pool is ready
                try:
                    await msg.edit(embed=_build_progress_embed(
                        length, 0, max_attempts, "—", available, pool_line
                    ))
                    last_edit_time = time.monotonic()
                except discord.HTTPException:
                    pass

                while len(available) < TARGET_FOUND and attempts < max_attempts:
                    username = _gen_username(length)

                    # ── Inner loop: rotate proxies until we get a 200 OK ──────
                    while True:
                        proxy  = pool.next()
                        result = await _probe(session, username, proxy, req_timeout)

                        if result.ok:
                            attempts          += 1
                            consecutive_fails  = 0
                            last_checked       = username
                            attempts_since_last_edit += 1

                            # ── Console log ────────────────────────────────────
                            status_word = "Available ✅" if result.available else "Taken ❌"
                            print(
                                f"[Search] Checking '{username}' "
                                f"→ 200 {status_word} "
                                f"(attempt {attempts}/{max_attempts})"
                            )

                            if result.available:
                                available.append(username)

                            # ── Live embed update ──────────────────────────────
                            now = time.monotonic()
                            if (
                                attempts_since_last_edit >= EMBED_UPDATE_EVERY
                                and now - last_edit_time >= EMBED_UPDATE_MIN_INTERVAL
                            ):
                                try:
                                    await msg.edit(embed=_build_progress_embed(
                                        length, attempts, max_attempts,
                                        last_checked, available, pool_line,
                                    ))
                                    last_edit_time = now
                                    attempts_since_last_edit = 0
                                except discord.HTTPException:
                                    pass

                            break  # move to next username

                        if result.rate_limited:
                            pool.mark_dead(proxy)
                            consecutive_fails += 1
                            print(
                                f"[Search] '{username}' → 429 Rate Limited "
                                f"(consecutive failures: {consecutive_fails})"
                            )
                            if consecutive_fails >= max_consec:
                                stopped_rate_limited = True
                                break
                            continue  # retry same username with next proxy

                        # bad_proxy: 403 / 5xx / timeout / conn error
                        pool.mark_dead(proxy)
                        consecutive_fails += 1

                        if consecutive_fails >= max_consec:
                            stopped_no_proxies = True
                            break

                        if not pool.has_live:
                            # Pool dead — one last direct attempt
                            direct = await _probe(session, username, None, req_timeout)
                            if direct.ok:
                                attempts          += 1
                                consecutive_fails  = 0
                                last_checked       = username
                                attempts_since_last_edit += 1
                                status_word = "Available ✅" if direct.available else "Taken ❌"
                                print(
                                    f"[Search] Checking '{username}' "
                                    f"→ 200 {status_word} [direct] "
                                    f"(attempt {attempts}/{max_attempts})"
                                )
                                if direct.available:
                                    available.append(username)
                            else:
                                stopped_no_proxies = True
                            break

                    if stopped_rate_limited or stopped_no_proxies:
                        break

                    if attempts < max_attempts and len(available) < TARGET_FOUND:
                        await asyncio.sleep(INTER_ATTEMPT_GAP)

        finally:
            stop_event.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        # ── Final summary to console ───────────────────────────────────────────
        print(
            f"[Search] Done — {attempts} attempts, "
            f"{len(available)} available: {available}"
        )

        # ── Final result embed ─────────────────────────────────────────────────
        result_embed = _build_result_embed(
            length=length,
            attempts=attempts,
            max_attempts=max_attempts,
            available=available,
            pool=pool,
            pool_line=pool_line,
            stopped_rate_limited=stopped_rate_limited,
            stopped_no_proxies=stopped_no_proxies,
            max_consec=max_consec,
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
