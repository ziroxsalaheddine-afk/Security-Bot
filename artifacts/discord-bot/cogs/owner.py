"""
Owner Cog — Announce · Co-Owner System · Profile Management
════════════════════════════════════════════════════════════

Access tiers
  Global Owner  — anyone in guardian.db.json "owners" list (you set this via +owner add)
  Server Co-Owner — per-guild list in server_coowners.json (managed by Global Owner only)

Commands
  +announce <channel_id> <message>   Global Owner or Server Co-Owner
  +addcoowner @user                  Global Owner only
  +removecoowner @user               Global Owner only
  +setavatar <image_url>             Global Owner only
  +setbio <text>                     Global Owner only
  +setnick <nickname>                Global Owner or Server Co-Owner
"""

import logging
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

from utils import db
from utils import coowners

log = logging.getLogger("guardian.owner")

COL    = 0x2B2D31
FOOTER = "© 2026 — developed by zrx.gg"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ok(description: str, *, timestamp: bool = False) -> discord.Embed:
    e = discord.Embed(description=description, color=COL)
    e.set_footer(text=FOOTER)
    if timestamp:
        e.timestamp = datetime.now(timezone.utc)
    return e


def _has_elevated(ctx: commands.Context) -> bool:
    """True for Global Owner OR Server Co-Owner of this guild."""
    return db.is_owner(ctx.author.id) or coowners.is_coowner(ctx.guild.id, ctx.author.id)


# ── Cog ────────────────────────────────────────────────────────────────────────

