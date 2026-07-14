"""
Anti-Raid & Alt-Protection Cog
──────────────────────────────
Tracks member join rate. If joinThreshold members join within joinInterval
seconds, triggers a server lockdown and punishes the raiders.
Also enforces minimum account-age (alt protection) on every join.
"""

import time
import asyncio
import logging
from collections import defaultdict

import discord
from discord.ext import commands

from utils import db, embeds, notifications

log = logging.getLogger("guardian.antiraid")


class AntiRaid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._join_tracker: dict[int, list] = defaultdict(list)
        self._lockdown_active: dict[int, bool] = defaultdict(bool)
        self._lockdown_overwrites: dict[int, dict] = {}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        # ── Alt-account protection ─────────────────────────────────────────────
        alt_cfg = db.get_config().get("altProtection", {})
        if alt_cfg.get("enabled", True) and not db.is_whitelisted(member.id):
            age_days = (discord.utils.utcnow() - member.created_at).days
            min_age = alt_cfg.get("minAccountAge", 7)
            if age_days < min_age:
                alt_action = alt_cfg.get("action", "kick")
                # Warn the account and alert owner before removal
                await notifications.dm_warn_user(
                    self.bot, member, guild.name,
                    f"Account too new (age: {age_days}d, minimum: {min_age}d) — alt-account protection triggered"
                )
                await notifications.dm_owner_alert(
                    self.bot,
                    "🔍  Alt Account Removed",
                    (
                        f"**Guild:** {guild.name} (`{guild.id}`)\n"
                        f"**Member:** {member.mention} (`{member.id}`) — {member}\n"
                        f"**Account Age:** `{age_days}d` (minimum: `{min_age}d`)\n"
                        f"**Action:** `{alt_action}`"
                    ),
                )
                try:
                    if alt_action == "ban":
                        await member.ban(reason=f"[Guardian] Alt account (age: {age_days}d < {min_age}d)")
                    else:
                        await member.kick(reason=f"[Guardian] Alt account (age: {age_days}d < {min_age}d)")
                    log.info("Alt account removed: %s (%dd old) from %s", member, age_days, guild)
                    await self._log_alt(guild, member, age_days, alt_action)
                except Exception as e:
                    log.error("Alt protection failed for %s: %s", member, e)
                return

        # ── Raid detection ────────────────────────────────────────────────────
        raid_cfg = db.get_config().get("automod", {}).get("antiRaid", {})
        if not raid_cfg.get("enabled", True):
            return

        threshold = raid_cfg.get("joinThreshold", 10)
        interval = raid_cfg.get("joinInterval", 10)
        action = raid_cfg.get("action", "kick")
        now = time.time()

        self._join_tracker[guild.id].append((member.id, now))
        self._join_tracker[guild.id] = [
            (uid, ts) for uid, ts in self._join_tracker[guild.id]
            if now - ts <= interval
        ]

        if len(self._join_tracker[guild.id]) >= threshold and not self._lockdown_active[guild.id]:
            raiders = [uid for uid, _ in self._join_tracker[guild.id]]
            self._join_tracker[guild.id] = []
            log.warning("RAID in %s: %d joins in %ds", guild, len(raiders), interval)

            self._lockdown_active[guild.id] = True
            await self._lockdown(guild)

            punished = 0
            for uid in raiders:
                if db.is_whitelisted(uid):
                    continue
                m = guild.get_member(uid)
                if m:
                    # Warn the raider before removal
                    await notifications.dm_warn_user(
                        self.bot, m, guild.name,
                        f"You joined during a detected raid ({len(raiders)} joins in {interval}s) and have been actioned"
                    )
                    try:
                        if action == "ban":
                            await guild.ban(m, reason="[Guardian] Anti-Raid")
                        else:
                            await m.kick(reason="[Guardian] Anti-Raid")
                        punished += 1
                    except Exception:
                        pass

            await self._send_raid_log(guild, len(raiders), interval, punished)

            # Alert the bot owner about the raid
            await notifications.dm_owner_alert(
                self.bot,
                "🚨  Raid Detected — Lockdown Active",
                (
                    f"**Guild:** {guild.name} (`{guild.id}`)\n"
                    f"**Joins:** `{len(raiders)}` within `{interval}s`\n"
                    f"**Punished:** `{punished}` raiders\n"
                    f"**Action:** `{action}` · Server locked for 5 minutes"
                ),
            )

            await asyncio.sleep(300)
            if self._lockdown_active.get(guild.id):
                await self._unlock(guild)
                self._lockdown_active[guild.id] = False

    async def _lockdown(self, guild: discord.Guild):
        everyone = guild.default_role
        saved: dict[int, tuple] = {}
        for ch in guild.text_channels:
            ow = ch.overwrites_for(everyone)
            saved[ch.id] = (ow.send_messages,)
            ow.send_messages = False
            try:
                await ch.set_permissions(everyone, overwrite=ow, reason="[Guardian] Anti-Raid Lockdown")
            except Exception:
                pass
        self._lockdown_overwrites[guild.id] = saved
        log.info("Lockdown activated in %s", guild)

    async def _unlock(self, guild: discord.Guild):
        everyone = guild.default_role
        for ch in guild.text_channels:
            ow = ch.overwrites_for(everyone)
            ow.send_messages = None
            try:
                await ch.set_permissions(everyone, overwrite=ow, reason="[Guardian] Lockdown lifted")
            except Exception:
                pass
        self._lockdown_overwrites.pop(guild.id, None)
        log.info("Lockdown lifted in %s", guild)

    async def _send_raid_log(self, guild: discord.Guild, count: int, interval: int, punished: int):
        ch_id = db.get_log_channel()
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if not ch:
            return
        embed = embeds.danger(
            "🚨  RAID DETECTED — Lockdown Active",
            f"`{count}` members joined within `{interval}s`.\n"
            f"`{punished}` raiders punished.\n"
            f"Server locked. Auto-unlock in **5 minutes**.",
        )
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    async def _log_alt(self, guild: discord.Guild, member: discord.Member, age: int, action: str):
        ch_id = db.get_log_channel()
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if not ch:
            return
        embed = embeds.danger(
            "🔍  Alt Account Removed",
            f"{member.mention} (`{member.id}`)\n"
            f"Account age: `{age}` days\n"
            f"Action: `{action}`",
        )
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    # ── Manual lockdown commands ──────────────────────────────────────────────

    @commands.command(name="lockdown")
    async def lockdown_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        if self._lockdown_active[ctx.guild.id]:
            return await ctx.send(embed=embeds.info("Already Locked", "Server is already in lockdown."))
        self._lockdown_active[ctx.guild.id] = True
        await self._lockdown(ctx.guild)
        await ctx.send(embed=embeds.danger("🔒  Server Locked Down", "Use `+unlock` to lift the lockdown."))

    @commands.command(name="unlock")
    async def unlock_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        self._lockdown_active[ctx.guild.id] = False
        await self._unlock(ctx.guild)
        await ctx.send(embed=embeds.success("🔓  Lockdown Lifted", "Server channels are now unlocked."))


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
