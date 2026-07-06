"""
Voice Cog — 24/7 VC System
════════════════════════════
+join   — join the invoker's voice channel (self-deafened, stays indefinitely)
+leave  — cleanly destroy the guild's voice connection
"""

import logging
import discord
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
            e = discord.Embed(
                description="• __**Error**__\nYou must be in a voice channel first.",
                color=COL,
            )
            await ctx.send(embed=e, delete_after=8)
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel

        vc = ctx.guild.voice_client

        if vc and vc.is_connected():
            if vc.channel.id == channel.id:
                e = discord.Embed(
                    description=f"• __**Already Connected**__\nAlready in {channel.mention}.",
                    color=COL,
                )
                await ctx.send(embed=e, delete_after=6)
                return
            await vc.move_to(channel)
        else:
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
            e = discord.Embed(
                description="• __**Error**__\nNot connected to any voice channel.",
                color=COL,
            )
            await ctx.send(embed=e, delete_after=8)
            return

        channel_name = vc.channel.name
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
