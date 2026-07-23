"""
Anti-Nuke Cog v2 — Ultra-Fast Dual-Layer Detection + Perfect Restoration
═══════════════════════════════════════════════════════════════════════════

ARCHITECTURE — Dual Layer
──────────────────────────
Layer 1  Gateway delete / ban events (fire FIRST, ~0 ms after the action).
         Purpose: immediately snapshot the target's state from the live
         Python object — before Discord even creates the audit log entry.
         For emojis and soundboard sounds the CDN asset (image / audio) is
         also pre-downloaded in a background task so restoration is instant
         the moment Layer 2 confirms the executor.

Layer 2  on_audit_log_entry_create (gateway-pushed, zero REST-poll delay).
         Purpose: deliver the executor.  Checks whitelist / bypass status,
         applies the correct punishment, and triggers restoration using the
         snapshot captured by Layer 1.

A _seen_events dedup set guarantees each (guild, target, action) is acted on
exactly once, guarding against duplicate gateway dispatches.

RESTORATION — Perfect Fidelity
────────────────────────────────
• Channels   name, type, topic, NSFW, slowmode, parent category, position,
             and every role/member permission overwrite (allow + deny bits).
• Roles      name, color, hoist, mentionable, permissions.  Every member who
             originally had the role is re-assigned in batches of 15.
             The role member-list stays accurate via on_member_update which
             patches individual role entries as roles change per-member.
• Emojis     CDN image pre-downloaded in Layer 1 (bytes cached in memory),
             re-uploaded with the original name — animated GIFs preserved.
• Soundboard CDN audio pre-downloaded in Layer 1, re-uploaded via REST API
             (discord.py 2.x has no native soundboard-create yet).

SECURITY RULES
──────────────
Whitelist       — Normal full bypass of all anti-nuke checks.
Rogue-whitelist — If a whitelisted user performs ≥ 20 bans within 1 hour
                  their whitelist is revoked and all roles stripped.
Bypass (60 %)   — Bypass users hit at 60 % of the configured threshold;
                  all their roles are stripped without full punishment.
Ban → Kick      — If the malicious action WAS a ban, the executor receives
                  a KICK instead of a ban (avoids conflating ban/unban).
Hierarchy guard — If the executor's top role is ≥ the bot's top role the
                  bot cannot strip / kick them.  A Forbidden-safe path is
                  taken, the block is recorded, and a hierarchy warning is
                  included in every log embed for that event.

DETAILED LOGGING
────────────────
Every detected action produces at least two rich embeds in the log channel:

  1. Restoration embed  — asset name + old ID, executor tag + ID,
     type/details, success / failure, elapsed ms, exact timestamp.
  2. Punishment embed   — executor tag + ID, trigger action, punishment
     type, success / partial / hierarchy-block, exact timestamp.

Both embeds include a ⚠️ Hierarchy Warning field whenever the executor's
top role was ≥ or = the bot's top role, so server owners know when the bot
was unable to fully punish.
"""

import time
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

from utils import db, embeds, notifications
from utils.bypass_db import is_bypassed

log = logging.getLogger("guardian.antinuke")

# ── Tunable constants ──────────────────────────────────────────────────────────

WL_BAN_LIMIT   = 20       # bans in …
WL_BAN_WINDOW  = 3600     # … one hour before whitelist is revoked
BYPASS_RATIO   = 0.60     # fraction of configured threshold that triggers bypass punishment
DELETE_DELAY   = 0.35     # seconds between bulk-role re-assignments (rate-limit buffer)
SEEN_TTL       = 30       # seconds before a dedup entry expires
CDN            = "https://cdn.discordapp.com"
DISCORD_API    = "https://discord.com/api/v10"

# ── Embed colours ──────────────────────────────────────────────────────────────
COL_RESTORED  = 0x2ECC71   # green  — action fully reversed
COL_FAILED    = 0xE74C3C   # red    — restoration could not complete
COL_PUNISHED  = 0x2ECC71   # green  — executor dealt with
COL_HIERARCHY = 0xE67E22   # amber  — hierarchy blocked full punishment
COL_DANGER    = 0xE74C3C   # red    — abuse / rogue user
FOOTER        = "Guardian Security System"

# ── Actions the engine watches ─────────────────────────────────────────────────

NUKE_ACTIONS = {
    discord.AuditLogAction.channel_delete,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.ban,
    discord.AuditLogAction.kick,
    discord.AuditLogAction.webhook_create,
    discord.AuditLogAction.member_prune,
    discord.AuditLogAction.emoji_delete,
}

# soundboard_sound_delete was added in discord.py ≥ 2.4 — add defensively.
_SB_DELETE = getattr(discord.AuditLogAction, "soundboard_sound_delete", None)
if _SB_DELETE:
    NUKE_ACTIONS.add(_SB_DELETE)

# Actions tracked for rogue-whitelist and bypass-threshold counters.
RATE_LIMIT_ACTIONS = {
    discord.AuditLogAction.channel_delete,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.ban,
    discord.AuditLogAction.kick,
}


# ══════════════════════════════════════════════════════════════════════════════
#  Local embed builders
#  (richer than the generic utils/embeds.py helpers — tailored for antinuke)
# ══════════════════════════════════════════════════════════════════════════════

def _now_ts() -> str:
    """Exact timestamp formatted as a Discord long date-time."""
    return discord.utils.format_dt(datetime.now(timezone.utc), "F")


def _executor_line(user: discord.User) -> str:
    return f"{user} (`{user.id}`)"


def _hierarchy_field(
    member:    discord.Member,
    guild:     discord.Guild,
) -> tuple[str, str] | None:
    """
    Return a (name, value) pair for a hierarchy-warning field, or None if
    the executor was safely below the bot.
    """
    bot_top = guild.me.top_role
    usr_top = member.top_role
    if usr_top >= bot_top:
        return (
            "⚠️ Hierarchy Warning",
            f"Executor's top role **{usr_top.mention}** (pos `{usr_top.position}`) is "
            f"**{'equal to' if usr_top == bot_top else 'above'}** the bot's top role "
            f"**{bot_top.mention}** (pos `{bot_top.position}`).\n"
            "Role-strip / kick may have been partially or fully blocked.",
        )
    return None


def _build_embed(
    title:     str,
    color:     int,
    fields:    list[tuple[str, str, bool]],
    *,
    footer:    str = FOOTER,
) -> discord.Embed:
    e = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=footer)
    for name, value, inline in fields:
        e.add_field(name=name, value=value, inline=inline)
    return e


