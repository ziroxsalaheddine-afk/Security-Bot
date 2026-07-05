"""
Admin Cog — whitelist/owner management, setlog, scaninvites, config commands.
All commands silently ignore non-whitelisted users.
Help command lives in cogs/help.py.
"""

import logging
from typing import Optional

import discord
from discord.ext import commands

from utils import db, embeds

log = logging.getLogger("guardian.admin")


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Whitelist ─────────────────────────────────────────────────────────────

    @commands.group(name="whitelist", invoke_without_command=True)
    async def whitelist(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        await ctx.send(embed=embeds.info("Whitelist", "Use `+whitelist add/remove/list`."))

    @whitelist.command(name="add")
    async def wl_add(self, ctx: commands.Context, user: discord.User):
        if not db.is_whitelisted(ctx.author.id):
            return
        db.add_whitelist(user.id)
        await ctx.send(embed=embeds.success("Whitelist Updated", f"{user.mention} has been **whitelisted**."))

    @whitelist.command(name="remove")
    async def wl_remove(self, ctx: commands.Context, user: discord.User):
        if not db.is_whitelisted(ctx.author.id):
            return
        db.remove_whitelist(user.id)
        await ctx.send(embed=embeds.success("Whitelist Updated", f"{user.mention} has been **removed** from the whitelist."))

    @whitelist.command(name="list")
    async def wl_list(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        wl = db.get_whitelist()
        if not wl:
            return await ctx.send(embed=embeds.info("Whitelist", "No users are currently whitelisted."))
        lines = "\n".join(f"<@{uid}>  (`{uid}`)" for uid in wl)
        await ctx.send(embed=embeds.info(f"Whitelist — {len(wl)} user(s)", lines))

    # ── Owner ─────────────────────────────────────────────────────────────────

    @commands.group(name="owner", invoke_without_command=True)
    async def owner(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        await ctx.send(embed=embeds.info("Owner", "Use `+owner add/remove/list`."))

    @owner.command(name="add")
    async def owner_add(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            return await ctx.send(
                embed=embeds.danger("Access Denied", "Only owners can add other owners."), delete_after=5
            )
        db.add_owner(user.id)
        await ctx.send(embed=embeds.success("Owner Added", f"{user.mention} is now an owner."))

    @owner.command(name="remove")
    async def owner_remove(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            return await ctx.send(
                embed=embeds.danger("Access Denied", "Only owners can remove owners."), delete_after=5
            )
        db.remove_owner(user.id)
        await ctx.send(embed=embeds.success("Owner Removed", f"{user.mention} is no longer an owner."))

    @owner.command(name="list")
    async def owner_list(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        owners = db.get_owners()
        if not owners:
            return await ctx.send(embed=embeds.info("Owners", "No owners configured."))
        lines = "\n".join(f"<@{uid}>  (`{uid}`)" for uid in owners)
        await ctx.send(embed=embeds.info(f"Owners — {len(owners)}", lines))

    # ── Set log channel ───────────────────────────────────────────────────────

    @commands.command(name="setlog")
    async def setlog(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        if not db.is_whitelisted(ctx.author.id):
            return
        if channel is None:
            db.set_log_channel(None)
            return await ctx.send(embed=embeds.success("Log Disabled", "Security logs have been turned off."))
        db.set_log_channel(channel.id)
        await ctx.send(embed=embeds.success("Log Channel Set", f"Security events will be sent to {channel.mention}."))

    # ── Scan invites ──────────────────────────────────────────────────────────

    @commands.command(name="scaninvites")
    async def scaninvites(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        try:
            invites = await ctx.guild.invites()
        except discord.Forbidden:
            return await ctx.send(embed=embeds.danger("Permission Error", "Missing `Manage Guild` permission."))

        suspicious = []
        for inv in invites:
            flags = []
            if inv.max_uses == 0:
                flags.append("♾️ Unlimited uses")
            if inv.max_age == 0:
                flags.append("⏰ Never expires")
            if flags:
                suspicious.append((inv, flags))

        if not suspicious:
            return await ctx.send(
                embed=embeds.success("Scan Complete", f"No suspicious invites found out of `{len(invites)}` total.")
            )

        desc = ""
        for inv, flags in suspicious[:15]:
            inviter = inv.inviter.mention if inv.inviter else "`Unknown`"
            desc += f"**`{inv.code}`** — {inviter} — `#{inv.channel.name if inv.channel else '?'}`\n"
            desc += "  " + "  ·  ".join(flags) + "\n\n"

        embed = embeds.danger(f"⚠️  {len(suspicious)} Suspicious Invite(s)", desc)
        embed.add_field(name="Total Invites", value=f"`{len(invites)}`", inline=True)
        embed.add_field(name="Flagged", value=f"`{len(suspicious)}`", inline=True)
        await ctx.send(embed=embed)

    # ── Anti-nuke config ──────────────────────────────────────────────────────

    @commands.group(name="antinuke", invoke_without_command=True)
    async def antinuke_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        cfg = db.get_config().get("antinuke", {})
        embed = embeds.info(
            "Anti-Nuke Config",
            f"**Status:** `{'ON' if cfg.get('enabled', True) else 'OFF'}`\n"
            f"**Threshold:** `{cfg.get('threshold', 3)}` actions\n"
            f"**Interval:** `{cfg.get('interval', 10)}s`\n"
            f"**Action:** `{cfg.get('action', 'ban')}`",
        )
        await ctx.send(embed=embed)

    @antinuke_cmd.command(name="on")
    async def an_on(self, ctx):
        if not db.is_whitelisted(ctx.author.id): return
        db.set_config(["antinuke", "enabled"], True)
        await ctx.send(embed=embeds.success("Anti-Nuke Enabled"))

    @antinuke_cmd.command(name="off")
    async def an_off(self, ctx):
        if not db.is_whitelisted(ctx.author.id): return
        db.set_config(["antinuke", "enabled"], False)
        await ctx.send(embed=embeds.success("Anti-Nuke Disabled"))

    @antinuke_cmd.command(name="threshold")
    async def an_threshold(self, ctx, n: int):
        if not db.is_whitelisted(ctx.author.id): return
        if n < 1:
            return await ctx.send(embed=embeds.danger("Invalid", "Must be ≥ 1."))
        db.set_config(["antinuke", "threshold"], n)
        await ctx.send(embed=embeds.success("Threshold Updated", f"Set to `{n}` actions."))

    @antinuke_cmd.command(name="interval")
    async def an_interval(self, ctx, seconds: int):
        if not db.is_whitelisted(ctx.author.id): return
        if seconds < 1:
            return await ctx.send(embed=embeds.danger("Invalid", "Must be ≥ 1 second."))
        db.set_config(["antinuke", "interval"], seconds)
        await ctx.send(embed=embeds.success("Interval Updated", f"Set to `{seconds}s`."))

    @antinuke_cmd.command(name="action")
    async def an_action(self, ctx, action: str):
        if not db.is_whitelisted(ctx.author.id): return
        action = action.lower()
        if action not in ("ban", "kick", "quarantine"):
            return await ctx.send(embed=embeds.danger("Invalid", "Use: `ban`, `kick`, or `quarantine`."))
        db.set_config(["antinuke", "action"], action)
        await ctx.send(embed=embeds.success("Action Updated", f"Punishment set to `{action}`."))

    # ── AutoMod config ────────────────────────────────────────────────────────

    @commands.group(name="automod", invoke_without_command=True)
    async def automod_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id): return
        cfg = db.get_config().get("automod", {})
        embed = embeds.info(
            "AutoMod Config",
            f"**Anti-Link:** `{'ON' if cfg.get('antiLink', {}).get('enabled') else 'OFF'}`\n"
            f"**Anti-Spam:** `{'ON' if cfg.get('antiSpam', {}).get('enabled') else 'OFF'}`\n"
            f"**Anti-Raid:** `{'ON' if cfg.get('antiRaid', {}).get('enabled') else 'OFF'}`",
        )
        await ctx.send(embed=embed)

    @automod_cmd.command(name="antilink")
    async def am_antilink(self, ctx, toggle: str):
        if not db.is_whitelisted(ctx.author.id): return
        val = toggle.lower() in ("on", "true", "1", "yes", "enable")
        db.set_config(["automod", "antiLink", "enabled"], val)
        await ctx.send(embed=embeds.success(f"Anti-Link {'Enabled' if val else 'Disabled'}"))

    @automod_cmd.command(name="antispam")
    async def am_antispam(self, ctx, toggle: str):
        if not db.is_whitelisted(ctx.author.id): return
        val = toggle.lower() in ("on", "true", "1", "yes", "enable")
        db.set_config(["automod", "antiSpam", "enabled"], val)
        await ctx.send(embed=embeds.success(f"Anti-Spam {'Enabled' if val else 'Disabled'}"))

    @automod_cmd.command(name="antiraid")
    async def am_antiraid(self, ctx, toggle: str):
        if not db.is_whitelisted(ctx.author.id): return
        val = toggle.lower() in ("on", "true", "1", "yes", "enable")
        db.set_config(["automod", "antiRaid", "enabled"], val)
        await ctx.send(embed=embeds.success(f"Anti-Raid {'Enabled' if val else 'Disabled'}"))

    # ── User info ─────────────────────────────────────────────────────────────

    @commands.command(name="userinfo")
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if not db.is_whitelisted(ctx.author.id):
            return
        member = member or ctx.author
        age = (discord.utils.utcnow() - member.created_at).days
        joined = (discord.utils.utcnow() - member.joined_at).days if member.joined_at else "?"

        embed = embeds.info(f"User Info — {member}", "")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Account Age", value=f"`{age}d`", inline=True)
        embed.add_field(name="In Server", value=f"`{joined}d`", inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Roles", value=f"`{len(member.roles) - 1}`", inline=True)
        embed.add_field(name="Whitelisted", value="`✅ Yes`" if db.is_whitelisted(member.id) else "`❌ No`", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
