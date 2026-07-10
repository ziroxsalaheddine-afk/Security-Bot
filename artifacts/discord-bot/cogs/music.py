"""
Music Cog — Lavalink v4 via wavelink 3.x
══════════════════════════════════════════════════════════════════════════════
Features
────────
• DJ whitelist permission gate  (Bot Owner / Co-Owner always bypass)
• +play  — URL or song-name search (YouTube Music fallback)
• +pause — toggle pause/resume
• +skip  — force-skip current track
• +stop  — clear queue + disconnect
• +volume <1-100>
• +autoplay — toggle recommended-track continuation

Now Playing embed
─────────────────
• Color     #2B2D31  (Discord dark-theme)
• Author    "🎶 Now Playing"
• Title     hyperlinked to track URI
• Thumbnail track artwork
• Fields    Uploader · Duration · Loop Mode · Autoplay · Requester

Interactive buttons (two rows, persistent)
──────────────────────────────────────────
Row 1: ⏯  ⏭  🔁  ✨  🔀
Row 2: ⏹  ✖

Queue progression
─────────────────
• AutoPlayMode.partial  = queue plays automatically, no auto-recommendations
• AutoPlayMode.enabled  = queue + Lavalink auto-recommendations ("+autoplay ON")
on_wavelink_track_end handles disconnect when queue exhausted & autoplay off.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import discord
import wavelink
from discord.ext import commands

from utils import db, coowners, dj_db

log = logging.getLogger("guardian.music")

COL_MAIN = 0x2B2D31   # Discord dark-theme grey
FOOTER   = "© 2026 — developed by zrx.gg"
URL_RE   = re.compile(r"^https?://", re.IGNORECASE)

# Seconds of idle before auto-disconnect when queue is exhausted
IDLE_TIMEOUT = 30


# ── Permission helpers ─────────────────────────────────────────────────────────

def _has_music_perm(author: discord.Member | discord.User,
                    guild:  discord.Guild | None) -> bool:
    """Bot Owner / Co-Owner bypass; otherwise must be in the DJ whitelist."""
    if db.is_owner(author.id):
        return True
    if guild and coowners.is_coowner(guild.id, author.id):
        return True
    return dj_db.is_dj(author.id)


def _dj_denied() -> discord.Embed:
    return discord.Embed(
        description="You are not in the DJ whitelist. Ask the owner to add you.",
        color=COL_MAIN,
    ).set_footer(text=FOOTER)


# ── Formatters ─────────────────────────────────────────────────────────────────

def _ms_to_ts(ms: int) -> str:
    """Milliseconds → MM:SS or HH:MM:SS."""
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _loop_label(mode: wavelink.QueueMode) -> str:
    return {
        wavelink.QueueMode.normal:   "Normal",
        wavelink.QueueMode.loop:     "Track 🔂",
        wavelink.QueueMode.loop_all: "Queue 🔁",
    }.get(mode, "Normal")


# ── Now Playing embed ──────────────────────────────────────────────────────────

def _now_playing_embed(
    track:     wavelink.Playable,
    player:    wavelink.Player,
    requester: discord.Member | discord.User,
) -> discord.Embed:
    dur      = _ms_to_ts(track.length) if track.length else "Live"
    ap_state = "🟢 On" if player.autoplay == wavelink.AutoPlayMode.enabled else "🔴 Off"
    loop_str = _loop_label(player.queue.mode)

    e = (
        discord.Embed(
            title=track.title,
            url=track.uri if track.uri else discord.utils.MISSING,
            color=COL_MAIN,
        )
        .set_author(name="🎶 Now Playing")
        .set_footer(text=FOOTER)
    )
    if track.artwork:
        e.set_thumbnail(url=track.artwork)

    e.add_field(name="Uploader",   value=track.author or "Unknown", inline=True)
    e.add_field(name="Duration",   value=f"`{dur}`",                inline=True)
    e.add_field(name="Loop Mode",  value=loop_str,                  inline=True)
    e.add_field(name="Autoplay",   value=ap_state,                  inline=True)
    e.add_field(name="Requester",  value=requester.mention,         inline=True)
    return e


def _err_embed(text: str) -> discord.Embed:
    return discord.Embed(description=text, color=COL_MAIN).set_footer(text=FOOTER)


def _ok_embed(text: str) -> discord.Embed:
    return discord.Embed(description=text, color=COL_MAIN).set_footer(text=FOOTER)


# ── Now Playing button view ────────────────────────────────────────────────────

class NowPlayingView(discord.ui.View):
    """
    Persistent two-row button panel attached to the Now Playing embed.
    timeout=None → stays active until the cog explicitly calls view.stop().
    """

    def __init__(self, player: wavelink.Player, cog: "Music"):
        super().__init__(timeout=None)
        self.player = player
        self.cog    = cog

    # ── Access gate ───────────────────────────────────────────────────────────

    def _can_use(self, interaction: discord.Interaction) -> bool:
        return _has_music_perm(interaction.user, interaction.guild)

    async def _deny(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "You are not in the DJ whitelist. Ask the owner to add you.",
            ephemeral=True,
        )

    def _player_ok(self) -> bool:
        return self.player is not None and self.player.connected

    # ── Row 1 ─────────────────────────────────────────────────────────────────

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary,   row=0)
    async def btn_pause(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        if not self._player_ok():
            return await itr.response.send_message("Player has disconnected.", ephemeral=True)
        await self.player.pause(not self.player.paused)
        state = "Paused ⏸" if self.player.paused else "Resumed ▶"
        await itr.response.send_message(state, ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_skip(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        if not self._player_ok():
            return await itr.response.send_message("Player has disconnected.", ephemeral=True)
        await self.player.skip(force=True)
        await itr.response.send_message("Skipped ⏭️", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def btn_loop(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        if not self._player_ok():
            return await itr.response.send_message("Player has disconnected.", ephemeral=True)
        # Cycle: Normal → Track → Queue → Normal
        modes = [
            wavelink.QueueMode.normal,
            wavelink.QueueMode.loop,
            wavelink.QueueMode.loop_all,
        ]
        idx = modes.index(self.player.queue.mode)
        self.player.queue.mode = modes[(idx + 1) % len(modes)]
        label = _loop_label(self.player.queue.mode)
        await itr.response.send_message(f"Loop mode: **{label}**", ephemeral=True)

    @discord.ui.button(emoji="✨", style=discord.ButtonStyle.secondary, row=0)
    async def btn_autoplay(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        if not self._player_ok():
            return await itr.response.send_message("Player has disconnected.", ephemeral=True)
        if self.player.autoplay == wavelink.AutoPlayMode.enabled:
            self.player.autoplay = wavelink.AutoPlayMode.partial
            state = "🔴 Off"
        else:
            self.player.autoplay = wavelink.AutoPlayMode.enabled
            state = "🟢 On"
        await itr.response.send_message(f"Autoplay: **{state}**", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def btn_shuffle(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        if not self._player_ok():
            return await itr.response.send_message("Player has disconnected.", ephemeral=True)
        self.player.queue.shuffle()
        await itr.response.send_message("Queue shuffled 🔀", ephemeral=True)

    # ── Row 2 ─────────────────────────────────────────────────────────────────

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger,    row=1)
    async def btn_stop(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        if not self._player_ok():
            return await itr.response.send_message("Player has disconnected.", ephemeral=True)
        p = self.player
        p.queue.clear()
        await p.stop()
        await p.disconnect()
        if p.guild:
            self.cog._bound.pop(p.guild.id, None)
            self.cog._np_messages.pop(p.guild.id, None)
        self.stop()
        await itr.response.send_message("Stopped and disconnected ⏹️", ephemeral=True)

    @discord.ui.button(emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_close(self, itr: discord.Interaction, _: discord.ui.Button):
        if not self._can_use(itr):
            return await self._deny(itr)
        self.stop()
        try:
            await itr.message.delete()
        except Exception:
            pass
        await itr.response.send_message("Embed closed.", ephemeral=True)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id → text channel to post NP embeds in
        self._bound:       dict[int, discord.abc.Messageable]       = {}
        # track.identifier → Member who requested the track
        self._requesters:  dict[str, discord.Member | discord.User] = {}
        # guild_id → the current Now Playing discord.Message
        self._np_messages: dict[int, discord.Message]               = {}

    # ── Wavelink events ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        log.info(
            "Lavalink node '%s' ready — session %s (resumed=%s)",
            payload.node.identifier, payload.session_id, payload.resumed,
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
        embed     = _now_playing_embed(payload.track, player, requester)
        view      = NowPlayingView(player, self)

        # Replace previous NP message — delete old one silently
        old = self._np_messages.pop(player.guild.id, None)
        if old:
            try:
                await old.delete()
            except Exception:
                pass

        try:
            msg = await ch.send(embed=embed, view=view)
            self._np_messages[player.guild.id] = msg
        except Exception as exc:
            log.error("Now Playing embed failed: %s", exc)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        """
        wavelink.AutoPlayMode.partial / .enabled handle queue progression
        automatically; we only need to handle the 'queue exhausted + autoplay off'
        idle-disconnect here.
        """
        player: wavelink.Player = payload.player
        if not player:
            return

        # If autoplay is enabled, Lavalink will fetch the next recommended track —
        # nothing to do.
        if player.autoplay == wavelink.AutoPlayMode.enabled:
            return

        # partial mode: wavelink plays from queue automatically.
        # When the queue is empty, the player goes idle — schedule a disconnect.
        if player.queue.is_empty:
            await asyncio.sleep(IDLE_TIMEOUT)
            # Re-check: a new track may have been queued in the meantime
            if not player.playing:
                try:
                    await player.disconnect()
                    if player.guild:
                        self._bound.pop(player.guild.id, None)
                        self._np_messages.pop(player.guild.id, None)
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player):
        """Fallback disconnect for players that go idle."""
        try:
            await player.disconnect()
            if player.guild:
                self._bound.pop(player.guild.id, None)
                self._np_messages.pop(player.guild.id, None)
        except Exception:
            pass

    # ── Internal: join / ensure player ───────────────────────────────────────

    async def _get_player(self, ctx: commands.Context) -> wavelink.Player | None:
        """
        Return the guild's wavelink.Player, joining the invoker's VC if needed.

        If a plain discord.VoiceClient is already connected (e.g. from +join),
        it is disconnected first so wavelink can take over with a proper Player.

        Returns None and sends an error embed if anything goes wrong.
        """
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(embed=_err_embed(
                "• __**Error**__\nYou must be in a voice channel to use music commands."
            ))
            return None

        raw_vc = ctx.voice_client

        # ── Type-guard: if a plain VoiceClient is connected, evict it ────────
        if raw_vc is not None and not isinstance(raw_vc, wavelink.Player):
            log.info(
                "Non-wavelink voice client found in %s — disconnecting before music connect.",
                ctx.guild,
            )
            try:
                await raw_vc.disconnect(force=True)
            except Exception:
                pass
            raw_vc = None

        vc: wavelink.Player | None = raw_vc  # type: ignore

        if vc is None:
            try:
                vc = await ctx.author.voice.channel.connect(
                    cls=wavelink.Player,
                    self_deaf=True,
                )
                # partial = queue auto-progresses; no auto-recommendations until +autoplay
                vc.autoplay = wavelink.AutoPlayMode.partial
            except Exception as exc:
                log.error("VC connect failed: %s", exc)
                await ctx.send(embed=_err_embed(
                    f"• __**Error**__\nCould not join your voice channel.\n`{exc}`"
                ))
                return None
        elif vc.channel != ctx.author.voice.channel:
            try:
                await vc.move_to(ctx.author.voice.channel)
            except Exception as exc:
                log.error("VC move failed: %s", exc)

        self._bound[ctx.guild.id] = ctx.channel
        return vc

    # ── +play ─────────────────────────────────────────────────────────────────

    @commands.command(name="play")
    async def play(self, ctx: commands.Context, *, query: str):
        """Search by name or URL and queue a track / playlist."""
        if not _has_music_perm(ctx.author, ctx.guild):
            return await ctx.send(embed=_dj_denied())

        vc = await self._get_player(ctx)
        if not vc:
            return

        async with ctx.typing():
            try:
                if URL_RE.match(query):
                    # Direct URL — let wavelink detect the source
                    results = await wavelink.Playable.search(query)
                else:
                    # Text search — default to YouTube Music
                    results = await wavelink.Playable.search(
                        query, source=wavelink.TrackSource.YouTubeMusic
                    )
            except Exception as exc:
                return await ctx.send(embed=_err_embed(
                    f"• __**Search Error**__\n`{exc}`"
                ))

        if not results:
            return await ctx.send(embed=_err_embed(
                "• __**No Results**__\nNo tracks found for that query.\n"
                "Try a different search term or paste a direct URL."
            ))

        if isinstance(results, wavelink.Playlist):
            for t in results.tracks:
                self._requesters[t.identifier] = ctx.author
            await vc.queue.put_wait(results)
            added = len(results.tracks)
            reply = (
                f"• __**Playlist Queued**__\n"
                f"**{results.name}** — `{added}` tracks added to the queue."
            )
        else:
            track = results[0]
            self._requesters[track.identifier] = ctx.author
            await vc.queue.put_wait(track)
            dur   = _ms_to_ts(track.length) if track.length else "Live"
            reply = (
                f"• __**Queued**__\n"
                f"**{track.title}** by `{track.author}`\n"
                f"Duration: `{dur}`"
            )

        # Begin playback immediately if the player is idle
        if not vc.playing:
            nxt = vc.queue.get()
            await vc.play(nxt)

        # ← No delete_after: queued-confirmation embeds stay permanently
        await ctx.send(embed=_ok_embed(reply))

    # ── +pause ────────────────────────────────────────────────────────────────

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        """Toggle pause/resume."""
        if not _has_music_perm(ctx.author, ctx.guild):
            return await ctx.send(embed=_dj_denied())

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc or not vc.playing:
            return await ctx.send(embed=_err_embed(
                "• __**Error**__\nNothing is currently playing."
            ))

        await vc.pause(not vc.paused)
        state = "Paused ⏸" if vc.paused else "Resumed ▶"
        await ctx.send(embed=_ok_embed(f"• __**{state}**__"))

    # ── +skip ─────────────────────────────────────────────────────────────────

    @commands.command(name="skip")
    async def skip(self, ctx: commands.Context):
        """Force-skip the current track."""
        if not _has_music_perm(ctx.author, ctx.guild):
            return await ctx.send(embed=_dj_denied())

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc or not vc.playing:
            return await ctx.send(embed=_err_embed(
                "• __**Error**__\nNothing is currently playing."
            ))

        await vc.skip(force=True)
        await ctx.send(embed=_ok_embed("• __**Skipped**__ ⏭️"))

    # ── +stop ─────────────────────────────────────────────────────────────────

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        """Clear the queue and disconnect."""
        if not _has_music_perm(ctx.author, ctx.guild):
            return await ctx.send(embed=_dj_denied())

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc:
            return await ctx.send(embed=_err_embed(
                "• __**Error**__\nBot is not in a voice channel."
            ))

        vc.queue.clear()
        await vc.stop()
        await vc.disconnect()
        self._bound.pop(ctx.guild.id, None)
        self._np_messages.pop(ctx.guild.id, None)
        await ctx.send(embed=_ok_embed(
            "• __**Stopped**__\nQueue cleared and disconnected."
        ))

    # ── +volume ───────────────────────────────────────────────────────────────

    @commands.command(name="volume")
    async def volume(self, ctx: commands.Context, vol: int):
        """Set playback volume (1–100)."""
        if not _has_music_perm(ctx.author, ctx.guild):
            return await ctx.send(embed=_dj_denied())

        if not 1 <= vol <= 100:
            return await ctx.send(embed=_err_embed(
                "• __**Invalid**__\nVolume must be between `1` and `100`."
            ))

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc:
            return await ctx.send(embed=_err_embed(
                "• __**Error**__\nBot is not in a voice channel."
            ))

        await vc.set_volume(vol)
        await ctx.send(embed=_ok_embed(f"• __**Volume**__\nSet to `{vol}%`"))

    # ── +autoplay ─────────────────────────────────────────────────────────────

    @commands.command(name="autoplay")
    async def autoplay_cmd(self, ctx: commands.Context):
        """Toggle Lavalink recommended-track autoplay."""
        if not _has_music_perm(ctx.author, ctx.guild):
            return await ctx.send(embed=_dj_denied())

        vc: wavelink.Player | None = ctx.voice_client  # type: ignore
        if not vc:
            return await ctx.send(embed=_err_embed(
                "• __**Error**__\nBot is not in a voice channel."
            ))

        if vc.autoplay == wavelink.AutoPlayMode.enabled:
            vc.autoplay = wavelink.AutoPlayMode.partial
            state = "🔴 Off"
        else:
            vc.autoplay = wavelink.AutoPlayMode.enabled
            state = "🟢 On"

        await ctx.send(embed=_ok_embed(f"• __**Autoplay**__\n{state}"))

    # ── Error handlers ────────────────────────────────────────────────────────

    @play.error
    async def _play_err(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_err_embed(
                "• __**Usage**__\n`+play <song name or URL>`"
            ))

    @volume.error
    async def _vol_err(self, ctx: commands.Context, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(embed=_err_embed(
                "• __**Usage**__\n`+volume <1-100>`"
            ))


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