class OwnerCog(commands.Cog, name="Owner"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +announce ──────────────────────────────────────────────────────────────

    @commands.command(name="announce")
    async def announce(self, ctx: commands.Context, channel_id: int, *, message: str):
        if not _has_elevated(ctx):
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                pass

        if channel is None:
            return await ctx.send(
                embed=_ok("• __**Error**__\nChannel not found. Double-check the ID."),
                delete_after=8,
            )

        avatar_url = self.bot.user.display_avatar.url if self.bot.user else None

        announcement = discord.Embed(
            description=message,
            color=COL,
            timestamp=datetime.now(timezone.utc),
        )
        announcement.set_author(name="<a:prize_draw:1497746457883578480> ⸻ Server Announcement")
        announcement.set_footer(text="Official Administration Notice")
        if avatar_url:
            announcement.set_thumbnail(url=avatar_url)

        try:
            await channel.send(embed=announcement)
        except discord.Forbidden:
            return await ctx.send(
                embed=_ok(f"• __**Error**__\nNo permission to send in <#{channel_id}>."),
                delete_after=8,
            )

        log.info(
            "Announcement sent to #%s (%s) by %s",
            getattr(channel, "name", channel_id), channel_id, ctx.author,
        )
        await ctx.send(
            embed=_ok(f"✅  Announcement delivered to <#{channel_id}>."),
            delete_after=5,
        )

    # ── Co-Owner management (Global Owner only) ────────────────────────────────

    @commands.command(name="addcoowner")
    async def addcoowner(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            return

        coowners.add_coowner(ctx.guild.id, user.id)
        log.info("Co-Owner added: %s in guild %s by %s", user, ctx.guild.id, ctx.author)

        await ctx.send(embed=_ok(
            f"• __**Co-Owner Added**__\n"
            f"{user.mention} (`{user.id}`) is now a Co-Owner in **{ctx.guild.name}**.\n\n"
            f"• __**Permissions**__\n"
            f"They may use `+announce` and `+setnick` in this server only."
        ))

    @commands.command(name="removecoowner")
    async def removecoowner(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            return

        coowners.remove_coowner(ctx.guild.id, user.id)
        log.info("Co-Owner removed: %s in guild %s by %s", user, ctx.guild.id, ctx.author)

        await ctx.send(embed=_ok(
            f"• __**Co-Owner Removed**__\n"
            f"{user.mention} (`{user.id}`) is no longer a Co-Owner in **{ctx.guild.name}**."
        ))

    @commands.command(name="coowners")
    async def list_coowners(self, ctx: commands.Context):
        if not db.is_owner(ctx.author.id):
            return

        co = coowners.get_coowners(ctx.guild.id)
        if not co:
            return await ctx.send(embed=_ok(
                f"• __**Co-Owners — {ctx.guild.name}**__\nNone assigned."
            ))

        lines = "\n".join(f"<@{uid}>  (`{uid}`)" for uid in co)
        await ctx.send(embed=_ok(
            f"• __**Co-Owners — {ctx.guild.name}**__\n{lines}"
        ))

    # ── Global Profile Management (Global Owner only) ──────────────────────────

    @commands.command(name="setavatar")
    async def setavatar(self, ctx: commands.Context, url: str):
        if not db.is_owner(ctx.author.id):
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return await ctx.send(
                            embed=_ok(f"• __**Error**__\nCould not fetch the image (`HTTP {resp.status}`). Check the URL."),
                            delete_after=8,
                        )
                    image_data = await resp.read()
        except Exception as exc:
            return await ctx.send(
                embed=_ok(f"• __**Error**__\nFailed to download image: `{exc}`"),
                delete_after=10,
            )

        try:
            await self.bot.user.edit(avatar=image_data)
            log.info("Bot avatar updated by %s", ctx.author)
            await ctx.send(embed=_ok(
                "• __**Avatar Updated**__\nBot avatar has been changed globally."
            ))
        except discord.HTTPException as exc:
            await ctx.send(
                embed=_ok(f"• __**Error**__\n`{exc}`\n\nDiscord rate-limits avatar changes to twice per hour."),
                delete_after=12,
            )

    @commands.command(name="setbio")
    async def setbio(self, ctx: commands.Context, *, text: str):
        if not db.is_owner(ctx.author.id):
            return

        try:
            await self.bot.user.edit(bio=text)
            log.info("Bot bio updated by %s", ctx.author)
            await ctx.send(embed=_ok(
                f"• __**Bio Updated**__\n{text}"
            ))
        except discord.HTTPException as exc:
            await ctx.send(
                embed=_ok(
                    f"• __**Error**__\n`{exc}`\n\n"
                    "Note: Discord restricts bio editing for verified bots. "
                    "This works for unverified/smaller bots only."
                ),
                delete_after=15,
            )

    # ── Server Profile Management (Global Owner or Co-Owner) ───────────────────

    @commands.command(name="setnick")
    async def setnick(self, ctx: commands.Context, *, nickname: str):
        if not _has_elevated(ctx):
            return

        try:
            await ctx.guild.me.edit(nick=nickname)
            log.info("Bot nick changed to '%s' in guild %s by %s", nickname, ctx.guild.id, ctx.author)
            await ctx.send(embed=_ok(
                f"• __**Nickname Set**__\nNow displaying as **{nickname}** in **{ctx.guild.name}**."
            ))
        except discord.Forbidden:
            await ctx.send(
                embed=_ok("• __**Error**__\nMissing `Change Nickname` permission in this server."),
                delete_after=8,
            )

    # ── +setprefix (Owner only) ────────────────────────────────────────────────

    @commands.command(name="setprefix")
    async def setprefix(self, ctx: commands.Context, prefix: str):
        if not db.is_owner(ctx.author.id):
            return
        if len(prefix) > 5:
            return await ctx.send(
                embed=_ok("• __**Error**__\nPrefix must be 5 characters or fewer."),
                delete_after=8,
            )
        db.set_prefix(prefix)
        log.info("Prefix changed to '%s' by %s", prefix, ctx.author)
        await ctx.send(embed=_ok(
            f"• __**Prefix Updated**__\nNew prefix: `{prefix}`\n\n"
            f"• __**Example**__\n`{prefix}help`  ·  `{prefix}play`  ·  `{prefix}antinuke`\n\n"
            f"*All commands now use this prefix.*"
        ))

    @setprefix.error
    async def _setprefix_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=_ok("• __**Usage**__\n`+setprefix <new_prefix>`"),
                delete_after=8,
            )

    # ── Error handlers ─────────────────────────────────────────────────────────

    @announce.error
    async def _announce_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=_ok("• __**Usage**__\n`+announce <channel_id> <message>`"),
                delete_after=8,
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.send(
                embed=_ok("• __**Error**__\nInvalid channel ID. Provide a numeric channel ID."),
                delete_after=8,
            )

    @addcoowner.error
    @removecoowner.error
    async def _coowner_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.UserNotFound):
            await ctx.send(
                embed=_ok("• __**Error**__\nUser not found. Mention them or provide their ID."),
                delete_after=8,
            )

    @setavatar.error
    async def _setavatar_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=_ok("• __**Usage**__\n`+setavatar <image_url>`"),
                delete_after=8,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCog(bot))
