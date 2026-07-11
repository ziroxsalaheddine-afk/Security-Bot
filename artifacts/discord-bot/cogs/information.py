"""
Information Cog — public commands, no whitelist required.
  +serverinfo / +si  — full guild snapshot
  +roleinfo   / +ri  — role details
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

log = logging.getLogger("guardian.information")

COL    = 0x2B2D31
FOOTER = "Guardian Security System"


class Information(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +serverinfo / +si ──────────────────────────────────────────────────────

    @commands.command(name="serverinfo", aliases=["si"])
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild

        # Fetch owner (may not be cached)
        owner = guild.owner or await guild.fetch_member(guild.owner_id)

        # Channel breakdown
        text_ch  = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        cats     = len(guild.categories)
        threads  = len(guild.threads)
        total_ch = text_ch + voice_ch + len(getattr(guild, "stage_channels", []))

        # Ban count — requires Ban Members permission on the bot
        try:
            ban_count = sum(1 async for _ in guild.bans(limit=None))
        except (discord.Forbidden, discord.HTTPException):
            ban_count = "N/A"

        # Boost info
        boost_count = guild.premium_subscription_count or 0
        boost_tier  = guild.premium_tier or 0

        # Vanity URL
        vanity = f"discord.gg/{guild.vanity_url_code}" if guild.vanity_url_code else "None"

        verif_map = {
            discord.VerificationLevel.none:    "None",
            discord.VerificationLevel.low:     "Low",
            discord.VerificationLevel.medium:  "Medium",
            discord.VerificationLevel.high:    "High",
            discord.VerificationLevel.highest: "Highest",
        }

        created_ts = int(guild.created_at.timestamp())

        e = discord.Embed(
            title=guild.name,
            color=COL,
            timestamp=datetime.now(timezone.utc),
        )
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        banner = guild.banner or guild.splash
        if banner:
            e.set_image(url=banner.url)

        e.add_field(name="Server ID",  value=f"`{guild.id}`", inline=True)
        e.add_field(
            name="Owner",
            value=f"<a:Red_Crown:1497198533621715155> {owner.mention if owner else '`Unknown`'}",
            inline=True,
        )
        e.add_field(
            name="Shard",
            value=f"`#{guild.shard_id + 1 if guild.shard_id is not None else 1}`",
            inline=True,
        )

        e.add_field(name="Members", value=f"👥 `{guild.member_count}`", inline=True)
        e.add_field(
            name="Channels",
            value=(
                f"📑 Text: `{text_ch}` · 🔊 Voice: `{voice_ch}`\n"
                f"📂 Categories: `{cats}` · 🧵 Threads: `{threads}`\n"
                f"**Total:** `{total_ch}`"
            ),
            inline=True,
        )
        e.add_field(name="Region", value=f"📡 `{guild.preferred_locale}`", inline=True)

        e.add_field(name="Roles",    value=f"<a:11pm_cc_1:1500648629159985283> `{len(guild.roles)}`",       inline=True)
        e.add_field(name="Emojis",   value=f"<:emoji_149:1497747690514288690> `{len(guild.emojis)}`",       inline=True)
        e.add_field(name="Stickers", value=f"<a:star11:1401192456938324123> `{len(guild.stickers)}`",       inline=True)

        e.add_field(
            name="Verification Level",
            value=f"`{verif_map.get(guild.verification_level, str(guild.verification_level))}`",
            inline=True,
        )
        e.add_field(
            name="Boost Count",
            value=f"<a:Nitro_boosting_level:1500645983116070952> `{boost_count}` (Tier `{boost_tier}`)",
            inline=True,
        )
        e.add_field(name="Vanity URL",     value=f"<:linksnakes:1481401437949919253> `{vanity}`",            inline=True)
        e.add_field(name="Server Created", value=f"<t:{created_ts}:R>",                                     inline=True)
        e.add_field(name="Ban Count",      value=f"<a:11pm_banned:1039486029159207003> `{ban_count}`",       inline=True)

        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── +roleinfo / +ri ────────────────────────────────────────────────────────

    @commands.command(name="roleinfo", aliases=["ri"])
    async def roleinfo(self, ctx: commands.Context, *, role: str):
        # Resolve: raw ID → guild cache → full RoleConverter (handles mentions + names)
        resolved: Optional[discord.Role] = None
        raw = role.strip().strip("<@&>")
        if raw.isdigit():
            resolved = ctx.guild.get_role(int(raw))
        if resolved is None:
            try:
                resolved = await commands.RoleConverter().convert(ctx, role)
            except commands.RoleNotFound:
                pass

        if resolved is None:
            e = discord.Embed(
                description="• __**Error**__\nNo role found matching that mention or ID.",
                color=0xC0392B,
            )
            return await ctx.send(embed=e, delete_after=10)

        r          = resolved
        created_ts = int(r.created_at.timestamp())

        if r.permissions.administrator:
            perms = "All Permissions"
        else:
            enabled = [p.replace("_", " ").title() for p, v in r.permissions if v]
            if not enabled:
                perms = "None"
            elif len(", ".join(enabled)) > 200:
                perms = f"{len(enabled)} permissions granted"
            else:
                perms = ", ".join(enabled)

        e = discord.Embed(
            color=r.color if r.color.value else COL,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_author(
            name="Role Information",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )

        e.add_field(name="Name",        value=f"```> {r.name}```",                         inline=False)
        e.add_field(name="ID",          value=f"```{r.id}```",                              inline=True)
        e.add_field(name="Position",    value=f"```{r.position}```",                        inline=True)
        e.add_field(name="Color",       value=f"```{str(r.color).upper()}```",              inline=True)
        e.add_field(name="Members",     value=f"```{len(r.members)}```",                    inline=True)
        e.add_field(name="Mentionable", value=f"```{'Yes' if r.mentionable else 'No'}```",  inline=True)
        e.add_field(name="Hoisted",     value=f"```{'Yes' if r.hoist else 'No'}```",        inline=True)
        e.add_field(name="Managed",     value=f"```{'Yes' if r.managed else 'No'}```",      inline=True)
        e.add_field(name="Permissions", value=f"```{perms}```",                             inline=False)
        e.add_field(name="Created At",  value=f"<t:{created_ts}:F>",                        inline=False)

        e.set_footer(
            text=FOOTER,
            icon_url=self.bot.user.display_avatar.url if self.bot.user else None,
        )
        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Information(bot))
