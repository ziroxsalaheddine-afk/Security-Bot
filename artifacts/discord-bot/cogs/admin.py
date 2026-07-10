"""
Admin Cog — whitelist/owner management, setlog, scaninvites, config commands.
All commands silently ignore non-whitelisted users.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from utils import db, logs

log = logging.getLogger("guardian.admin")

COL    = 0x2B2D31
FOOTER = "Guardian Security System"


def _embed(title: str, description: str) -> discord.Embed:
    e = discord.Embed(
        title=title,
        description=description,
        color=COL,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=FOOTER)
    return e


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Whitelist ──────────────────────────────────────────────────────────────

    @commands.group(name="whitelist", invoke_without_command=True)
    async def whitelist(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        await ctx.send(embed=_embed("Whitelist", "Use `+whitelist add/remove/list`."))

    @whitelist.command(name="add")
    async def wl_add(self, ctx: commands.Context, user: discord.User):
        if not db.is_whitelisted(ctx.author.id):
            return
        db.add_whitelist(user.id)
        await ctx.send(embed=_embed(
            "Whitelist Updated",
            f"• __**User Added**__\n{user.mention} has been whitelisted."
        ))
        await logs.send(
            self.bot, ctx.guild, "✅  Whitelist — User Added",
            f"• __**User**__\n{user.mention}\n\n• __**Added By**__\n{ctx.author.mention}",
            user=user, color=logs.COL_SUCCESS,
        )

    @whitelist.command(name="remove")
    async def wl_remove(self, ctx: commands.Context, user: discord.User):
        if not db.is_whitelisted(ctx.author.id):
            return
        db.remove_whitelist(user.id)
        await ctx.send(embed=_embed(
            "Whitelist Updated",
            f"• __**User Removed**__\n{user.mention} has been removed from the whitelist."
        ))
        await logs.send(
            self.bot, ctx.guild, "🚫  Whitelist — User Removed",
            f"• __**User**__\n{user.mention}\n\n• __**Removed By**__\n{ctx.author.mention}",
            user=user, color=logs.COL_DANGER,
        )

    @whitelist.command(name="list")
    async def wl_list(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        wl = db.get_whitelist()
        if not wl:
            return await ctx.send(embed=_embed("Whitelist", "No users are currently whitelisted."))
        lines = "\n".join(f"<@{uid}>  (`{uid}`)" for uid in wl)
        await ctx.send(embed=_embed(f"Whitelist — {len(wl)} user(s)", f"• __**Users**__\n{lines}"))

    # ── Owner ──────────────────────────────────────────────────────────────────

    @commands.group(name="owner", invoke_without_command=True)
    async def owner(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        await ctx.send(embed=_embed("Owner", "Use `+owner add/remove/list`."))

    @owner.command(name="add")
    async def owner_add(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            # Raise instead of sending our own message + returning: a plain
            # `return` makes the command "complete" successfully as far as
            # on_command_completion is concerned, which would wrongly trigger
            # the auto-react ✅ on a denied action. Raising CheckFailure lets
            # the global on_command_error show the one standard denial embed.
            raise commands.CheckFailure("Only owners can add other owners.")
        db.add_owner(user.id)
        await ctx.send(embed=_embed("Owner Added", f"• __**New Owner**__\n{user.mention} is now an owner."))
        await logs.send(
            self.bot, ctx.guild, "✅  Owner Added",
            f"• __**User**__\n{user.mention}\n\n• __**Added By**__\n{ctx.author.mention}",
            user=user, color=logs.COL_SUCCESS,
        )

    @owner.command(name="remove")
    async def owner_remove(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            raise commands.CheckFailure("Only owners can remove owners.")
        db.remove_owner(user.id)
        await ctx.send(embed=_embed("Owner Removed", f"• __**Removed**__\n{user.mention} is no longer an owner."))
        await logs.send(
            self.bot, ctx.guild, "🚫  Owner Removed",
            f"• __**User**__\n{user.mention}\n\n• __**Removed By**__\n{ctx.author.mention}",
            user=user, color=logs.COL_DANGER,
        )

    @owner.command(name="list")
    async def owner_list(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        owners = db.get_owners()
        if not owners:
            return await ctx.send(embed=_embed("Owners", "No owners configured."))
        lines = "\n".join(f"<@{uid}>  (`{uid}`)" for uid in owners)
        await ctx.send(embed=_embed(f"Owners — {len(owners)}", f"• __**Users**__\n{lines}"))

    # ── Set log channel ────────────────────────────────────────────────────────

    @commands.command(name="setlog")
    async def setlog(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        if not db.is_whitelisted(ctx.author.id):
            return
        if channel is None:
            db.set_log_channel(None)
            return await ctx.send(embed=_embed("Log Disabled", "• __**Status**__\nSecurity logs have been turned off."))
        db.set_log_channel(channel.id)
        await ctx.send(embed=_embed(
            "Log Channel Set",
            f"• __**Channel**__\n{channel.mention}\nSecurity events will be sent here."
        ))

    # ── Scan invites ───────────────────────────────────────────────────────────

    @commands.command(name="scaninvites")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def scaninvites(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        try:
            invites = await ctx.guild.invites()
        except discord.Forbidden:
            return await ctx.send(embed=_embed(
                "Permission Error",
                "• __**Error**__\nMissing `Manage Guild` permission."
            ))

        suspicious = []
        for inv in invites:
            flags = []
            if inv.max_uses == 0:
                flags.append("Unlimited uses")
            if inv.max_age == 0:
                flags.append("Never expires")
            if flags:
                suspicious.append((inv, flags))

        if not suspicious:
            return await ctx.send(embed=_embed(
                "Scan Complete",
                f"• __**Result**__\nNo suspicious invites found out of `{len(invites)}` total."
            ))

        lines = ""
        for inv, flags in suspicious[:15]:
            inviter = inv.inviter.mention if inv.inviter else "`Unknown`"
            ch_name = inv.channel.name if inv.channel else "?"
            lines += f"• __**{inv.code}**__\n{inviter} · `#{ch_name}` · " + " · ".join(f"`{f}`" for f in flags) + "\n\n"

        lines += (
            f"• __**Total Invites**__\n`{len(invites)}`\n\n"
            f"• __**Flagged**__\n`{len(suspicious)}`"
        )
        await ctx.send(embed=_embed(f"{len(suspicious)} Suspicious Invite(s)", lines))

    # ── Anti-nuke config ───────────────────────────────────────────────────────

    @commands.group(name="antinuke", invoke_without_command=True)
    async def antinuke_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        cfg = db.get_config().get("antinuke", {})
        await ctx.send(embed=_embed(
            "Anti-Nuke Config",
            f"• __**Status**__\n`{'ON' if cfg.get('enabled', True) else 'OFF'}`\n\n"
            f"• __**Threshold**__\n`{cfg.get('threshold', 3)}` actions\n\n"
            f"• __**Interval**__\n`{cfg.get('interval', 10)}s`\n\n"
            f"• __**Action**__\n`{cfg.get('action', 'ban')}`"
        ))

    @antinuke_cmd.command(name="on")
    async def an_on(self, ctx):
        if not db.is_whitelisted(ctx.author.id): return
        db.set_config(["antinuke", "enabled"], True)
        await ctx.send(embed=_embed("Anti-Nuke", "• __**Status**__\n`ENABLED`"))

    @antinuke_cmd.command(name="off")
    async def an_off(self, ctx):
        if not db.is_whitelisted(ctx.author.id): return
        db.set_config(["antinuke", "enabled"], False)
        await ctx.send(embed=_embed("Anti-Nuke", "• __**Status**__\n`DISABLED`"))

    @antinuke_cmd.command(name="threshold")
    async def an_threshold(self, ctx, n: int):
        if not db.is_whitelisted(ctx.author.id): return
        if n < 1:
            return await ctx.send(embed=_embed("Invalid", "• __**Error**__\nMust be ≥ 1."))
        db.set_config(["antinuke", "threshold"], n)
        await ctx.send(embed=_embed("Threshold Updated", f"• __**New Value**__\n`{n}` actions"))

    @antinuke_cmd.command(name="interval")
    async def an_interval(self, ctx, seconds: int):
        if not db.is_whitelisted(ctx.author.id): return
        if seconds < 1:
            return await ctx.send(embed=_embed("Invalid", "• __**Error**__\nMust be ≥ 1 second."))
        db.set_config(["antinuke", "interval"], seconds)
        await ctx.send(embed=_embed("Interval Updated", f"• __**New Value**__\n`{seconds}s`"))

    @antinuke_cmd.command(name="action")
    async def an_action(self, ctx, action: str):
        if not db.is_whitelisted(ctx.author.id): return
        action = action.lower()
        if action not in ("ban", "kick", "quarantine"):
            return await ctx.send(embed=_embed("Invalid", "• __**Error**__\nUse: `ban`, `kick`, or `quarantine`."))
        db.set_config(["antinuke", "action"], action)
        await ctx.send(embed=_embed("Action Updated", f"• __**Punishment**__\n`{action}`"))

    # ── AutoMod config ─────────────────────────────────────────────────────────

    @commands.group(name="automod", invoke_without_command=True)
    async def automod_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id): return
        cfg = db.get_config().get("automod", {})
        await ctx.send(embed=_embed(
            "AutoMod Config",
            f"• __**Anti-Link**__\n`{'ON' if cfg.get('antiLink', {}).get('enabled') else 'OFF'}`\n\n"
            f"• __**Anti-Spam**__\n`{'ON' if cfg.get('antiSpam', {}).get('enabled') else 'OFF'}`\n\n"
            f"• __**Anti-Raid**__\n`{'ON' if cfg.get('antiRaid', {}).get('enabled') else 'OFF'}`"
        ))

    @automod_cmd.command(name="antilink")
    async def am_antilink(self, ctx, toggle: str):
        if not db.is_whitelisted(ctx.author.id): return
        val = toggle.lower() in ("on", "true", "1", "yes", "enable")
        db.set_config(["automod", "antiLink", "enabled"], val)
        await ctx.send(embed=_embed("Anti-Link", f"• __**Status**__\n`{'ENABLED' if val else 'DISABLED'}`"))

    @automod_cmd.command(name="antispam")
    async def am_antispam(self, ctx, toggle: str):
        if not db.is_whitelisted(ctx.author.id): return
        val = toggle.lower() in ("on", "true", "1", "yes", "enable")
        db.set_config(["automod", "antiSpam", "enabled"], val)
        await ctx.send(embed=_embed("Anti-Spam", f"• __**Status**__\n`{'ENABLED' if val else 'DISABLED'}`"))

    @automod_cmd.command(name="antiraid")
    async def am_antiraid(self, ctx, toggle: str):
        if not db.is_whitelisted(ctx.author.id): return
        val = toggle.lower() in ("on", "true", "1", "yes", "enable")
        db.set_config(["automod", "antiRaid", "enabled"], val)
        await ctx.send(embed=_embed("Anti-Raid", f"• __**Status**__\n`{'ENABLED' if val else 'DISABLED'}`"))

    # ── User info ──────────────────────────────────────────────────────────────

    @commands.command(name="userinfo")
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if not db.is_whitelisted(ctx.author.id):
            return
        member = member or ctx.author
        age    = (discord.utils.utcnow() - member.created_at).days
        joined = (discord.utils.utcnow() - member.joined_at).days if member.joined_at else "?"
        wl     = "`Yes`" if db.is_whitelisted(member.id) else "`No`"

        e = discord.Embed(
            title=f"User Info — {member}",
            description=(
                f"• __**ID**__\n`{member.id}`\n\n"
                f"• __**Account Age**__\n`{age}d`\n\n"
                f"• __**In Server**__\n`{joined}d`\n\n"
                f"• __**Top Role**__\n{member.top_role.mention}\n\n"
                f"• __**Role Count**__\n`{len(member.roles) - 1}`\n\n"
                f"• __**Whitelisted**__\n{wl}"
            ),
            color=COL,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── Server info ────────────────────────────────────────────────────────────

    @commands.command(name="serverinfo", aliases=["si"])
    async def serverinfo(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        guild = ctx.guild

        # Owner / member counts
        owner = guild.owner or await guild.fetch_member(guild.owner_id)

        # Channel breakdown
        text_ch  = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        cats     = len(guild.categories)
        threads  = len(guild.threads)
        total_ch = text_ch + voice_ch + len(getattr(guild, "stage_channels", []))

        # Bans (requires Ban Members permission)
        try:
            ban_count = sum(1 async for _ in guild.bans(limit=None))
        except (discord.Forbidden, discord.HTTPException):
            ban_count = "N/A"

        # Boosts
        boost_count = guild.premium_subscription_count or 0
        boost_tier  = guild.premium_tier or 0

        # Vanity
        vanity = "None"
        if guild.vanity_url_code:
            vanity = f"discord.gg/{guild.vanity_url_code}"

        verif_map = {
            discord.VerificationLevel.none:   "None",
            discord.VerificationLevel.low:    "Low",
            discord.VerificationLevel.medium: "Medium",
            discord.VerificationLevel.high:   "High",
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

        e.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        e.add_field(
            name="Owner",
            value=f"<a:Red_Crown:1497198533621715155> {owner.mention if owner else '`Unknown`'}",
            inline=True,
        )
        e.add_field(name="Shard", value=f"`#{guild.shard_id + 1 if guild.shard_id is not None else 1}`", inline=True)

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

        e.add_field(name="Roles", value=f"<a:11pm_cc_1:1500648629159985283> `{len(guild.roles)}`", inline=True)
        e.add_field(name="Emojis", value=f"<:emoji_149:1497747690514288690> `{len(guild.emojis)}`", inline=True)
        e.add_field(name="Stickers", value=f"<a:star11:1401192456938324123> `{len(guild.stickers)}`", inline=True)

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
        e.add_field(name="Vanity URL", value=f"<:linksnakes:1481401437949919253> `{vanity}`", inline=True)

        e.add_field(name="Server Created", value=f"<t:{created_ts}:R>", inline=True)
        e.add_field(name="Ban Count", value=f"<a:11pm_banned:1039486029159207003> `{ban_count}`", inline=True)

        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── Role info ──────────────────────────────────────────────────────────────

    @commands.command(name="roleinfo", aliases=["ri"])
    async def roleinfo(self, ctx: commands.Context, *, role: str):
        if not db.is_whitelisted(ctx.author.id):
            return

        # Accept a mention, a raw ID, or (as a fallback) a role converter lookup.
        resolved: Optional[discord.Role] = None
        raw = role.strip().strip("<@&>")
        if raw.isdigit():
            resolved = ctx.guild.get_role(int(raw))
        if resolved is None:
            try:
                resolved = await commands.RoleConverter().convert(ctx, role)
            except commands.RoleNotFound:
                resolved = None

        if resolved is None:
            return await ctx.send(embed=_embed(
                "Error", "• __**Error**__\nNo role found matching that mention or ID."
            ), delete_after=10)

        r = resolved
        member_count = len(r.members)
        created_ts   = int(r.created_at.timestamp())

        perms = "All Permissions" if r.permissions.administrator else (
            ", ".join(p.replace("_", " ").title() for p, v in r.permissions if v) or "None"
        )
        if len(perms) > 200:
            enabled = [p for p, v in r.permissions if v]
            perms = f"{len(enabled)} permission(s) granted"

        e = discord.Embed(color=r.color if r.color.value else COL, timestamp=datetime.now(timezone.utc))
        e.set_author(
            name="Role Information",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )

        e.add_field(name="Name", value=f"```> {r.name}```", inline=False)
        e.add_field(name="ID", value=f"```{r.id}```", inline=True)
        e.add_field(name="Position", value=f"```{r.position}```", inline=True)
        e.add_field(name="Color", value=f"```{str(r.color).upper()}```", inline=True)
        e.add_field(name="Members", value=f"```{member_count}```", inline=True)
        e.add_field(name="Mentionable", value=f"```{'Yes' if r.mentionable else 'No'}```", inline=True)
        e.add_field(name="Hoisted", value=f"```{'Yes' if r.hoist else 'No'}```", inline=True)
        e.add_field(name="Managed", value=f"```{'Yes' if r.managed else 'No'}```", inline=True)
        e.add_field(name="Permissions", value=f"```{perms}```", inline=False)
        e.add_field(name="Created At", value=f"<t:{created_ts}:F>", inline=False)

        e.set_footer(text=FOOTER, icon_url=self.bot.user.display_avatar.url if self.bot.user else None)
        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
