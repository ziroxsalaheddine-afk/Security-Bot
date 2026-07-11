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

        # ── Safe owner fetch ───────────────────────────────────────────────────
        # guild.owner is None when not cached; fetch_member fails if they left.
        # Either way we fall back to a plain ID string so the command never dies.
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            owner_display = owner.mention
        except Exception:
            owner_display = f"`{guild.owner_id}`"

        # ── Safe ban count ─────────────────────────────────────────────────────
        # Requires Ban Members permission. Falls back gracefully on any error.
        try:
            ban_count = 0
            async for _ in guild.bans(limit=None):
                ban_count += 1
        except Exception:
            ban_count = "Missing Perms"

        # ── Channel breakdown ──────────────────────────────────────────────────
        text_ch  = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        cats     = len(guild.categories)
        threads  = len(guild.threads)
        total_ch = text_ch + voice_ch + len(getattr(guild, "stage_channels", []))

        # ── Misc ───────────────────────────────────────────────────────────────
        boost_count = guild.premium_subscription_count or 0
        boost_tier  = guild.premium_tier or 0
        vanity      = f"discord.gg/{guild.vanity_url_code}" if guild.vanity_url_code else "None"
        shard_num   = (guild.shard_id + 1) if guild.shard_id is not None else 1
        created_ts  = int(guild.created_at.timestamp())

        verif_map = {
            discord.VerificationLevel.none:    "None",
            discord.VerificationLevel.low:     "Low",
            discord.VerificationLevel.medium:  "Medium",
            discord.VerificationLevel.high:    "High",
            discord.VerificationLevel.highest: "Highest",
        }

        # ── Build embed ────────────────────────────────────────────────────────
        e = discord.Embed(
            title=guild.name,
            color=COL,
            timestamp=datetime.now(timezone.utc),
        )
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        # Banner takes priority over splash for the large image
        if guild.banner:
            e.set_image(url=guild.banner.url)
        elif guild.splash:
            e.set_image(url=guild.splash.url)

        e.add_field(name="Server ID", value=f"`{guild.id}`",                    inline=True)
        e.add_field(name="Owner",     value=owner_display,                     inline=True)
        e.add_field(name="Shard",     value=f"`#{shard_num}`",                 inline=True)

        e.add_field(name="Members", value=f"`{guild.member_count}`",           inline=True)
        e.add_field(
            name="Channels",
            value=(
                f"Text: `{text_ch}` · Voice: `{voice_ch}`\n"
                f"Categories: `{cats}` · Threads: `{threads}`\n"
                f"**Total:** `{total_ch}`"
            ),
            inline=True,
        )
        e.add_field(name="Region", value=f"`{guild.preferred_locale}`",        inline=True)

        e.add_field(name="Roles",    value=f"`{len(guild.roles)}`",            inline=True)
        e.add_field(name="Emojis",   value=f"`{len(guild.emojis)}`",           inline=True)
        e.add_field(name="Stickers", value=f"`{len(guild.stickers)}`",         inline=True)

        e.add_field(
            name="Verification Level",
            value=f"`{verif_map.get(guild.verification_level, str(guild.verification_level))}`",
            inline=True,
        )
        e.add_field(
            name="Boost Count",
            value=f"`{boost_count}` (Tier `{boost_tier}`)",
            inline=True,
        )
        e.add_field(name="Vanity URL",     value=f"`{vanity}`",                inline=True)
        e.add_field(name="Server Created", value=f"<t:{created_ts}:R>",        inline=True)
        e.add_field(name="Ban Count",      value=f"`{ban_count}`",             inline=True)

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
