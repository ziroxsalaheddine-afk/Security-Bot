"""
Anti-Nuke Cog — Role & Channel restoration with per-guild whitelist checks.

Role cache:
  - Background task syncs all guild members' roles into SQLite every 5 minutes.
  - on_member_update patches individual members' cache entries instantly.
  - on_guild_join triggers an immediate full sync for the new guild.

Role restore (on_guild_role_delete):
  - Checks audit log to identify the executor.
  - If executor IS whitelisted → skip (trusted action).
  - If executor is NOT whitelisted → recreate the role with identical properties,
    fetch the member list from role_cache, re-assign the role to every member,
    update the cache with the new role ID, and log a warning embed.

Channel restore (on_guild_channel_delete):
  - Same whitelist check logic.
  - Recreates the channel preserving: name, type, topic, category, overwrites,
    slowmode, NSFW flag, bitrate/user_limit (voice), position.
"""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from database import Database
from utils import get_audit_executor, is_whitelisted, warn_embed

log = logging.getLogger("secbot.antinuke")

# Seconds to wait between member role-reassignments to respect rate limits.
_REASSIGN_DELAY = 0.30


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self._sync_task.start()

    def cog_unload(self) -> None:
        self._sync_task.cancel()

    # ══════════════════════════════════════════════════════════════════════════
    #  Role cache — background sync (every 5 min) + per-event updates
    # ══════════════════════════════════════════════════════════════════════════

    @tasks.loop(minutes=5)
    async def _sync_task(self) -> None:
        for guild in self.bot.guilds:
            await self._sync_guild(guild)

    @_sync_task.before_loop
    async def _before_sync(self) -> None:
        await self.bot.wait_until_ready()

    async def _sync_guild(self, guild: discord.Guild) -> None:
        """Fully rebuild the role_cache for one guild."""
        if not guild.chunked:
            try:
                await guild.chunk(cache=True)
            except Exception as exc:
                log.warning("Chunk failed for guild %s: %s", guild.id, exc)

        await self.db.role_cache_clear_guild(guild.id)

        for member in guild.members:
            role_ids = [r.id for r in member.roles if not r.is_default()]
            if role_ids:
                await self.db.role_cache_sync_member(guild.id, member.id, role_ids)

        log.debug("Role cache synced: guild=%d members=%d", guild.id, guild.member_count)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Sync all guilds immediately on (re)connect.
        await asyncio.gather(*[self._sync_guild(g) for g in self.bot.guilds],
                             return_exceptions=True)
        log.info("Initial role cache sync complete (%d guild(s)).", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._sync_guild(guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Patch the cache immediately when a member's roles change."""
        if before.roles == after.roles:
            return
        role_ids = [r.id for r in after.roles if not r.is_default()]
        await self.db.role_cache_sync_member(after.guild.id, after.id, role_ids)

    # ══════════════════════════════════════════════════════════════════════════
    #  Role restore
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        guild = role.guild
        t0 = time.perf_counter()

        executor = await get_audit_executor(guild, discord.AuditLogAction.role_delete, role.id)

        # Skip if executor is whitelisted or is the bot itself.
        if executor:
            if executor.id == self.bot.user.id:
                return
            member = guild.get_member(executor.id)
            if member and await is_whitelisted(self.db, guild, member):
                log.debug("Whitelisted executor %s deleted role %r — skipping restore.", executor, role.name)
                return

        # ── Recreate the role ─────────────────────────────────────────────────
        new_role: discord.Role | None = None
        error: str | None = None

        try:
            new_role = await guild.create_role(
                name=role.name,
                color=role.color,
                hoist=role.hoist,
                mentionable=role.mentionable,
                permissions=role.permissions,
                reason="[Security Bot] Anti-Nuke: auto-restored deleted role",
            )
            # Best-effort position restore.
            try:
                await new_role.edit(position=max(1, role.position))
            except Exception:
                pass
        except discord.Forbidden as exc:
            error = f"Missing Permissions — {exc}"
            log.error("Role restore forbidden: guild=%d role=%r: %s", guild.id, role.name, exc)
        except Exception as exc:
            error = str(exc)[:120]
            log.error("Role restore failed: guild=%d role=%r: %s", guild.id, role.name, exc)

        # ── Re-assign to original members ─────────────────────────────────────
        reassigned = 0
        member_ids = await self.db.role_cache_get_members(guild.id, role.id)

        if new_role and member_ids:
            for uid in member_ids:
                m = guild.get_member(uid)
                if m is None:
                    continue
                try:
                    await m.add_roles(new_role, reason="[Security Bot] Role restore — original member")
                    reassigned += 1
                except discord.Forbidden:
                    log.warning("Forbidden adding role to member %d", uid)
                except Exception as exc:
                    log.debug("Role re-assign failed for %d: %s", uid, exc)
                await asyncio.sleep(_REASSIGN_DELAY)

            # Swap the cached role ID from old → new.
            await self.db.role_cache_update_role_id(guild.id, role.id, new_role.id)

        elapsed = (time.perf_counter() - t0) * 1000
        log.info(
            "Role restore: guild=%d role=%r new_id=%s members=%d/%d elapsed=%.1fms",
            guild.id, role.name,
            new_role.id if new_role else "—",
            reassigned, len(member_ids), elapsed,
        )

        # ── Log embed ─────────────────────────────────────────────────────────
        await self._log(
            guild,
            self._role_restore_embed(
                role=role,
                new_role=new_role,
                executor=executor,
                error=error,
                reassigned=reassigned,
                total=len(member_ids),
                elapsed_ms=elapsed,
            ),
        )

    def _role_restore_embed(
        self,
        *,
        role: discord.Role,
        new_role: discord.Role | None,
        executor: discord.User | None,
        error: str | None,
        reassigned: int,
        total: int,
        elapsed_ms: float,
    ) -> discord.Embed:
        restored = new_role is not None and error is None
        embed = discord.Embed(
            title="🔄  Role Auto-Restored" if restored else "❌  Role Restore Failed",
            color=0x2ECC71 if restored else 0xE74C3C,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Deleted Role", value=f"`@{role.name}` (ID `{role.id}`)", inline=True)
        embed.add_field(name="New Role", value=new_role.mention if new_role else "`—`", inline=True)
        embed.add_field(
            name="Executor",
            value=f"{executor} (`{executor.id}`)" if executor else "*Unknown*",
            inline=False,
        )
        embed.add_field(name="Members Re-assigned", value=f"`{reassigned}` / `{total}`", inline=True)
        status = f"✅ Restored in `{elapsed_ms:.0f}ms`" if restored else f"❌ Failed — `{error}`"
        embed.add_field(name="Status", value=status, inline=True)
        embed.set_footer(text="Security Bot • Anti-Nuke")
        return embed

    # ══════════════════════════════════════════════════════════════════════════
    #  Channel restore
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        guild = channel.guild
        t0 = time.perf_counter()

        executor = await get_audit_executor(guild, discord.AuditLogAction.channel_delete, channel.id)

        if executor:
            if executor.id == self.bot.user.id:
                return
            member = guild.get_member(executor.id)
            if member and await is_whitelisted(self.db, guild, member):
                log.debug("Whitelisted executor %s deleted channel %r — skipping restore.", executor, channel.name)
                return

        new_ch: discord.abc.GuildChannel | None = None
        error: str | None = None

        try:
            new_ch = await self._recreate_channel(channel)
        except discord.Forbidden as exc:
            error = f"Missing Permissions — {exc}"
            log.error("Channel restore forbidden: guild=%d ch=%r: %s", guild.id, channel.name, exc)
        except Exception as exc:
            error = str(exc)[:120]
            log.error("Channel restore failed: guild=%d ch=%r: %s", guild.id, channel.name, exc)

        elapsed = (time.perf_counter() - t0) * 1000
        log.info(
            "Channel restore: guild=%d ch=%r new_id=%s elapsed=%.1fms",
            guild.id, channel.name,
            new_ch.id if new_ch else "—", elapsed,
        )

        await self._log(
            guild,
            self._channel_restore_embed(
                channel=channel,
                new_ch=new_ch,
                executor=executor,
                error=error,
                elapsed_ms=elapsed,
            ),
        )

    async def _recreate_channel(
        self, ch: discord.abc.GuildChannel
    ) -> discord.abc.GuildChannel:
        """Recreate a deleted channel preserving all properties."""
        guild = ch.guild
        reason = "[Security Bot] Anti-Nuke: auto-restored deleted channel"
        overwrites = ch.overwrites

        base: dict = dict(
            name=ch.name,
            overwrites=overwrites,
            reason=reason,
        )
        category = ch.category if ch.category else None
        if category:
            base["category"] = category

        if isinstance(ch, discord.TextChannel):
            new_ch = await guild.create_text_channel(
                **base,
                topic=ch.topic or "",
                slowmode_delay=ch.slowmode_delay,
                nsfw=ch.is_nsfw(),
            )
        elif isinstance(ch, discord.VoiceChannel):
            new_ch = await guild.create_voice_channel(
                **base,
                bitrate=min(ch.bitrate, guild.bitrate_limit),
                user_limit=ch.user_limit,
            )
        elif isinstance(ch, discord.CategoryChannel):
            new_ch = await guild.create_category(
                name=ch.name,
                overwrites=overwrites,
                reason=reason,
            )
        elif isinstance(ch, discord.StageChannel):
            new_ch = await guild.create_stage_channel(**base)
        elif isinstance(ch, discord.ForumChannel):
            new_ch = await guild.create_forum(**base, topic=ch.topic or "")
        else:
            # Fallback: recreate as text channel.
            new_ch = await guild.create_text_channel(**base)

        # Best-effort position restore.
        try:
            await new_ch.edit(position=ch.position)
        except Exception:
            pass

        return new_ch

    def _channel_restore_embed(
        self,
        *,
        channel: discord.abc.GuildChannel,
        new_ch: discord.abc.GuildChannel | None,
        executor: discord.User | None,
        error: str | None,
        elapsed_ms: float,
    ) -> discord.Embed:
        restored = new_ch is not None and error is None
        ch_type = channel.type.name.replace("_", " ").title()
        embed = discord.Embed(
            title="🔄  Channel Auto-Restored" if restored else "❌  Channel Restore Failed",
            color=0x2ECC71 if restored else 0xE74C3C,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Deleted Channel", value=f"`#{channel.name}` (ID `{channel.id}`)", inline=True)
        embed.add_field(name="Type", value=f"`{ch_type}`", inline=True)
        embed.add_field(name="New Channel", value=new_ch.mention if new_ch else "`—`", inline=True)
        embed.add_field(
            name="Executor",
            value=f"{executor} (`{executor.id}`)" if executor else "*Unknown*",
            inline=False,
        )
        status = f"✅ Restored in `{elapsed_ms:.0f}ms`" if restored else f"❌ Failed — `{error}`"
        embed.add_field(name="Status", value=status, inline=False)
        embed.set_footer(text="Security Bot • Anti-Nuke")
        return embed

    # ══════════════════════════════════════════════════════════════════════════
    #  Log channel helper
    # ══════════════════════════════════════════════════════════════════════════

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        """Send embed to the first text channel the bot can write to."""
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass
                return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNuke(bot))