def _channel_restore_embed(
    *,
    data:        dict,
    ch_id:       int,
    new_ch:      discord.abc.GuildChannel | None,
    actor:       discord.User,
    actor_member: discord.Member | None,
    guild:       discord.Guild,
    elapsed_ms:  float,
    error:       str | None,
    ow_count:    int,
) -> discord.Embed:
    """
    Rich restoration embed for a deleted channel event.

    Always contains:
      • Executor Name#discriminator + ID
      • Deleted channel name + old ID
      • Channel type
      • Permission overwrite count
      • New channel mention (if restored)
      • Restoration status (Restored ✅ / Failed ❌) with elapsed time
      • Exact timestamp (Discord format_dt)
      • Hierarchy warning (if executor's top role ≥ bot's top role)
    """
    restored  = error is None and new_ch is not None
    ch_type   = discord.ChannelType(data["type"]).name.replace("_", " ").title()
    status    = f"✅ Restored in `{elapsed_ms:.1f}ms`" if restored else f"❌ Failed — `{error}`"

    fields: list[tuple[str, str, bool]] = [
        ("Executor",          _executor_line(actor),                      False),
        ("Channel Deleted",   f"`#{data['name']}` (ID: `{ch_id}`)",       True),
        ("Channel Type",      f"`{ch_type}`",                              True),
        ("Perm. Overwrites",  f"`{ow_count}` restored",                   True),
        ("New Channel",       new_ch.mention if new_ch else "`—`",        True),
        ("Restoration",       status,                                       False),
        ("Detected At",       _now_ts(),                                   False),
    ]

    # Hierarchy warning — only if the member is still in the guild.
    if actor_member:
        hw = _hierarchy_field(actor_member, guild)
        if hw:
            fields.append((hw[0], hw[1], False))

    return _build_embed(
        "🔄  Channel Restored" if restored else "❌  Channel Restore Failed",
        COL_RESTORED if restored else COL_FAILED,
        fields,
    )


def _role_restore_embed(
    *,
    data:         dict,
    role_id:      int,
    new_role:     discord.Role | None,
    actor:        discord.User,
    actor_member: discord.Member | None,
    guild:        discord.Guild,
    elapsed_ms:   float,
    error:        str | None,
    reassigned:   int,
    total:        int,
) -> discord.Embed:
    """
    Rich restoration embed for a deleted role event.

    Always contains:
      • Executor Name#discriminator + ID
      • Deleted role name + old ID
      • Color hex, permissions integer
      • Members reassigned count (N / M)
      • Restoration status (Restored ✅ / Failed ❌) with elapsed time
      • Exact timestamp
      • Hierarchy warning (if applicable)
    """
    restored = error is None and new_role is not None
    color_hex = f"#{data['color']:06X}"
    status    = f"✅ Restored in `{elapsed_ms:.1f}ms`" if restored else f"❌ Failed — `{error}`"

    fields: list[tuple[str, str, bool]] = [
        ("Executor",         _executor_line(actor),                           False),
        ("Role Deleted",     f"`@{data['name']}` (ID: `{role_id}`)",          True),
        ("Color",            f"`{color_hex}`",                                 True),
        ("Permissions",      f"`{data['permissions']}`",                       True),
        ("Members Affected", f"`{reassigned}` / `{total}` reassigned",        True),
        ("Restoration",      status,                                            False),
        ("Detected At",      _now_ts(),                                        False),
    ]

    if actor_member:
        hw = _hierarchy_field(actor_member, guild)
        if hw:
            fields.append((hw[0], hw[1], False))

    return _build_embed(
        "🔄  Role Restored" if restored else "❌  Role Restore Failed",
        COL_RESTORED if restored else COL_FAILED,
        fields,
    )


def _punishment_embed(
    *,
    actor:            discord.User,
    actor_member:     discord.Member | None,
    guild:            discord.Guild,
    trigger_action:   discord.AuditLogAction | None,
    effective_action: str,
    success:          bool,
    hierarchy_block:  bool,
    fail_reason:      str | None,
) -> discord.Embed:
    """
    Dedicated punishment result embed — sent to the log channel after
    every enforcement action, separate from the restoration embed.

    Contains:
      • Executor tag + ID
      • Trigger action name
      • Punishment applied (Ban / Kick / Strip)
      • Status (✅ Success / ❌ Failed / ⚠️ Partial — hierarchy block)
      • Hierarchy warning (if executor's top role ≥ bot's top role)
      • Exact timestamp
    """
    action_name = trigger_action.name.replace("_", " ").title() if trigger_action else "Unknown"

    if hierarchy_block and not success:
        status = "⚠️ Blocked — hierarchy"
        color  = COL_HIERARCHY
        title  = "⚠️  Punishment Blocked — Hierarchy"
    elif success:
        status = "✅ Applied"
        color  = COL_PUNISHED
        title  = "🛡️  Executor Punished"
    else:
        status = f"❌ Failed — `{fail_reason}`"
        color  = COL_FAILED
        title  = "❌  Punishment Failed"

    fields: list[tuple[str, str, bool]] = [
        ("Executor",        _executor_line(actor),                                    False),
        ("Trigger Action",  f"`{action_name}`",                                       True),
        ("Punishment",      f"`{effective_action.upper()}`",                          True),
        ("Status",          status,                                                    True),
        ("Actioned At",     _now_ts(),                                                False),
    ]

    if hierarchy_block and actor_member:
        hw = _hierarchy_field(actor_member, guild)
        if hw:
            fields.append((hw[0], hw[1], False))

    return _build_embed(title, color, fields)


# ══════════════════════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════════════════════

