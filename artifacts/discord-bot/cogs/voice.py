"""
Voice Cog — 24/7 VC System
════════════════════════════
+join   — join the invoker's voice channel (self-deafened, stays indefinitely)
+leave  — cleanly destroy the guild's voice connection

Key design decisions
────────────────────
• +join always connects as a wavelink.Player (not a plain VoiceClient) so the
  music cog's _get_player() never has to evict a conflicting client.
• wavelink.Player does NOT have .is_connected() — it has .connected (property).
  A _is_vc_connected() helper normalises both types to avoid AttributeError.
• When +leave is called while music is playing, the queue is cleared and the
  player stopped before disconnecting so wavelink state stays clean.
"""

import logging
import discord
import wavelink
from discord.ext import commands

log = logging.getLogger("guardian.voice")

COL    = discord.Color(0x2B2D31)
FOOTER = "© 2026 — developed by zrx.gg"


def _is_vc_connected(vc) -> bool:
    """
    Return True if the voice client is active.
    discord.VoiceClient  → vc.is_connected()
    wavelink.Player      → vc.connected  (property; no parentheses)
    """
    if isinstance(vc, wavelink.Player):
        return vc.connected
    if vc is not None and hasattr(vc, "is_connected"):
        return vc.is_connected()
    return False


def _embed(desc: str) -> discord.Embed:
    return discord.Embed(description=desc, color=COL).set_footer(text=FOOTER)


class Voice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +join ──────────────────────────────────────────────────────────────────

    @commands.command(name="join")
    async def join(self, ctx: commands.Context):
        """
        Join the invoker's voice channel and stay indefinitely (24/7 mode).

        Connects as a wavelink.Player so that a subsequent +play does not need
        to evict a plain VoiceClient first.  autoplay is left at 'partial'
        (queue-only) so the player sits silently until music is queued.
        """
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(
                embed=_embed("• __**Error**__\nYou must be in a voice channel first."),
                delete_after=8,
            )

        target: discord.VoiceChannel = ctx.author.voice.channel
        vc = ctx.guild.voice_client

        if vc is not None and _is_vc_connected(vc):
            if vc.channel.id == target.id:
                return await ctx.send(
                    embed=_embed(
                        f"• __**Already Connected**__\nAlready in {target.mention}."
                    ),
                    delete_after=6,
                )
            # Already connected elsewhere — move channels
            try:
                await vc.move_to(target)
            except Exception as exc:
                log.error("VC move failed: %s", exc)
                return await ctx.send(
                    embed=_embed(f"• __**Error**__\nCould not move channels:\n`{exc}`"),
                    delete_after=8,
                )
        else:
            # Connect as wavelink.Player (keeps compatibility with music cog)
            try:
                vc = await target.connect(cls=wavelink.Player, self_deaf=True)
                # Silence: queue-only mode, no auto-recommendations
                vc.autoplay = wavelink.AutoPlayMode.partial
            except Exception as exc:
                log.error("VC connect failed: %s", exc)
                return await ctx.send(
                    embed=_embed(
                        f"• __**Error**__\nCould not join {target.mention}:\n`{exc}`"
                    ),
                    delete_after=8,
                )

        # Mark this player as 24/7 — prevents music cog idle-disconnect handlers
        # from kicking us out when no music is playing.
        vc._stay = True
        log.info("Joined VC '%s' in guild '%s' (24/7 mode)", target.name, ctx.guild.name)
        await ctx.send(
            embed=_embed(
                f"• __**Voice Connected**__\n"
                f"Joined {target.mention} and self-deafened.\n"
                f"Use `+play` to start music or `+leave` to disconnect."
            )
        )

    # ── +leave ─────────────────────────────────────────────────────────────────

    @commands.command(name="leave")
    async def leave(self, ctx: commands.Context):
        """
        Disconnect from the current voice channel.
        If music is playing, the queue is cleared and the track stopped first.
        """
        vc = ctx.guild.voice_client

        if not vc or not _is_vc_connected(vc):
            return await ctx.send(
                embed=_embed("• __**Error**__\nNot connected to any voice channel."),
                delete_after=8,
            )

        channel_name = vc.channel.name

        if isinstance(vc, wavelink.Player):
            # Stop music cleanly before disconnecting
            vc.queue.clear()
            try:
                await vc.stop()
            except Exception:
                pass
            try:
                await vc.disconnect()
            except Exception as exc:
                log.error("Wavelink disconnect error: %s", exc)
        else:
            await vc.disconnect(force=False)

        log.info("Left VC '%s' in guild '%s'", channel_name, ctx.guild.name)
        await ctx.send(
            embed=_embed(f"• __**Disconnected**__\nLeft **{channel_name}**.")
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Voice(bot))
