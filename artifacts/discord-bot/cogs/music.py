"""
Music Cog — Lavalink v4 via wavelink 3.x
══════════════════════════════════════════
Node  : lavalinkv4.serenetia.com:443 (TLS)
Commands: +play  +pause  +volume  +autoplay

Permission gate
  • Bot Owner or Server Co-Owner  → always allowed
  • Everyone else                 → must have a role named "Music"

Now Playing embed: Noir / Dark Techwear (#000000)
"""

import logging
from datetime import datetime, timezone

import discord
import wavelink
from discord.ext import commands

from utils import db, coowners

log = logging.getLogger("guardian.music")

COL_NOIR = 0x000000          # Noir / Dark Techwear
FOOTER   = "© 2026 — developed by zrx.gg"


# ── Permission helper ──────────────────────────────────────────────────────────

def _has_music_perm(ctx: commands.Context) -> bool:
    """Owner / Co-Owner bypass; otherwise require a 'Music' role."""
    if db.is_owner(ctx.author.id):
        return True
    if ctx.guild and coowners.is_coowner(ctx.guild.id, ctx.author.id):
        return True
    if ctx.guild:
        return any(r.name == "Music" for r in getattr(ctx.author, "roles", []))
    return False


# ── Embed builders ─────────────────────────────────────────────────────────────

def _no_perm() -> discord.Embed:
    return (
        discord.Embed(
            description=(
                "• __**Access Denied**__\n"
                "You need the **Music** role to use music commands.\n"
                "Bot Owner and Co-Owner are automatically exempt."
            ),
            color=COL_NOIR,
        )
        .set_footer(text=FOOTER)
    )


def _err(text: str) -> discord.Embed:
    return discord.Embed(description=text, color=COL_NOIR).set_footer(text=FOOTER)


def _ms_to_ts(ms: int) -> str:
    """Milliseconds → HH:MM:SS or MM:SS."""
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _now_playing(
    track: wavelink.Playable,
    player: wavelink.Player,
    requester: discord.Member | discord.User,
) -> discord.Embed:
    """Noir / Dark Techwear 'Now Playing' embed."""
    e = discord.Embed(
        title="▶  NOW  PLAYING",
        description=f"**[{track.title}]({track.uri or ''})**\n`{track.author}`",
        color=COL_NOIR,
        timestamp=datetime.now(timezone.utc),
    )
    if track.artwork:
        e.set_thumbnail(url=track.artwork)

    dur  = _ms_to_ts(track.length) if track.length else "Live"
    mode = "ON" if player.autoplay == wavelink.AutoPlayMode.enabled else "OFF"

    e.add_field(name="◈  Duration",   value=f"`{dur}`",             inline=True)
    e.add_field(name="◈  Volume",     value=f"`{player.volume}%`",  inline=True)
    e.add_field(name="◈  Autoplay",   value=f"`{mode}`",            inline=True)
    e.add_field(name="◈  Requester",  value=requester.mention,      inline=True)
    e.set_footer(
        text=f"{FOOTER}  ·  {track.source.name if track.source else 'Unknown'}",
    )
    return e