class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # ── Object-state caches (populated from gateway, kept fresh by listeners) ─
        # Channel and role caches are the primary restoration source; they are
        # updated on every create/update event so the snapshot is always current.
        self._ch_cache:   dict[int, dict[int, dict]] = defaultdict(dict)
        self._role_cache: dict[int, dict[int, dict]] = defaultdict(dict)

        # Emoji metadata cache: guild_id → emoji_id → {name, url, animated}
        self._emoji_cache: dict[int, dict[int, dict]] = defaultdict(dict)

        # Soundboard metadata cache: guild_id → sound_id → {name, url, volume}
        self._sound_cache: dict[int, dict[int, dict]] = defaultdict(dict)

        # ── Pre-downloaded asset bytes for instant restoration ─────────────────
        # Populated by Layer-1 listeners the moment a delete is detected.
        # (guild_id, asset_id) → raw bytes ready for re-upload.
        self._emoji_bytes: dict[tuple[int, int], bytes] = {}
        self._sound_bytes: dict[tuple[int, int], bytes] = {}

        # ── Rate-limit / abuse trackers ────────────────────────────────────────
        # Rogue-whitelist: counts BANS by whitelisted users (20 bans / 1 hour).
        self._wl_ban_tracker:  dict[tuple[int, int], list[float]] = defaultdict(list)

        # Bypass-threshold: counts all rate-limited actions by bypass users.
        self._bypass_tracker:  dict[tuple[int, int], list[float]] = defaultdict(list)

        # Legacy rogue-admin tracker (still used for the configurable threshold).
        self._nuke_tracker:    dict[tuple[int, int], list[float]] = defaultdict(list)

        # ── Dedup guards ───────────────────────────────────────────────────────
        # _punished: per-user, prevents punishing the same person twice in 30 s.
        self._punished: set[tuple[int, int]] = set()

        # _seen_events: per-event, prevents double-processing the same audit log
        #               entry if the gateway somehow delivers it twice.
        self._seen_events: set[tuple[int, int, int]] = set()

    # ══════════════════════════════════════════════════════════════════════════
    #  Serialisation helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _serialize_overwrites(self, ch: discord.abc.GuildChannel) -> dict:
        """Encode a channel's permission overwrites as a JSON-friendly dict."""
        result: dict = {}
        for target, ow in ch.overwrites.items():
            allow, deny = ow.pair()
            key = f"{'role' if isinstance(target, discord.Role) else 'member'}:{target.id}"
            result[key] = {"allow": allow.value, "deny": deny.value}
        return result

    def _serialize_channel(self, ch: discord.abc.GuildChannel) -> dict:
        data: dict = {
            "name":        ch.name,
            "type":        ch.type.value,
            "position":    ch.position,
            "category_id": ch.category_id,
            "overwrites":  self._serialize_overwrites(ch),
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
            "name":        role.name,
            "color":       role.color.value,
            "hoist":       role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position":    role.position,
            "members":     [m.id for m in role.members],
        }

    def _deserialize_overwrites(self, guild: discord.Guild, raw: dict) -> dict:
        """Reconstruct a permission-overwrite dict from the serialised form."""
        result: dict = {}
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

    # ══════════════════════════════════════════════════════════════════════════
    #  Cache initialisation helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _cache_guild_channels_and_roles(self, guild: discord.Guild) -> None:
        for ch in guild.channels:
            self._ch_cache[guild.id][ch.id] = self._serialize_channel(ch)
        for role in guild.roles:
            if not role.is_default():
                self._role_cache[guild.id][role.id] = self._serialize_role(role)

    def _cache_guild_emojis(self, guild: discord.Guild) -> None:
        for emoji in guild.emojis:
            self._emoji_cache[guild.id][emoji.id] = {
                "name":     emoji.name,
                "url":      str(emoji.url),
                "animated": emoji.animated,
            }

    async def _cache_guild_soundboard(self, guild: discord.Guild) -> None:
        """Fetch soundboard sounds via REST and store in _sound_cache."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{DISCORD_API}/guilds/{guild.id}/soundboard-sounds"
                async with session.get(
                    url, headers={"Authorization": f"Bot {self.bot.http.token}"}
                ) as r:
                    if r.status != 200:
                        return
                    payload = await r.json()
                    sounds = payload if isinstance(payload, list) else payload.get("items", [])
            for s in sounds:
                sid = int(s["sound_id"])
                self._sound_cache[guild.id][sid] = {
                    "name":   s["name"],
                    "url":    f"{CDN}/soundboard-sounds/{sid}",
                    "volume": s.get("volume", 1.0),
                }
        except Exception as exc:
            log.debug("Soundboard cache failed for guild %s: %s", guild.id, exc)

    # ══════════════════════════════════════════════════════════════════════════
    #  Layer-1: Gateway listeners — keep caches fresh & pre-download assets
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_ready(self):
        tasks = []
        for guild in self.bot.guilds:
            self._cache_guild_channels_and_roles(guild)
            self._cache_guild_emojis(guild)
            tasks.append(self._cache_guild_soundboard(guild))
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("Anti-nuke cache ready for %d guild(s).", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._cache_guild_channels_and_roles(guild)
        self._cache_guild_emojis(guild)
        await self._cache_guild_soundboard(guild)

    # ── Channel / role update listeners (keep snapshots current) ──────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch: discord.abc.GuildChannel):
        self._ch_cache[ch.guild.id][ch.id] = self._serialize_channel(ch)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, _before, after: discord.abc.GuildChannel):
        self._ch_cache[after.guild.id][after.id] = self._serialize_channel(after)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, ch: discord.abc.GuildChannel):
        """
        Layer-1 channel delete.
        The channel object still holds full data here (name, type, overwrites,
        category, etc.).  If the cache missed the channel for any reason we
        re-serialise from the live object before it becomes stale.
        """
        gid = ch.guild.id
        if ch.id not in self._ch_cache.get(gid, {}):
            self._ch_cache[gid][ch.id] = self._serialize_channel(ch)
        # Layer 2 (on_audit_log_entry_create) will pick this up and act on it.

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not role.is_default():
            self._role_cache[role.guild.id][role.id] = self._serialize_role(role)

    @commands.Cog.listener()
    async def on_guild_role_update(self, _before, after: discord.Role):
        if not after.is_default():
            self._role_cache[after.guild.id][after.id] = self._serialize_role(after)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        """
        Layer-1 role delete.  Same pattern as channel delete — snapshot if missed.
        """
        gid = role.guild.id
        if role.id not in self._role_cache.get(gid, {}):
            self._role_cache[gid][role.id] = self._serialize_role(role)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """
        Keep role member-lists in _role_cache accurate whenever a member's
        roles change.

        This is critical for role restoration: `_serialize_role()` at snapshot
        time captures `role.members`, but that list changes as members gain /
        lose the role between the snapshot and a delete event.  Patching the
        cache on every `on_member_update` keeps the member roster current so
        the restoration always re-assigns exactly the right people.
        """
        if before.roles == after.roles:
            return

        gid    = after.guild.id
        gained = set(after.roles) - set(before.roles)
        lost   = set(before.roles) - set(after.roles)

        for role in gained | lost:
            if role.is_default():
                continue
            cached = self._role_cache.get(gid, {}).get(role.id)
            if cached is not None:
                # Patch just the member list — avoid re-serialising the entire role.
                cached["members"] = [m.id for m in role.members]

    # ── Emoji update listener — Layer-1 emoji detection & pre-download ─────────

    @commands.Cog.listener()
    async def on_guild_emojis_update(
        self,
        guild: discord.Guild,
        before: tuple[discord.Emoji, ...],
        after:  tuple[discord.Emoji, ...],
    ):
        """
        Layer-1 emoji detection.
        Fires the instant Discord pushes GUILD_EMOJIS_UPDATE.
        1. Update the emoji metadata cache with the current set.
        2. Identify deleted emojis (in before, not in after).
        3. For each deleted emoji: pre-download from CDN so restoration
           bytes are ready the moment Layer 2 confirms a nuke.
        """
        # Refresh metadata cache from the new 'after' state.
        self._emoji_cache[guild.id] = {
            e.id: {"name": e.name, "url": str(e.url), "animated": e.animated}
            for e in after
        }

        # Detect deleted emojis (present before, absent after).
        after_ids = {e.id for e in after}
        deleted   = [e for e in before if e.id not in after_ids]

        for emoji in deleted:
            # Pre-download the image bytes in a background task.
            asyncio.create_task(
                self._predownload_emoji(guild.id, emoji.id, str(emoji.url))
            )

    async def _predownload_emoji(self, guild_id: int, emoji_id: int, url: str) -> None:
        """Download emoji bytes from CDN and cache them for immediate restoration."""
        key = (guild_id, emoji_id)
        if key in self._emoji_bytes:
            return  # already have it
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        self._emoji_bytes[key] = await r.read()
                        log.debug("Pre-downloaded emoji %s for guild %s", emoji_id, guild_id)
        except Exception as exc:
            log.debug("Emoji pre-download failed (%s, %s): %s", guild_id, emoji_id, exc)

    # ── Soundboard delete listener — Layer-1 sound detection & pre-download ────

    @commands.Cog.listener()
    async def on_guild_soundboard_sound_delete(self, sound) -> None:  # type: ignore[override]
        """
        Layer-1 soundboard sound detection.
        Fires the instant Discord pushes GUILD_SOUNDBOARD_SOUND_DELETE.
        Saves metadata then pre-downloads the audio from CDN.
        """
        try:
            guild_id = getattr(sound, "guild_id", None) or getattr(sound.guild, "id", None)
            sound_id = int(sound.sound_id)
        except Exception:
            return

        # Snapshot metadata if not already cached.
        if sound_id not in self._sound_cache.get(guild_id, {}):
            self._sound_cache[guild_id][sound_id] = {
                "name":   sound.name,
                "url":    f"{CDN}/soundboard-sounds/{sound_id}",
                "volume": getattr(sound, "volume", 1.0),
            }

        # Pre-download audio bytes.
        asyncio.create_task(
            self._predownload_sound(guild_id, sound_id,
                                   f"{CDN}/soundboard-sounds/{sound_id}")
        )

    async def _predownload_sound(self, guild_id: int, sound_id: int, url: str) -> None:
        """Download soundboard audio bytes and cache them for restoration."""
        key = (guild_id, sound_id)
        if key in self._sound_bytes:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        self._sound_bytes[key] = await r.read()
                        log.debug("Pre-downloaded sound %s for guild %s", sound_id, guild_id)
        except Exception as exc:
            log.debug("Sound pre-download failed (%s, %s): %s", guild_id, sound_id, exc)

    # ══════════════════════════════════════════════════════════════════════════
    #  Layer-2: Audit-log gateway event — executor identification & action
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        """
        Layer-2 detection.  Fired by the GUILD_AUDIT_LOG_ENTRY_CREATE gateway
        event — no REST poll, ~0 ms additional latency over the action itself.

        Decision tree:
          1. Ignore non-nuke actions and bot executors.
          2. Whitelisted users: only run the 20-ban / 1-hour abuse check.
          3. Bypass users: run the 60 % threshold check.
          4. Everyone else: punish + restore.
        """
        t0 = time.perf_counter()

        if entry.action not in NUKE_ACTIONS:
            return

        user = entry.user
        if user is None or user.bot:
            return

        guild = entry.guild
        cfg   = db.get_config().get("antinuke", {})
        if not cfg.get("enabled", True):
            return

        # ── Dedup: skip if already processed (e.g. duplicate gateway dispatch) ─
        target_id  = entry.target.id if entry.target else 0
        action_val = entry.action.value
        event_key  = (guild.id, target_id, action_val)
        if event_key in self._seen_events:
            return
        self._seen_events.add(event_key)
        self.bot.loop.call_later(SEEN_TTL, lambda: self._seen_events.discard(event_key))

        # ── Whitelisted users ─────────────────────────────────────────────────
        if db.is_whitelisted(user.id):
            # Only bans count toward the rogue-whitelist limit.
            if entry.action == discord.AuditLogAction.ban:
                await self._check_wl_ban_limit(guild, user)
            # Also run the legacy rogue-admin check for all rate-limited actions.
            if entry.action in RATE_LIMIT_ACTIONS:
                await self._check_rogue_admin(guild, user, cfg)
            return

        # ── Bypass users (60 % threshold) ─────────────────────────────────────
        if is_bypassed(user.id):
            if entry.action in RATE_LIMIT_ACTIONS:
                await self._check_bypass_threshold(guild, user, cfg)
            return

        # ── Non-whitelisted / non-bypass: punish + restore ────────────────────
        key = (guild.id, user.id)
        if key in self._punished:
            return  # Already punishing this user — skip duplicate event.
        self._punished.add(key)
        self.bot.loop.call_later(SEEN_TTL, lambda: self._punished.discard(key))

        member = guild.get_member(user.id)

        # ── For channel / role deletes: run punishment and restoration in
        #    parallel (both are fast, neither depends on the other).
        #    Both produce their own log embeds with full detail.
        # ── For all other nuke actions: punish first, then log.

        if entry.action == discord.AuditLogAction.channel_delete:
            restore_task = asyncio.create_task(
                self._restore_channel(guild, entry, t0, user, member, cfg)
            )
            await self._punish(guild, member, user, cfg,
                               "Unauthorized channel deletion",
                               trigger_action=entry.action)
            await restore_task

        elif entry.action == discord.AuditLogAction.role_delete:
            restore_task = asyncio.create_task(
                self._restore_role(guild, entry, t0, user, member, cfg)
            )
            await self._punish(guild, member, user, cfg,
                               "Unauthorized role deletion",
                               trigger_action=entry.action)
            await restore_task

        elif entry.action == discord.AuditLogAction.emoji_delete:
            restore_task = asyncio.create_task(
                self._restore_emoji(guild, entry, t0, user)
            )
            await self._punish(guild, member, user, cfg,
                               "Unauthorized emoji deletion",
                               trigger_action=entry.action)
            await restore_task

        elif _SB_DELETE and entry.action == _SB_DELETE:
            restore_task = asyncio.create_task(
                self._restore_soundboard_sound(guild, entry, t0, user)
            )
            await self._punish(guild, member, user, cfg,
                               "Unauthorized soundboard deletion",
                               trigger_action=entry.action)
            await restore_task

        else:
            # Ban / kick / webhook_create / member_prune — no object to restore.
            await self._punish(guild, member, user, cfg,
                               f"Unauthorized {entry.action.name}",
                               trigger_action=entry.action)

    # ══════════════════════════════════════════════════════════════════════════
    #  Abuse / threshold checks
    # ══════════════════════════════════════════════════════════════════════════

    async def _check_wl_ban_limit(self, guild: discord.Guild, user: discord.User) -> None:
        """
        Rogue-whitelist rule: ≥ 20 bans by a whitelisted user within 1 hour
        → revoke whitelist + strip all roles immediately.
        """
        key = (guild.id, user.id)
        now = time.time()

        self._wl_ban_tracker[key].append(now)
        self._wl_ban_tracker[key] = [
            ts for ts in self._wl_ban_tracker[key] if now - ts <= WL_BAN_WINDOW
        ]
        count = len(self._wl_ban_tracker[key])

        if count < WL_BAN_LIMIT:
            return

        # Threshold breached — revoke whitelist and strip roles.
        self._wl_ban_tracker[key] = []
        db.remove_whitelist(user.id)

        member = guild.get_member(user.id)
        if member:
            try:
                safe = [r for r in member.roles if r.is_default()]
                await member.edit(
                    roles=safe,
                    reason=f"[Guardian] Rogue-whitelist: {WL_BAN_LIMIT} bans in {WL_BAN_WINDOW//3600}h"
                )
            except Exception as exc:
                log.error("Failed to strip rogue-whitelist roles for %s: %s", user.id, exc)

        log.warning(
            "Rogue-whitelist triggered: %s (%d) — %d bans in 1 hour in guild '%s'",
            user, user.id, count, guild.name,
        )

        embed = embeds.danger(
            "🚨  Rogue Whitelisted User Detected",
            f"{user.mention} performed `{count}` bans within `1 hour`.\n"
            f"**Whitelist revoked** and **all roles stripped** immediately.",
        )
        await self._send_log(guild, embed)

        await notifications.dm_warn_user(
            self.bot, user, guild.name,
            f"Rogue-whitelist threshold exceeded: {count} bans in 1 hour"
        )
        await notifications.dm_owner_alert(
            self.bot,
            "🚨  Rogue Whitelisted User — Whitelist Revoked",
            (
                f"**Guild:** {guild.name} (`{guild.id}`)\n"
                f"**User:** {user.mention} (`{user.id}`) — {user}\n"
                f"**Trigger:** {count} bans within 1 hour\n"
                f"**Action:** Whitelist revoked + all roles stripped"
            ),
        )

    async def _check_rogue_admin(
        self, guild: discord.Guild, user: discord.User, cfg: dict
    ) -> None:
        """
        Legacy rogue-admin check (applies to whitelisted users).
        Uses the configurable threshold + interval from the antinuke config.
        """
        threshold = cfg.get("threshold", 3)
        interval  = cfg.get("interval", 10)
        key       = (guild.id, user.id)
        now       = time.time()

        self._nuke_tracker[key].append(now)
        self._nuke_tracker[key] = [
            ts for ts in self._nuke_tracker[key] if now - ts <= interval
        ]

        if len(self._nuke_tracker[key]) < threshold:
            return

        self._nuke_tracker[key] = []
        member = guild.get_member(user.id)
        if member:
            try:
                safe = [r for r in member.roles if r.is_default() or r >= guild.me.top_role]
                await member.edit(roles=safe, reason="[Guardian] Rogue admin: destructive action rate exceeded")
            except Exception as exc:
                log.error("Failed to demote rogue admin: %s", exc)

        embed = embeds.danger(
            "⚠️  Rogue Admin Detected",
            f"{user.mention} performed `{threshold}+` destructive actions in `{interval}s`.\n"
            "All roles have been stripped immediately.",
        )
        await self._send_log(guild, embed)

        await notifications.dm_warn_user(
            self.bot, user, guild.name,
            f"Rogue admin threshold exceeded: {threshold}+ destructive actions in {interval}s"
        )
        await notifications.dm_owner_alert(
            self.bot,
            "⚠️  Rogue Admin Detected",
            (
                f"**Guild:** {guild.name} (`{guild.id}`)\n"
                f"**Rogue Admin:** {user.mention} (`{user.id}`) — {user}\n"
                f"**Trigger:** {threshold}+ destructive actions in {interval}s\n"
                f"**Action:** All roles stripped"
            ),
        )

    async def _check_bypass_threshold(
        self, guild: discord.Guild, user: discord.User, cfg: dict
    ) -> None:
        """
        Bypass 60 % rule.
        Bypass users are trusted up to 60 % of the configured threshold.
        Once they hit that mark within the configured interval, all their
        roles are stripped immediately.  Their bypass status is not revoked
        (an owner can re-evaluate), but the action is logged and the owners
        are alerted.
        """
        threshold      = cfg.get("threshold", 3)
        interval       = cfg.get("interval", 10)
        bypass_limit   = max(1, int(threshold * BYPASS_RATIO))
        key            = (guild.id, user.id)
        now            = time.time()

        self._bypass_tracker[key].append(now)
        self._bypass_tracker[key] = [
            ts for ts in self._bypass_tracker[key] if now - ts <= interval
        ]
        count = len(self._bypass_tracker[key])

        if count < bypass_limit:
            return

        # Bypass threshold breached — strip roles.
        self._bypass_tracker[key] = []
        member = guild.get_member(user.id)
        if member:
            try:
                safe = [r for r in member.roles if r.is_default()]
                await member.edit(
                    roles=safe,
                    reason=f"[Guardian] Bypass 60% threshold: {count}/{threshold} actions in {interval}s"
                )
            except Exception as exc:
                log.error("Failed to strip bypass-user roles for %s: %s", user.id, exc)

        log.warning(
            "Bypass 60%% threshold triggered: %s (%d) — %d/%d actions in %ds in guild '%s'",
            user, user.id, count, threshold, interval, guild.name,
        )

        embed = embeds.danger(
            "⚠️  Bypass User — Threshold Exceeded (60 %)",
            f"{user.mention} (`{user.id}`) performed `{count}` destructive actions "
            f"(≥ `{bypass_limit}` = 60 % of threshold `{threshold}`) in `{interval}s`.\n"
            "All roles have been stripped. Bypass status retained — review manually.",
        )
        await self._send_log(guild, embed)

        await notifications.dm_owner_alert(
            self.bot,
            "⚠️  Bypass User Exceeded 60 % Threshold",
            (
                f"**Guild:** {guild.name} (`{guild.id}`)\n"
                f"**User:** {user.mention} (`{user.id}`) — {user}\n"
                f"**Actions:** {count} in {interval}s (threshold × 60 % = {bypass_limit})\n"
                f"**Action:** All roles stripped (bypass status unchanged)"
            ),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  Punishment
    #  Returns a result dict for inclusion in log embeds.
    # ══════════════════════════════════════════════════════════════════════════

    async def _punish(
        self,
        guild:          discord.Guild,
        member:         discord.Member | None,
        user:           discord.User,
        cfg:            dict,
        reason:         str,
        trigger_action: discord.AuditLogAction | None = None,
    ) -> dict:
        """
        Apply the configured punishment to the executor and send a dedicated
        punishment log embed.

        Returns a dict:
          {
            "effective_action": str,   # "ban" / "kick" / "strip"
            "success":          bool,
            "hierarchy_block":  bool,  # True if executor top role ≥ bot top role
            "fail_reason":      str | None,
          }

        Special rule — Ban → Kick:
          If the malicious action was an unauthorized BAN, the executor is
          KICKED instead of banned.  Banning a user who just banned someone
          would leave the victim still banned; kicking removes the threat
          without that complication.

        Hierarchy safety:
          Before attempting role-strip or kick, the bot checks whether the
          executor's top role is ≥ its own top role.  If so, role-stripping
          is skipped (it would raise Forbidden), and every relevant path
          catches discord.Forbidden explicitly so a partial failure never
          crashes the flow.  The hierarchy_block flag is passed into the
          punishment embed so it appears as a visible ⚠️ warning.

          Note: banning does NOT require role hierarchy — only BAN_MEMBERS
          permission.  So a ban is still attempted even when hierarchy_block
          is True (the bot may still have BAN_MEMBERS over a higher-ranked
          member if the guild owner set it up that way).
        """
        configured_action = cfg.get("action", "ban")
        full_reason       = f"[Guardian] Anti-Nuke: {reason}"

        # Determine the actual punishment.
        if trigger_action == discord.AuditLogAction.ban:
            effective_action = "kick"   # Always kick if the trigger was a ban.
        else:
            effective_action = configured_action

        # ── Hierarchy detection ────────────────────────────────────────────────
        hierarchy_block = False
        if member is not None:
            bot_top = guild.me.top_role
            usr_top = member.top_role
            if usr_top >= bot_top:
                hierarchy_block = True
                log.warning(
                    "Hierarchy block: executor %s (%d) top role '%s' (pos %d) >= bot top '%s' (pos %d) in '%s'",
                    user, user.id, usr_top.name, usr_top.position,
                    bot_top.name, bot_top.position, guild.name,
                )

        # ── DM notifications ───────────────────────────────────────────────────
        await notifications.dm_warn_user(self.bot, user, guild.name, reason)
        await notifications.dm_owner_alert(
            self.bot,
            "🛡️  Anti-Nuke — Security Action Taken",
            (
                f"**Guild:** {guild.name} (`{guild.id}`)\n"
                f"**Perpetrator:** {user.mention} (`{user.id}`) — {user}\n"
                f"**Trigger action:** `{trigger_action.name if trigger_action else 'unknown'}`\n"
                f"**Punishment applied:** `{effective_action}`\n"
                f"**Hierarchy blocked:** `{hierarchy_block}`\n"
                f"**Reason:** {reason}"
            ),
        )

        success     = False
        fail_reason = None

        # ── Apply punishment ───────────────────────────────────────────────────
        if member is None:
            # Member already left — only ban can still work (by user ID).
            if effective_action == "ban":
                try:
                    await guild.ban(discord.Object(id=user.id), reason=full_reason)
                    success = True
                except discord.Forbidden:
                    fail_reason = "Missing Permissions"
                except Exception as exc:
                    fail_reason = str(exc)[:80]
            else:
                fail_reason = "Member not in guild"

        elif effective_action == "ban":
            # Ban ignores role hierarchy — attempt regardless of hierarchy_block.
            try:
                await member.ban(reason=full_reason)
                success = True
            except discord.Forbidden:
                # No BAN_MEMBERS permission — fall back to role strip.
                try:
                    strippable = [r for r in member.roles
                                  if not r.is_default() and r < guild.me.top_role]
                    await member.edit(roles=[], reason=full_reason)
                    success     = True
                    fail_reason = "Ban blocked (Forbidden) — roles stripped instead"
                except discord.Forbidden:
                    fail_reason = "Ban + role-strip both blocked (Forbidden)"
                except Exception as exc2:
                    fail_reason = str(exc2)[:80]
            except Exception as exc:
                fail_reason = str(exc)[:80]

        elif effective_action == "kick":
            if hierarchy_block:
                # Kick requires role hierarchy — skip if blocked; strip what we can.
                fail_reason = "Kick blocked — executor above bot in role hierarchy"
                try:
                    strippable = [r for r in member.roles
                                  if not r.is_default() and r < guild.me.top_role]
                    if strippable:
                        keep = [r for r in member.roles if r not in strippable]
                        await member.edit(roles=keep, reason=f"{full_reason} (partial strip, kick blocked)")
                        fail_reason += " — partial role strip applied"
                        success = True   # Partial success.
                except discord.Forbidden:
                    pass
                except Exception as exc:
                    fail_reason = str(exc)[:80]
            else:
                try:
                    await member.kick(reason=full_reason)
                    success = True
                except discord.Forbidden:
                    fail_reason = "Kick blocked (Forbidden)"
                    # Attempt role strip as fallback.
                    try:
                        await member.edit(roles=[], reason=full_reason)
                        success     = True
                        fail_reason = "Kick blocked (Forbidden) — roles stripped instead"
                    except discord.Forbidden:
                        fail_reason = "Kick + role-strip both blocked (Forbidden)"
                    except Exception as exc2:
                        fail_reason = str(exc2)[:80]
                except Exception as exc:
                    fail_reason = str(exc)[:80]

        else:   # "strip" / any other configured value
            if hierarchy_block:
                # Only strip roles that are below the bot's top role.
                try:
                    strippable = [r for r in member.roles
                                  if not r.is_default() and r < guild.me.top_role]
                    if strippable:
                        keep = [r for r in member.roles if r not in strippable]
                        await member.edit(roles=keep, reason=f"{full_reason} (partial — hierarchy)")
                        success     = True
                        fail_reason = f"Partial strip — {len(strippable)} role(s) below bot removed; higher roles kept"
                    else:
                        fail_reason = "No strippable roles below bot's top role"
                except discord.Forbidden:
                    fail_reason = "Role-strip blocked (Forbidden) — hierarchy"
                except Exception as exc:
                    fail_reason = str(exc)[:80]
            else:
                try:
                    await member.edit(roles=[], reason=full_reason)
                    success = True
                except discord.Forbidden:
                    fail_reason = "Role-strip blocked (Forbidden)"
                except Exception as exc:
                    fail_reason = str(exc)[:80]

        result = {
            "effective_action": effective_action,
            "success":          success,
            "hierarchy_block":  hierarchy_block,
            "fail_reason":      fail_reason,
        }

        # ── Send punishment log embed ──────────────────────────────────────────
        p_embed = _punishment_embed(
            actor=user,           # `user` is the local name for the executor
            actor_member=member,
            guild=guild,
            trigger_action=trigger_action,
            effective_action=effective_action,
            success=success,
            hierarchy_block=hierarchy_block,
            fail_reason=fail_reason,
        )
        await self._send_log(guild, p_embed)

        log.info(
            "Punishment for %s (%d) in '%s': action=%s success=%s hierarchy=%s%s",
            user, user.id, guild.name,
            effective_action, success, hierarchy_block,
            f" reason={fail_reason!r}" if fail_reason else "",
        )
        return result

    # ══════════════════════════════════════════════════════════════════════════
    #  Restoration — Channels
    # ══════════════════════════════════════════════════════════════════════════

    async def _restore_channel(
        self,
        guild:  discord.Guild,
        entry:  discord.AuditLogEntry,
        t0:     float,
        actor:  discord.User,
        member: discord.Member | None,
        cfg:    dict,
    ) -> None:
        """
        Perfectly reconstruct the deleted channel and send a detailed log embed.

        What is restored:
          • Name, channel type, position, parent category
          • Topic, NSFW flag, slowmode delay (text channels)
          • Bitrate, user limit (voice channels)
          • Every permission overwrite (role and member-level allow + deny bits)

        The log embed includes:
          • Executor Name#discriminator + ID
          • Deleted channel name + old snowflake ID
          • Channel type, permission overwrite count
          • New channel mention (if successful)
          • Restoration status: ✅ Restored / ❌ Failed with elapsed time
          • Exact timestamp (Discord format_dt)
          • ⚠️ Hierarchy Warning (if executor top role ≥ bot top role)
        """
        ch_id = entry.target.id if entry.target else None
        if not ch_id:
            return

        data = self._ch_cache.get(guild.id, {}).get(ch_id)
        if not data:
            log.warning("Channel cache miss for id=%d — cannot restore", ch_id)
            # Send a failure embed so the log channel still gets an entry.
            err_embed = _channel_restore_embed(
                data={"name": f"(unknown — id {ch_id})", "type": 0, "overwrites": {}},
                ch_id=ch_id,
                new_ch=None,
                actor=actor,
                actor_member=member,
                guild=guild,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                error="Cache miss — channel data was not captured before deletion",
                ow_count=0,
            )
            await self._send_log(guild, err_embed)
            return

        new_ch:    discord.abc.GuildChannel | None = None
        error:     str | None = None
        ow_count   = len(data.get("overwrites", {}))

        try:
            ch_type    = discord.ChannelType(data["type"])
            overwrites = self._deserialize_overwrites(guild, data.get("overwrites", {}))
            cat        = guild.get_channel(data["category_id"]) if data.get("category_id") else None
            category   = cat if isinstance(cat, discord.CategoryChannel) else None

            base_kw: dict = dict(
                name=data["name"],
                overwrites=overwrites,
                reason="[Guardian] Anti-Nuke: Perfect auto-restore",
            )
            if category:
                base_kw["category"] = category

            if ch_type == discord.ChannelType.text:
                base_kw.update(
                    topic=data.get("topic") or "",
                    slowmode_delay=data.get("slowmode_delay", 0),
                    nsfw=data.get("nsfw", False),
                )
                new_ch = await guild.create_text_channel(**base_kw)

            elif ch_type == discord.ChannelType.voice:
                base_kw.update(
                    bitrate=min(data.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=data.get("user_limit", 0),
                )
                new_ch = await guild.create_voice_channel(**base_kw)

            elif ch_type == discord.ChannelType.category:
                new_ch = await guild.create_category(
                    name=data["name"],
                    overwrites=overwrites,
                    reason=base_kw["reason"],
                )

            elif ch_type == discord.ChannelType.forum:
                base_kw.update(topic=data.get("topic") or "")
                new_ch = await guild.create_forum(**base_kw)

            elif ch_type == discord.ChannelType.stage_voice:
                new_ch = await guild.create_stage_channel(**base_kw)

            else:
                # Fallback for unknown types — recreate as text.
                new_ch = await guild.create_text_channel(**base_kw)

            # Best-effort position restore.
            try:
                await new_ch.edit(position=data["position"])
            except Exception:
                pass

            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.info(
                "Restored #%s (was id=%d) in guild '%s' — %d OWs — %.1fms",
                data["name"], ch_id, guild.name, ow_count, elapsed_ms,
            )

        except discord.Forbidden as exc:
            error      = f"Missing Permissions — {exc}"
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.error("Channel restore Forbidden for guild %s: %s", guild.id, exc)

        except Exception as exc:
            error      = str(exc)[:120]
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.error("Channel restore failed for guild %s: %s", guild.id, exc)

        embed = _channel_restore_embed(
            data=data,
            ch_id=ch_id,
            new_ch=new_ch,
            actor=actor,
            actor_member=member,
            guild=guild,
            elapsed_ms=elapsed_ms,
            error=error,
            ow_count=ow_count,
        )
        await self._send_log(guild, embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  Restoration — Roles
    # ══════════════════════════════════════════════════════════════════════════

    async def _restore_role(
        self,
        guild:  discord.Guild,
        entry:  discord.AuditLogEntry,
        t0:     float,
        actor:  discord.User,
        member: discord.Member | None,
        cfg:    dict,
    ) -> None:
        """
        Perfectly reconstruct the deleted role AND re-assign it to every
        member who originally held it (batched in groups of 15 to avoid
        saturating the rate limiter).

        What is restored:
          • Name, color, hoist flag, mentionable flag, permissions
          • Full member roster (every original holder receives the new role)

        The log embed includes:
          • Executor Name#discriminator + ID
          • Deleted role name + old snowflake ID
          • Color hex, permissions integer
          • Members reassigned: N / M
          • Restoration status: ✅ Restored / ❌ Failed with elapsed time
          • Exact timestamp
          • ⚠️ Hierarchy Warning (if applicable)
        """
        role_id = entry.target.id if entry.target else None
        if not role_id:
            return

        data = self._role_cache.get(guild.id, {}).get(role_id)
        if not data:
            log.warning("Role cache miss for id=%d — cannot restore", role_id)
            err_embed = _role_restore_embed(
                data={"name": f"(unknown — id {role_id})", "color": 0, "permissions": 0},
                role_id=role_id,
                new_role=None,
                actor=actor,
                actor_member=member,
                guild=guild,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                error="Cache miss — role data was not captured before deletion",
                reassigned=0,
                total=0,
            )
            await self._send_log(guild, err_embed)
            return

        new_role:   discord.Role | None = None
        error:      str | None = None
        reassigned  = 0
        member_ids: list[int] = data.get("members", [])

        try:
            new_role = await guild.create_role(
                name=data["name"],
                color=discord.Color(data["color"]),
                hoist=data["hoist"],
                mentionable=data["mentionable"],
                permissions=discord.Permissions(data["permissions"]),
                reason="[Guardian] Anti-Nuke: Perfect auto-restore",
            )

            # Best-effort position restore (roles are created at the bottom;
            # move it back to its original position so channel overwrites still
            # make visual sense in the role list).
            try:
                target_pos = max(1, data.get("position", 1))
                await new_role.edit(position=target_pos)
            except Exception:
                pass  # Position edit can fail if hierarchy prevents it; not fatal.

            # Re-assign to every original member (batched, 350 ms between batches).
            async def _assign(mid: int) -> None:
                nonlocal reassigned
                m = guild.get_member(mid)
                if m:
                    try:
                        await m.add_roles(new_role, reason="[Guardian] Role auto-restore")
                        reassigned += 1
                    except discord.Forbidden:
                        log.warning(
                            "Forbidden adding role '%s' to member %d in guild '%s'",
                            new_role.name, mid, guild.name,
                        )
                    except Exception:
                        pass

            batch_size = 15
            for i in range(0, len(member_ids), batch_size):
                await asyncio.gather(*[_assign(mid) for mid in member_ids[i:i + batch_size]])
                if i + batch_size < len(member_ids):
                    await asyncio.sleep(DELETE_DELAY)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.info(
                "Restored @%s (was id=%d) in '%s', reassigned %d/%d members — %.1fms",
                data["name"], role_id, guild.name, reassigned, len(member_ids), elapsed_ms,
            )

        except discord.Forbidden as exc:
            error      = f"Missing Permissions — {exc}"
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.error("Role restore Forbidden for guild %s: %s", guild.id, exc)

        except Exception as exc:
            error      = str(exc)[:120]
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.error("Role restore failed for guild %s: %s", guild.id, exc)

        embed = _role_restore_embed(
            data=data,
            role_id=role_id,
            new_role=new_role,
            actor=actor,
            actor_member=member,
            guild=guild,
            elapsed_ms=elapsed_ms,
            error=error,
            reassigned=reassigned,
            total=len(member_ids),
        )
        await self._send_log(guild, embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  Restoration — Emojis
    # ══════════════════════════════════════════════════════════════════════════

    async def _restore_emoji(
        self,
        guild: discord.Guild,
        entry: discord.AuditLogEntry,
        t0:    float,
        actor: discord.User,
    ) -> None:
        """
        Re-upload the deleted emoji.

        Layer-1 pre-downloaded the bytes in _emoji_bytes the instant the
        GUILD_EMOJIS_UPDATE event arrived.  If that pre-download finished
        (typical case), restoration is instant.  Otherwise we attempt a fresh
        CDN fetch here as a fallback.
        """
        emoji_id = entry.target.id if entry.target else None
        if not emoji_id:
            return

        data = self._emoji_cache.get(guild.id, {}).get(emoji_id)
        if not data:
            log.warning("Emoji cache miss for id=%d — cannot restore", emoji_id)
            return

        img_bytes = self._emoji_bytes.pop((guild.id, emoji_id), None)

        if img_bytes is None:
            # Pre-download wasn't ready yet — try a direct CDN fetch.
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        data["url"], timeout=aiohttp.ClientTimeout(total=10)
                    ) as r:
                        if r.status == 200:
                            img_bytes = await r.read()
            except Exception as exc:
                log.warning("Emoji CDN fallback fetch failed for %d: %s", emoji_id, exc)

        if not img_bytes:
            log.error(
                "No bytes available for emoji '%s' (%d) — skipping restore",
                data["name"], emoji_id,
            )
            return

        try:
            new_emoji = await guild.create_custom_emoji(
                name=data["name"],
                image=img_bytes,
                reason="[Guardian] Anti-Nuke: Emoji auto-restore",
            )
            elapsed = time.perf_counter() - t0
            kind    = "Animated GIF" if data["animated"] else "PNG"

            embed = embeds.stats(
                "🔄  Emoji Auto-Restored",
                elapsed,
                fields=[
                    ("Executor",    _executor_line(actor),                      False),
                    ("Emoji",       f":{data['name']}: (old id `{emoji_id}`)",  True),
                    ("New Emoji",   f":{new_emoji.name}: (id `{new_emoji.id}`)", True),
                    ("Format",      f"`{kind}`",                                 True),
                    ("Detected At", _now_ts(),                                   False),
                ],
            )
            await self._send_log(guild, embed)
            log.info(
                "Restored emoji :%s: in '%s' — %.1fms",
                data["name"], guild.name, elapsed * 1000,
            )

        except Exception as exc:
            log.error("Emoji restore failed for guild %s: %s", guild.id, exc)

    # ══════════════════════════════════════════════════════════════════════════
    #  Restoration — Soundboard
    # ══════════════════════════════════════════════════════════════════════════

    async def _restore_soundboard_sound(
        self,
        guild: discord.Guild,
        entry: discord.AuditLogEntry,
        t0:    float,
        actor: discord.User,
    ) -> None:
        """
        Re-upload the deleted soundboard sound.

        Uses the same pre-download strategy as emoji restoration.
        Re-upload is done via the Discord REST API (discord.py does not yet
        expose a native create-soundboard-sound method).
        """
        sound_id = entry.target.id if entry.target else None
        if not sound_id:
            return

        data = self._sound_cache.get(guild.id, {}).get(sound_id)
        if not data:
            log.warning("Soundboard cache miss for id=%d — cannot restore", sound_id)
            return

        audio_bytes = self._sound_bytes.pop((guild.id, sound_id), None)

        if audio_bytes is None:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        data["url"], timeout=aiohttp.ClientTimeout(total=15)
                    ) as r:
                        if r.status == 200:
                            audio_bytes = await r.read()
            except Exception as exc:
                log.warning("Soundboard CDN fallback fetch failed for %d: %s", sound_id, exc)

        if not audio_bytes:
            log.error(
                "No bytes for soundboard sound '%s' (%d) — skipping restore",
                data["name"], sound_id,
            )
            return

        import base64
        try:
            b64    = base64.b64encode(audio_bytes).decode()
            volume = data.get("volume", 1.0)

            async with aiohttp.ClientSession() as session:
                url     = f"{DISCORD_API}/guilds/{guild.id}/soundboard-sounds"
                payload = {
                    "name":   data["name"],
                    "sound":  f"data:audio/ogg;base64,{b64}",
                    "volume": round(volume, 2),
                }
                async with session.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bot {self.bot.http.token}"},
                ) as r:
                    ok = r.status in (200, 201)

            elapsed = time.perf_counter() - t0
            embed = embeds.stats(
                "🔄  Soundboard Sound Auto-Restored" if ok else "⚠️  Soundboard Restore Failed",
                elapsed,
                fields=[
                    ("Executor",    _executor_line(actor),            False),
                    ("Sound",       f"`{data['name']}` (id `{sound_id}`)", True),
                    ("Volume",      f"`{volume:.2f}`",                  True),
                    ("Status",      "`Restored` ✅" if ok else "`Failed` ❌", True),
                    ("Detected At", _now_ts(),                          False),
                ],
            )
            await self._send_log(guild, embed)
            log.info(
                "%s soundboard sound '%s' in '%s' — %.1fms",
                "Restored" if ok else "FAILED to restore",
                data["name"], guild.name, elapsed * 1000,
            )

        except Exception as exc:
            log.error("Soundboard restore failed for guild %s: %s", guild.id, exc)

    # ══════════════════════════════════════════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════════════════════════════════════════

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
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
