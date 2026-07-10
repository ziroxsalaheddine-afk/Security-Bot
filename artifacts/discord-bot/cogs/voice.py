"""
Voice Cog — 24/7 VC System
════════════════════════════
+join   — join the invoker's voice channel (self-deafened, stays indefinitely)
+leave  — cleanly destroy the guild's voice connection

Wavelink-aware: if the active voice client is a wavelink.Player (music is
playing), both commands handle it correctly without crashing.
"""

import logging
import discord
import wavelink
from discord.ext import commands

log = logging.getLogger("guardian.voice")

COL = discord.Color(0x2B2D31)


class Voice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +join ──────────────────────────────────────────────────────────────────

    @commands.command(name="join")
    async def join(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(embed=discord.Embed(
                description="• __**Error**__\nYou must be in a voice channel first.",
                color=COL,
            ), delete_after=8)

        channel: discord.VoiceChannel = ctx.author.voice.channel
        vc = ctx.guild.voice_client

        if vc and vc.is_connected():
            if vc.channel.id == channel.id:
                return await ctx.send(embed=discord.Embed(
                    description=f"• __**Already Connected**__\nAlready in {channel.mention}.",
                    color=COL,
                ), delete_after=6)
            # Move — works for both regular VoiceClient and wavelink.Player
            await vc.move_to(channel)
        else:
            # If the music cog has a wavelink player connecting elsewhere, let it;
            # just connect a plain VoiceClient for the 24/7 use-case.
            vc = await channel.connect(self_deaf=True)

        log.info("Joined VC '%s' in guild '%s'", channel.name, ctx.guild.name)

        e = discord.Embed(
            description=f"• __**Voice Connected**__\nJoined {channel.mention} and self-deafened.",
            color=COL,
        )
        e.set_footer(text="© 2026 — developed by zrx.gg")
        await ctx.send(embed=e)

    # ── +leave ─────────────────────────────────────────────────────────────────

    @commands.command(name="leave")
    async def leave(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if not vc or not vc.is_connected():
            return await ctx.send(embed=discord.Embed(
                description="• __**Error**__\nNot connected to any voice channel.",
                color=COL,
            ), delete_after=8)

        channel_name = vc.channel.name

        # If the voice client is a wavelink Player, stop the music cleanly first
        if isinstance(vc, wavelink.Player):
            vc.queue.clear()
            try:
                await vc.stop()
            except Exception:
                pass

        await vc.disconnect(force=False)
        log.info("Left VC '%s' in guild '%s'", channel_name, ctx.guild.name)

        e = discord.Embed(
            description=f"• __**Disconnected**__\nLeft **{channel_name}**.",
            color=COL,
        )
        e.set_footer(text="© 2026 — developed by zrx.gg")
        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Voice(bot))
