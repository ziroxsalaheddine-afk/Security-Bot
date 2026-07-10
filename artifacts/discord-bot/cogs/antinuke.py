"""
Anti-Nuke Cog
─────────────
Uses on_audit_log_entry_create (gateway event, not REST polling) for
instant detection (~20–50 ms reaction window). Caches all channels and
roles on startup so auto-restore has everything it needs even after the
target object is deleted.
"""

import time
import asyncio
import logging
from collections import defaultdict

import discord
from discord.ext import commands

from utils import db, embeds
from utils.bypass_db import is_bypassed

log = logging.getLogger("guardian.antinuke")

NUKE_ACTIONS = {
    discord.AuditLogAction.channel_delete,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.ban,
    discord.AuditLogAction.kick,
    discord.AuditLogAction.webhook_create,
    discord.AuditLogAction.member_prune,
}

RATE_LIMIT_ACTIONS = {
    discord.AuditLogAction.channel_delete,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.ban,
    discord.AuditLogAction.kick,
}


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ch_cache: dict[int, dict[int, dict]] = defaultdict(dict)
        self._role_cache: dict[int, dict[int, dict]] = defaultdict(dict)
        self._nuke_tracker: dict[tuple, list] = defaultdict(list)
        self._punished: set = set()

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _serialize_overwrites(self, ch: discord.abc.GuildChannel) -> dict:
        result = {}
        for target, ow in ch.overwrites.items():
            allow, deny = ow.pair()
            key = f"{'role' if isinstance(target, discord.Role) else 'member'}:{target.id}"
            result[key] = {"allow": allow.value, "deny": deny.value}
        return result

    def _serialize_channel(self, ch: discord.abc.GuildChannel) -> dict:
        data: dict = {
            "name": ch.name,
            "type": ch.type.value,
            "position": ch.position,
            "category_id": ch.category_id,
            "overwrites": self._serialize_overwrites(ch),
        }
        if isinstance(ch, discord.TextChannel):
            data.update(topic=ch.topic, slowmode_delay=ch.slowmode_delay, nsfw=ch.is_nsfw())
        elif isinstance(ch, discord.VoiceChannel):
            data.update(bitrate=ch.bitrate, user_limit=ch.user_limit)
        elif isinstance(ch, discord.ForumChannel):
            data.update(topic=ch.topic)
        return data

    def _serialize_role(self, role: discord.Role) -> dict:
        return {
            "name": role.name,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position": role.position,
            "members": [m.id for m in role.members],
        }

    def _deserialize_overwrites(self, guild: discord.Guild, raw: dict) -> dict:
        result = {}
        for key, val in raw.items():
            kind, oid = key.split(":", 1)
            oid = int(oid)
            target = guild.get_role(oid) if kind == "role" else guild.get_member(oid)
            if target is None:
                continue
            ow = discord.PermissionOverwrite.from_pair(
                discord.Permissions(val["allow"]),
                discord.Permissions(val["deny"]),
            )
            result[target] = ow
        return result

    def _cache_guild(self, guild: discord.Guild):
        for ch in guild.channels:
            self._ch_cache[guild.id][ch.id] = self._serialize_channel(ch)
        for role in guild.roles:
            if not role.is_default():
                self._role_cache[guild.id][role.id] = self._serialize_role(role)

    # ── Listeners: keep cache fresh ───────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            self._cache_guild(guild)
        log.info("Anti-nuke cache ready for %d guild(s).", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._cache_guild(guild)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch: discord.abc.GuildChannel):
        self._ch_cache[ch.guild.id][ch.id] = self._serialize_channel(ch)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, _before, after: discord.abc.GuildChannel):
        self._ch_cache[after.guild.id][after.id] = self._serialize_channel(after)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not role.is_default():
            self._role_cache[role.guild.id][role.id] = self._serialize_role(role)

    @commands.Cog.listener()
    async def on_guild_role_update(self, _before, after: discord.Role):
        if not after.is_default():
            self._role_cache[after.guild.id][after.id] = self._serialize_role(after)

    # ── Core: audit log gateway event ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        t0 = time.perf_counter()

        if entry.action not in NUKE_ACTIONS:
            return

        user = entry.user
        if user is None or user.bot:
            return

        guild = entry.guild
        cfg = db.get_config().get("antinuke", {})
        if not cfg.get("enabled", True):
            return

        # ── Whitelisted: only apply rogue-admin rate-limit ────────────────────
        if db.is_whitelisted(user.id):
            if entry.action in RATE_LIMIT_ACTIONS:
                await self._check_rogue_admin(guild, user, cfg)
            return

        # ── Bypass users: Warden cog monitors and enforces threshold ──────────
        if is_bypassed(user.id):
            return

        # ── Non-whitelisted: punish + restore ─────────────────────────────────
        key = (guild.id, user.id)
        if key in self._punished:
            return
        self._punished.add(key)
        self.bot.loop.call_later(30, lambda: self._punished.discard(key))

        member = guild.get_member(user.id)
        punish_task = asyncio.create_task(
            self._punish(guild, member, user, cfg, "Unauthorized destructive action")
        )

        if entry.action == discord.AuditLogAction.channel_delete:
            await self._restore_channel(guild, entry, t0, user)
        elif entry.action == discord.AuditLogAction.role_delete:
            await self._restore_role(guild, entry, t0, user)
        else:
            elapsed = time.perf_counter() - t0
            embed = embeds.stats(
                f"🛡️  Action Blocked — {entry.action.name.replace('_', ' ').title()}",
                elapsed,
                fields=[
                    ("Perpetrator", f"<@{user.id}>", True),
                    ("Action", f"`{entry.action.name}`", True),
                    ("Status", "`Punished`", True),
                ],
            )
            await self._send_log(guild, embed)

        await punish_task

    # ── Rogue-admin rate limiter ──────────────────────────────────────────────

    async def _check_rogue_admin(self, guild: discord.Guild, user: discord.User, cfg: dict):
        threshold = cfg.get("threshold", 3)
        interval = cfg.get("interval", 10)
        key = (guild.id, user.id)
        now = time.time()

        self._nuke_tracker[key].append(now)
        self._nuke_tracker[key] = [
            ts for ts in self._nuke_tracker[key] if now - ts <= interval
        ]

        if len(self._nuke_tracker[key]) >= threshold:
            self._nuke_tracker[key] = []
            member = guild.get_member(user.id)
            if member:
                log.warning("Rogue admin detected: %s (%d) in %s", user, user.id, guild)
                try:
                    safe = [r for r in member.roles if r.is_default() or r >= guild.me.top_role]
                    await member.edit(roles=safe, reason="[Guardian] Rogue admin: too many destructive actions")
                except Exception as e:
                    log.error("Failed to demote rogue admin: %s", e)

                embed = embeds.danger(
                    "⚠️  Rogue Admin Detected",
                    f"{user.mention} performed `{threshold}+` destructive actions in `{interval}s`.\n"
                    f"All roles have been stripped immediately.",
                )
                await self._send_log(guild, embed)

    # ── Punish ────────────────────────────────────────────────────────────────

    async def _punish(
        self,
        guild: discord.Guild,
        member: discord.Member | None,
        user: discord.User,
        cfg: dict,
        reason: str,
    ):
        action = cfg.get("action", "ban")
        full_reason = f"[Guardian] Anti-Nuke: {reason}"

        if member is None:
            if action == "ban":
                try:
                    await guild.ban(discord.Object(id=user.id), reason=full_reason)
                except Exception as e:
                    log.error("Failed to ban user %d: %s", user.id, e)
            return

        if action == "ban":
            try:
                await member.ban(reason=full_reason)
            except Exception:
                try:
                    await member.edit(roles=[], reason=full_reason)
                except Exception as e:
                    log.error("Fallback role-strip failed: %s", e)
        elif action == "kick":
            try:
                await member.kick(reason=full_reason)
            except Exception as e:
                log.error("Failed to kick %d: %s", member.id, e)
        else:
            try:
                await member.edit(roles=[], reason=full_reason)
            except Exception as e:
                log.error("Failed to quarantine %d: %s", member.id, e)

    # ── Channel restore ───────────────────────────────────────────────────────

    async def _restore_channel(
        self,
        guild: discord.Guild,
        entry: discord.AuditLogEntry,
        t0: float,
        actor: discord.User,
    ):
        ch_id = entry.target.id if entry.target else None
        if not ch_id:
            return

        data = self._ch_cache.get(guild.id, {}).get(ch_id)
        if not data:
            log.warning("Channel cache miss for id=%d", ch_id)
            return

        try:
            ch_type = discord.ChannelType(data["type"])
            overwrites = self._deserialize_overwrites(guild, data.get("overwrites", {}))
            category = guild.get_channel(data["category_id"]) if data.get("category_id") else None
            if isinstance(category, discord.CategoryChannel):
                cat = category
            else:
                cat = None

            kw: dict = dict(name=data["name"], overwrites=overwrites, reason="[Guardian] Anti-Nuke: Auto-restore")
            if cat:
                kw["category"] = cat

            if ch_type == discord.ChannelType.text:
                kw.update(topic=data.get("topic") or "", slowmode_delay=data.get("slowmode_delay", 0), nsfw=data.get("nsfw", False))
                new_ch = await guild.create_text_channel(**kw)
            elif ch_type == discord.ChannelType.voice:
                kw.update(bitrate=data.get("bitrate", 64000), user_limit=data.get("user_limit", 0))
                new_ch = await guild.create_voice_channel(**kw)
            elif ch_type == discord.ChannelType.category:
                new_ch = await guild.create_category(name=data["name"], overwrites=overwrites, reason=kw["reason"])
            elif ch_type == discord.ChannelType.forum:
                kw.update(topic=data.get("topic") or "")
                new_ch = await guild.create_forum(**kw)
            else:
                new_ch = await guild.create_text_channel(**kw)

            elapsed = time.perf_counter() - t0
            ow_count = len(data.get("overwrites", {}))

            embed = embeds.stats(
                "🔄  Channel Auto-Restored",
                elapsed,
                fields=[
                    ("Channel", f"`#{data['name']}`", True),
                    ("Type", f"`{ch_type.name}`", True),
                    ("Permission Overwrites", f"`{ow_count}` restored", True),
                    ("New Channel", new_ch.mention, True),
                    ("Perpetrator", f"<@{actor.id}>", True),
                    ("Action Taken", "`Banned / Stripped`", True),
                ],
            )
            await self._send_log(guild, embed)
            log.info("Restored #%s in %s (%.2fms)", data["name"], guild, elapsed * 1000)

        except Exception as e:
            log.error("Channel restore failed: %s", e)

    # ── Role restore ──────────────────────────────────────────────────────────

    async def _restore_role(
        self,
        guild: discord.Guild,
        entry: discord.AuditLogEntry,
        t0: float,
        actor: discord.User,
    ):
        role_id = entry.target.id if entry.target else None
        if not role_id:
            return

        data = self._role_cache.get(guild.id, {}).get(role_id)
        if not data:
            log.warning("Role cache miss for id=%d", role_id)
            return

        try:
            new_role = await guild.create_role(
                name=data["name"],
                color=discord.Color(data["color"]),
                hoist=data["hoist"],
                mentionable=data["mentionable"],
                permissions=discord.Permissions(data["permissions"]),
                reason="[Guardian] Anti-Nuke: Auto-restore",
            )

            member_ids: list = data.get("members", [])
            reassigned = 0

            async def _assign(mid: int):
                nonlocal reassigned
                m = guild.get_member(mid)
                if m:
                    try:
                        await m.add_roles(new_role, reason="[Guardian] Role auto-restore")
                        reassigned += 1
                    except Exception:
                        pass

            batch = 15
            for i in range(0, len(member_ids), batch):
                await asyncio.gather(*[_assign(mid) for mid in member_ids[i : i + batch]])

            elapsed = time.perf_counter() - t0

            embed = embeds.stats(
                "🔄  Role Auto-Restored",
                elapsed,
                fields=[
                    ("Role", f"`@{data['name']}`", True),
                    ("Color", f"`#{data['color']:06X}`", True),
                    ("Permissions", f"`{data['permissions']}`", True),
                    ("Members Reassigned", f"`{reassigned}` / `{len(member_ids)}`", True),
                    ("Perpetrator", f"<@{actor.id}>", True),
                    ("Action Taken", "`Banned / Stripped`", True),
                ],
            )
            await self._send_log(guild, embed)
            log.info("Restored @%s in %s, reassigned %d members (%.2fms)", data["name"], guild, reassigned, elapsed * 1000)

        except Exception as e:
            log.error("Role restore failed: %s", e)

    # ── Log helper ────────────────────────────────────────────────────────────

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed):
        ch_id = db.get_log_channel()
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