# ── Cog ────────────────────────────────────────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id → text channel for Now Playing embeds
        self._bound: dict[int, discord.abc.Messageable] = {}
        # track.identifier → the member who queued it
        self._requesters: dict[str, discord.Member | discord.User] = {}

    # ── Wavelink events ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        log.info(
            "Lavalink node '%s' ready — session %s (resumed=%s)",
            payload.node.identifier,
            payload.session_id,
            payload.resumed,
        )

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        if not player or not player.guild:
            return
        ch = self._bound.get(player.guild.id)
        if not ch:
            return
        requester = self._requesters.get(payload.track.identifier, player.guild.me)
        try:
            await ch.send(embed=_now_playing(payload.track, player, requester))
        except Exception as exc:
            log.error("Now Playing embed failed: %s", exc)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: wavelink.Player = payload.player
        if not player:
            return
        # If queue is empty and autoplay is off, disconnect cleanly
        if player.autoplay == wavelink.AutoPlayMode.disabled and player.queue.is_empty:
            try:
                await player.disconnect()
                if player.guild:
                    self._bound.pop(player.guild.id, None)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player):
        """Disconnect players idle for the inactivity threshold."""
        try:
            await player.disconnect()
            if player.guild:
                self._bound.pop(player.guild.id, None)
        except Exception:
            pass

    # ── Internal: join / ensure player ───────────────────────────────────────

    async def _get_player(self, ctx: commands.Context) -> wavelink.Player | None:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(
                embed=_err("• __**Error**__\nYou must be in a voice channel first."),
                delete_after=8,
            )
            return None

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if vc is None:
            try:
                vc = await ctx.author.voice.channel.connect(
                    cls=wavelink.Player,
                    self_deaf=True,
                )
                vc.autoplay = wavelink.AutoPlayMode.disabled
            except Exception as exc:
                log.error("VC connect failed: %s", exc)
                await ctx.send(
                    embed=_err(f"• __**Error**__\nCould not join your channel:\n`{exc}`"),
                    delete_after=10,
                )
                return None
        elif vc.channel != ctx.author.voice.channel:
            await vc.move_to(ctx.author.voice.channel)

        self._bound[ctx.guild.id] = ctx.channel
        return vc

    # ── +play ─────────────────────────────────────────────────────────────────

    @commands.command(name="play")
    async def play(self, ctx: commands.Context, *, query: str):
        """Search and play a track or playlist via Lavalink."""
        if not _has_music_perm(ctx):
            return await ctx.send(embed=_no_perm(), delete_after=12)

        vc = await self._get_player(ctx)
        if not vc:
            return

        async with ctx.typing():
            try:
                results = await wavelink.Playable.search(query)
            except Exception as exc:
                return await ctx.send(
                    embed=_err(f"• __**Search Error**__\n`{exc}`"),
                    delete_after=10,
                )

        if not results:
            return await ctx.send(
                embed=_err("• __**No Results**__\nNo tracks found for that query."),
                delete_after=8,
            )

        if isinstance(results, wavelink.Playlist):
            for t in results.tracks:
                self._requesters[t.identifier] = ctx.author
                vc.queue.put(t)
            added = len(results.tracks)
            reply = (
                f"• __**Playlist Queued**__\n"
                f"`{results.name}` — `{added}` tracks added."
            )
        else:
            track = results[0]
            self._requesters[track.identifier] = ctx.author
            vc.queue.put(track)
            reply = (
                f"• __**Queued**__\n"
                f"`{track.title}` by `{track.author}`\n"
                f"Duration: `{_ms_to_ts(track.length)}`"
            )

        if not vc.playing:
            nxt = vc.queue.get()
            await vc.play(nxt)

        await ctx.send(
            embed=discord.Embed(description=reply, color=COL_NOIR).set_footer(text=FOOTER),
            delete_after=12,
        )

    # ── +pause ────────────────────────────────────────────────────────────────

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        """Toggle pause/resume."""
        if not _has_music_perm(ctx):
            return await ctx.send(embed=_no_perm(), delete_after=12)

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc or not vc.playing:
            return await ctx.send(
                embed=_err("• __**Error**__\nNothing is playing right now."),
                delete_after=8,
            )

        await vc.pause(not vc.paused)
        state = "Paused ⏸" if vc.paused else "Resumed ▶"
        await ctx.send(
            embed=discord.Embed(
                description=f"• __**{state}**__",
                color=COL_NOIR,
            ).set_footer(text=FOOTER),
            delete_after=8,
        )

    # ── +volume ───────────────────────────────────────────────────────────────

    @commands.command(name="volume")
    async def volume(self, ctx: commands.Context, vol: int):
        """Set playback volume (1–100)."""
        if not _has_music_perm(ctx):
            return await ctx.send(embed=_no_perm(), delete_after=12)

        if not 1 <= vol <= 100:
            return await ctx.send(
                embed=_err("• __**Invalid**__\nVolume must be between `1` and `100`."),
                delete_after=8,
            )

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc:
            return await ctx.send(
                embed=_err("• __**Error**__\nBot is not in a voice channel."),
                delete_after=8,
            )

        await vc.set_volume(vol)
        await ctx.send(
            embed=discord.Embed(
                description=f"• __**Volume**__\nSet to `{vol}%`",
                color=COL_NOIR,
            ).set_footer(text=FOOTER),
            delete_after=8,
        )

    # ── +autoplay ─────────────────────────────────────────────────────────────

    @commands.command(name="autoplay")
    async def autoplay_cmd(self, ctx: commands.Context):
        """Toggle Lavalink autoplay (recommended-track continuation)."""
        if not _has_music_perm(ctx):
            return await ctx.send(embed=_no_perm(), delete_after=12)

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc:
            return await ctx.send(
                embed=_err("• __**Error**__\nBot is not in a voice channel."),
                delete_after=8,
            )

        if vc.autoplay == wavelink.AutoPlayMode.enabled:
            vc.autoplay = wavelink.AutoPlayMode.disabled
            state = "OFF"
        else:
            vc.autoplay = wavelink.AutoPlayMode.enabled
            state = "ON"

        await ctx.send(
            embed=discord.Embed(
                description=f"• __**Autoplay**__\n`{state}`",
                color=COL_NOIR,
            ).set_footer(text=FOOTER),
            delete_after=8,
        )

    # ── Error handlers ────────────────────────────────────────────────────────

    @play.error
    async def _play_err(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=_err("• __**Usage**__\n`+play <song name or URL>`"),
                delete_after=8,
            )

    @volume.error
    async def _vol_err(self, ctx: commands.Context, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(
                embed=_err("• __**Usage**__\n`+volume <1-100>`"),
                delete_after=8,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
