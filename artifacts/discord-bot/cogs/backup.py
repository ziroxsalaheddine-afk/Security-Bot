"""
Backup Cog — Advanced Interactive Backup System v3
════════════════════════════════════════════════════

+backup create          — snapshot server (roles, channels, emojis, soundboard) → backups/<ID>.json
+backup list            — embed listing all saved backups (ID, server, date)
+backup load <id>       — two multi-select menus (Wipe / Load) + "Validate & Start" button
+backup delete <id>     — permanently delete a saved backup

Legacy (backward compat):
+restore [guild_id]     — restore from old-style guild-id backups
+cloneroles <guild_id>  — copy roles from any saved backup

Security   : Global Owner OR Server Co-Owner only
Performance: asyncio.Semaphore + asyncio.gather for concurrent API calls
             discord.py handles 429 rate-limit backoff automatically
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

from utils import db
from utils import coowners

log = logging.getLogger("guardian.backup")

# ── Constants ──────────────────────────────────────────────────────────────────

COL         = 0x2B2D31
COL_ERR     = 0xC0392B
COL_WARN    = 0xE67E22
FOOTER      = "© 2026 — developed by zrx.gg"
BACKUPS_DIR = Path(__file__).parent.parent / "backups"
INDEX_FILE  = BACKUPS_DIR / "index.json"

HISTORY_LIMIT   = 25
EMOJI_SEM       = 2   # emoji & soundboard uploads are strictly rate-limited
GENERAL_SEM     = 6   # roles, channels, categories, deletions
DISCORD_API_VER = "v10"

BACKUPS_DIR.mkdir(exist_ok=True)


# ── Generic helpers ────────────────────────────────────────────────────────────

def _has_elevated(ctx: commands.Context) -> bool:
    return db.is_owner(ctx.author.id) or coowners.is_coowner(ctx.guild.id, ctx.author.id)


def _embed(description: str, *, color: int = COL) -> discord.Embed:
    e = discord.Embed(description=description, color=color,
                      timestamp=datetime.now(timezone.utc))
    e.set_footer(text=FOOTER)
    return e


def _generate_backup_id() -> str:
    return uuid.uuid4().hex[:8].upper()


def _backup_path(backup_id: str) -> Path:
    return BACKUPS_DIR / f"{backup_id}.json"


# ── Index management ───────────────────────────────────────────────────────────

def _load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(index: list[dict]) -> None:
    INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def _add_to_index(backup_id: str, guild_id: int, guild_name: str,
                  created_at: str) -> None:
    index = _load_index()
    index.append({"id": backup_id, "guild_id": guild_id,
                  "guild_name": guild_name, "created_at": created_at})
    _save_index(index)


def _remove_from_index(backup_id: str) -> None:
    _save_index([e for e in _load_index() if e.get("id") != backup_id])


# ── Serialization ──────────────────────────────────────────────────────────────

async def _serialize_overwrites(overwrites: dict) -> dict:
    out = {}
    for target, ow in overwrites.items():
        allow, deny = ow.pair()
        key = (f"role:{target.id}" if isinstance(target, discord.Role)
               else f"member:{target.id}")
        out[key] = {"allow": allow.value, "deny": deny.value}
    return out


def _build_overwrites(raw: dict, role_map: dict[int, discord.Role],
                      guild: discord.Guild) -> dict:
    result: dict = {}
    for key, val in raw.items():
        kind, old_id_str = key.split(":", 1)
        old_id = int(old_id_str)
        ow = discord.PermissionOverwrite.from_pair(
            discord.Permissions(val["allow"]), discord.Permissions(val["deny"])
        )
        if kind == "role":
            target = role_map.get(old_id) or (
                guild.default_role if old_id == guild.default_role.id else None)
            if target:
                result[target] = ow
        elif kind == "member":
            member = guild.get_member(old_id)
            if member:
                result[member] = ow
    return result


# ── Soundboard HTTP helpers ────────────────────────────────────────────────────
# discord.py 2.x doesn't fully expose soundboard create/delete at module level,
# so we call the Discord REST API directly using the bot token.

_BASE = f"https://discord.com/api/{DISCORD_API_VER}"


async def _sb_fetch(session: aiohttp.ClientSession, token: str,
                    guild_id: int) -> list[dict]:
    """Return list of guild soundboard sound objects."""
    url = f"{_BASE}/guilds/{guild_id}/soundboard-sounds"
    async with session.get(url, headers={"Authorization": f"Bot {token}"}) as r:
        if r.status != 200:
            log.warning("Soundboard fetch returned HTTP %s", r.status)
            return []
        payload = await r.json()
        # Discord returns {"items": [...]} or a bare list depending on version
        if isinstance(payload, list):
            return payload
        return payload.get("items", [])


async def _sb_delete(session: aiohttp.ClientSession, token: str,
                     guild_id: int, sound_id: int) -> None:
    url = f"{_BASE}/guilds/{guild_id}/soundboard-sounds/{sound_id}"
    async with session.delete(url, headers={"Authorization": f"Bot {token}"}) as r:
        if r.status not in (200, 204):
            log.warning("Soundboard delete %s returned HTTP %s", sound_id, r.status)


async def _sb_create(session: aiohttp.ClientSession, token: str,
                     guild_id: int, name: str, ogg_bytes: bytes,
                     volume: float = 1.0) -> bool:
    url = f"{_BASE}/guilds/{guild_id}/soundboard-sounds"
    b64 = base64.b64encode(ogg_bytes).decode()
    payload = {
        "name": name,
        "sound": f"data:audio/ogg;base64,{b64}",
        "volume": round(volume, 2),
    }
    async with session.post(url, json=payload,
                            headers={"Authorization": f"Bot {token}"}) as r:
        if r.status not in (200, 201):
            log.warning("Soundboard create '%s' returned HTTP %s", name, r.status)
            return False
        return True


# ── Backup creation ────────────────────────────────────────────────────────────

async def _do_backup(guild: discord.Guild, progress_msg: discord.Message,
                     bot_token: str) -> dict:
    """Snapshot an entire guild and return the data dict."""

    async def upd(lines: list[str]) -> None:
        await progress_msg.edit(embed=_embed("\n".join(lines)))

    status: list[str] = [
        "• __**Backup**__",
        f"Server: **{guild.name}**",
        "",
    ]

    # ── Metadata ──
    status.append("`⏳` Capturing metadata…")
    await upd(status)

    data: dict = {
        "meta": {
            "guild_id":     guild.id,
            "guild_name":   guild.name,
            "backed_up_at": datetime.now(timezone.utc).isoformat(),
            "icon_url":     str(guild.icon.url) if guild.icon else None,
            "member_count": guild.member_count,
        },
        "roles":      [],
        "categories": [],
        "channels":   [],
        "emojis":     [],
        "soundboard": [],
        "members":    [],
    }

    # ── Roles ──
    status[-1] = "`⏳` Capturing roles…"
    await upd(status)
    for role in sorted(guild.roles, key=lambda r: r.position):
        data["roles"].append({
            "id":          role.id,
            "name":        role.name,
            "color":       role.color.value,
            "hoist":       role.hoist,
            "mentionable": role.mentionable,
            "position":    role.position,
            "permissions": role.permissions.value,
            "is_default":  role.is_default(),
        })
    status[-1] = f"`✅` Roles: `{len(data['roles'])}`"
    status.append("`⏳` Capturing categories…")
    await upd(status)

    # ── Categories ──
    for cat in sorted(guild.categories, key=lambda c: c.position):
        data["categories"].append({
            "id":         cat.id,
            "name":       cat.name,
            "position":   cat.position,
            "overwrites": await _serialize_overwrites(cat.overwrites),
        })
    status[-1] = f"`✅` Categories: `{len(data['categories'])}`"
    status.append("`⏳` Capturing channels…")
    await upd(status)

    # ── Channels ──
    ch_count = msg_count = 0
    for ch in sorted(guild.channels, key=lambda c: c.position):
        if isinstance(ch, discord.CategoryChannel):
            continue
        cd: dict = {
            "id":          ch.id,
            "name":        ch.name,
            "type":        str(ch.type),
            "category_id": ch.category_id,
            "position":    ch.position,
            "overwrites":  await _serialize_overwrites(ch.overwrites),
            "history":     [],
        }
        if isinstance(ch, discord.TextChannel):
            cd["topic"]    = ch.topic
            cd["nsfw"]     = ch.is_nsfw()
            cd["slowmode"] = ch.slowmode_delay
            try:
                async for msg in ch.history(limit=HISTORY_LIMIT, oldest_first=False):
                    cd["history"].append({
                        "author":      str(msg.author),
                        "author_id":   msg.author.id,
                        "content":     msg.content,
                        "timestamp":   msg.created_at.isoformat(),
                        "attachments": [a.url for a in msg.attachments],
                    })
                    msg_count += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
        elif isinstance(ch, discord.VoiceChannel):
            cd["bitrate"]    = ch.bitrate
            cd["user_limit"] = ch.user_limit
        elif isinstance(ch, discord.StageChannel):
            cd["bitrate"] = ch.bitrate
        data["channels"].append(cd)
        ch_count += 1
    status[-1] = f"`✅` Channels: `{ch_count}` · Messages: `{msg_count}`"
    status.append("`⏳` Capturing emojis…")
    await upd(status)

    # ── Emojis ──
    for emoji in guild.emojis:
        data["emojis"].append({
            "name":     emoji.name,
            "url":      str(emoji.url),
            "animated": emoji.animated,
            "id":       emoji.id,
        })
    status[-1] = f"`✅` Emojis: `{len(data['emojis'])}`"
    status.append("`⏳` Capturing soundboard…")
    await upd(status)

    # ── Soundboard ──
    try:
        async with aiohttp.ClientSession() as session:
            sounds = await _sb_fetch(session, bot_token, guild.id)
        for s in sounds:
            data["soundboard"].append({
                "id":     s["sound_id"],
                "name":   s["name"],
                "volume": s.get("volume", 1.0),
                "url":    f"https://cdn.discordapp.com/soundboard-sounds/{s['sound_id']}",
            })
    except Exception as exc:
        log.warning("Soundboard capture failed (skipped): %s", exc)

    status[-1] = f"`✅` Soundboard: `{len(data['soundboard'])}`"
    status.append("`⏳` Capturing member roles…")
    await upd(status)

    # ── Members ──
    for member in guild.members:
        role_ids = [r.id for r in member.roles if not r.is_default()]
        data["members"].append({
            "id":       member.id,
            "name":     str(member),
            "role_ids": role_ids,
        })
    status[-1] = f"`✅` Members: `{len(data['members'])}`"
    await upd(status)

    return data


# ── Parallel execution helper ──────────────────────────────────────────────────

async def _par(coros: list, concurrency: int = GENERAL_SEM) -> list:
    """Run coroutines concurrently up to `concurrency` at a time.
    Returns results list (exceptions are returned, not raised)."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(coro):
        async with sem:
            return await coro

    return list(await asyncio.gather(*[_one(c) for c in coros],
                                     return_exceptions=True))


# ── Wipe helpers ───────────────────────────────────────────────────────────────

async def _wipe_channels(guild: discord.Guild, invoke_channel_id: int) -> None:
    """Delete all channels except the invocation channel (parallel)."""
    non_cats = [c for c in guild.channels
                if not isinstance(c, discord.CategoryChannel)
                and c.id != invoke_channel_id]
    cats = list(guild.categories)

    async def _del(ch: discord.abc.GuildChannel) -> None:
        try:
            await ch.delete(reason="Guardian backup wipe")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    await _par([_del(c) for c in non_cats])
    await _par([_del(c) for c in cats])


async def _wipe_roles(guild: discord.Guild) -> None:
    """Delete all deletable roles (parallel, respects bot ceiling)."""
    bot_top = guild.me.top_role.position if guild.me else 0
    deletable = [r for r in guild.roles
                 if not r.is_default() and not r.managed and r.position < bot_top]

    async def _del(role: discord.Role) -> None:
        try:
            await role.delete(reason="Guardian backup wipe")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    await _par([_del(r) for r in deletable])


async def _wipe_emojis(guild: discord.Guild) -> None:
    """Delete all custom emojis (parallel, capped at EMOJI_SEM)."""
    async def _del(emoji: discord.Emoji) -> None:
        try:
            await emoji.delete(reason="Guardian backup wipe")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    await _par([_del(e) for e in list(guild.emojis)], concurrency=EMOJI_SEM)


async def _wipe_soundboard(guild: discord.Guild, bot_token: str) -> None:
    """Delete all guild soundboard sounds (parallel)."""
    try:
        async with aiohttp.ClientSession() as session:
            sounds = await _sb_fetch(session, bot_token, guild.id)
            await _par(
                [_sb_delete(session, bot_token, guild.id, int(s["sound_id"]))
                 for s in sounds],
                concurrency=EMOJI_SEM,
            )
    except Exception as exc:
        log.warning("Soundboard wipe failed: %s", exc)


# ── Restore helpers ────────────────────────────────────────────────────────────

async def _restore_roles(guild: discord.Guild, data: dict,
                         role_map: dict[int, discord.Role]) -> int:
    """Create roles in parallel, then bulk-set exact saved positions."""
    # Update @everyone permissions
    for rd in data.get("roles", []):
        if rd.get("is_default"):
            try:
                await guild.default_role.edit(
                    permissions=discord.Permissions(rd["permissions"]))
            except (discord.Forbidden, discord.HTTPException):
                pass
            role_map[rd["id"]] = guild.default_role

    non_default = sorted(
        [r for r in data.get("roles", []) if not r.get("is_default")],
        key=lambda r: r["position"], reverse=True,
    )

    position_targets: list[tuple[discord.Role, int]] = []

    async def _create(rd: dict):
        try:
            role = await guild.create_role(
                name=rd["name"],
                color=discord.Color(rd["color"]),
                hoist=rd["hoist"],
                mentionable=rd["mentionable"],
                permissions=discord.Permissions(rd["permissions"]),
                reason="Guardian backup restore",
            )
            return (rd["id"], rd["position"], role)
        except Exception as exc:
            log.warning("Could not create role '%s': %s", rd["name"], exc)
            return None

    results = await _par([_create(rd) for rd in non_default])
    created = 0
    for res in results:
        if isinstance(res, tuple):
            old_id, pos, role = res
            role_map[old_id] = role
            position_targets.append((role, pos))
            created += 1

    # Bulk-apply exact saved positions
    if position_targets:
        bot_ceiling = (guild.me.top_role.position - 1) if guild.me else 999
        positions = {
            role: max(1, min(pos, bot_ceiling))
            for role, pos in position_targets
        }
        try:
            await guild.edit_role_positions(
                positions=positions, reason="Guardian backup restore — hierarchy")
        except Exception as exc:
            log.warning("Could not bulk-set role positions: %s", exc)

    return created


async def _restore_categories(guild: discord.Guild, data: dict,
                               cat_map: dict[int, discord.CategoryChannel],
                               role_map: dict[int, discord.Role]) -> int:
    """Create categories in parallel."""
    sorted_cats = sorted(data.get("categories", []), key=lambda c: c["position"])

    async def _create(cd: dict):
        ow = _build_overwrites(cd["overwrites"], role_map, guild)
        try:
            cat = await guild.create_category(
                name=cd["name"], overwrites=ow, reason="Guardian backup restore")
            return (cd["id"], cat)
        except Exception as exc:
            log.warning("Could not create category '%s': %s", cd["name"], exc)
            return None

    results = await _par([_create(cd) for cd in sorted_cats])
    created = 0
    for res in results:
        if isinstance(res, tuple):
            old_id, cat = res
            cat_map[old_id] = cat
            created += 1
    return created


async def _restore_channels(guild: discord.Guild, data: dict,
                             cat_map: dict[int, discord.CategoryChannel],
                             role_map: dict[int, discord.Role]) -> int:
    """Create channels in parallel."""
    sorted_chs = sorted(data.get("channels", []), key=lambda c: c["position"])

    async def _create(cd: dict):
        ow  = _build_overwrites(cd["overwrites"], role_map, guild)
        cat = cat_map.get(cd.get("category_id"))
        ch_type = cd.get("type", "text")
        try:
            if "text" in ch_type or "forum" in ch_type:
                await guild.create_text_channel(
                    name=cd["name"], category=cat, overwrites=ow,
                    topic=cd.get("topic") or "", nsfw=cd.get("nsfw", False),
                    slowmode_delay=cd.get("slowmode", 0),
                    reason="Guardian backup restore",
                )
            elif "voice" in ch_type:
                await guild.create_voice_channel(
                    name=cd["name"], category=cat, overwrites=ow,
                    bitrate=min(cd.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=cd.get("user_limit", 0),
                    reason="Guardian backup restore",
                )
            elif "stage" in ch_type:
                await guild.create_stage_channel(
                    name=cd["name"], category=cat, overwrites=ow,
                    reason="Guardian backup restore",
                )
            else:
                await guild.create_text_channel(
                    name=cd["name"], category=cat, overwrites=ow,
                    reason="Guardian backup restore",
                )
            return True
        except Exception as exc:
            log.warning("Could not create channel '%s': %s", cd["name"], exc)
            return False

    results = await _par([_create(cd) for cd in sorted_chs])
    return sum(1 for r in results if r is True)


async def _restore_emojis(guild: discord.Guild, data: dict) -> int:
    """Upload emojis sequentially (Discord enforces strict rate limits on binary uploads)."""
    created = 0
    async with aiohttp.ClientSession() as session:
        for ed in data.get("emojis", []):
            try:
                async with session.get(ed["url"]) as resp:
                    if resp.status != 200:
                        continue
                    img = await resp.read()
                await guild.create_custom_emoji(
                    name=ed["name"], image=img, reason="Guardian backup restore")
                created += 1
                await asyncio.sleep(1.2)   # emoji rate-limit buffer
            except Exception as exc:
                log.warning("Could not restore emoji '%s': %s", ed.get("name"), exc)
    return created


async def _restore_soundboard(guild: discord.Guild, data: dict,
                               bot_token: str) -> int:
    """Download and re-upload soundboard sounds sequentially."""
    sounds = data.get("soundboard", [])
    if not sounds:
        return 0
    created = 0
    async with aiohttp.ClientSession() as session:
        for sd in sounds:
            try:
                async with session.get(sd["url"]) as resp:
                    if resp.status != 200:
                        continue
                    ogg = await resp.read()
                ok = await _sb_create(
                    session, bot_token, guild.id,
                    sd["name"], ogg, sd.get("volume", 1.0),
                )
                if ok:
                    created += 1
                await asyncio.sleep(1.5)  # soundboard rate-limit buffer
            except Exception as exc:
                log.warning("Could not restore sound '%s': %s", sd.get("name"), exc)
    return created


# ── Interactive Load View ──────────────────────────────────────────────────────

class _WipeSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Phase 1 — What to WIPE before loading…",
            min_values=0,
            max_values=4,
            options=[
                discord.SelectOption(
                    label="Wipe Roles",
                    value="roles",
                    emoji="🛡️",
                    description="Delete all non-managed roles below the bot",
                ),
                discord.SelectOption(
                    label="Wipe Channels",
                    value="channels",
                    emoji="📁",
                    description="Delete all channels (command channel is kept)",
                ),
                discord.SelectOption(
                    label="Wipe Emojis",
                    value="emojis",
                    emoji="😀",
                    description="Delete all custom emojis from this server",
                ),
                discord.SelectOption(
                    label="Wipe Soundboard",
                    value="soundboard",
                    emoji="🔊",
                    description="Delete all guild soundboard sounds",
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


class _LoadSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Phase 2 — What to LOAD from the backup…",
            min_values=0,
            max_values=4,
            options=[
                discord.SelectOption(
                    label="Load Roles",
                    value="roles",
                    emoji="🛡️",
                    description="Recreate all saved roles with exact hierarchy",
                ),
                discord.SelectOption(
                    label="Load Channels",
                    value="channels",
                    emoji="📁",
                    description="Recreate all saved categories and channels",
                ),
                discord.SelectOption(
                    label="Load Emojis",
                    value="emojis",
                    emoji="😀",
                    description="Re-upload all saved custom emojis",
                ),
                discord.SelectOption(
                    label="Load Soundboard",
                    value="soundboard",
                    emoji="🔊",
                    description="Re-upload all saved soundboard sounds",
                ),
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


class LoadView(discord.ui.View):
    """
    Two multi-select menus (Wipe / Load) + Validate & Start button.
    No destructive action is taken until the user clicks the green button.
    """

    def __init__(
        self,
        author_id: int,
        guild: discord.Guild,
        data: dict,
        backup_id: str,
        invoke_channel_id: int,
        bot_token: str,
    ) -> None:
        super().__init__(timeout=120)
        self.author_id         = author_id
        self.guild             = guild
        self.data              = data
        self.backup_id         = backup_id
        self.invoke_channel_id = invoke_channel_id
        self.bot_token         = bot_token
        self.message: Optional[discord.Message] = None

        self.wipe_select = _WipeSelect()
        self.load_select = _LoadSelect()
        self.add_item(self.wipe_select)
        self.add_item(self.load_select)

    # ── Auth guard ─────────────────────────────────────────────────────────────

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This menu belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(
                    embed=_embed(
                        "• __**Load Cancelled**__\n"
                        "Menu expired — no changes were made.",
                        color=COL_WARN,
                    ),
                    view=self,
                )
            except Exception:
                pass

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]

    # ── Button: Validate & Start ────────────────────────────────────────────────

    @discord.ui.button(label="Validate & Start", style=discord.ButtonStyle.success,
                       emoji="✅", row=2)
    async def execute(self, interaction: discord.Interaction,
                      button: discord.ui.Button) -> None:
        wipe: set[str] = set(self.wipe_select.values)
        load: set[str] = set(self.load_select.values)

        if not wipe and not load:
            await interaction.response.send_message(
                "⚠️ Please select at least one option from either menu first.",
                ephemeral=True,
            )
            return

        self._disable_all()
        self.stop()

        # Build human-readable plan lines
        wipe_labels = {
            "roles": "Roles", "channels": "Channels",
            "emojis": "Emojis", "soundboard": "Soundboard",
        }
        plan_wipe = " · ".join(wipe_labels[k] for k in
                               ["roles", "channels", "emojis", "soundboard"] if k in wipe)
        plan_load = " · ".join(wipe_labels[k] for k in
                               ["roles", "channels", "emojis", "soundboard"] if k in load)

        plan_desc = (
            f"• __**Executing Backup Plan**__\n"
            f"Backup ID: `{self.backup_id}`\n\n"
            + (f"**Wipe →** {plan_wipe}\n" if plan_wipe else "")
            + (f"**Load →** {plan_load}\n" if plan_load else "")
            + "\n`⏳` Starting…"
        )
        await interaction.response.edit_message(embed=_embed(plan_desc), view=self)
        msg = interaction.message

        # ── Progress tracker ─────────────────────────────────────────────────
        done: list[str] = []

        async def step(label: str, coro) -> object:
            """Await `coro`, update progress embed before and after."""
            lines = "\n".join(f"`✅` {d}" for d in done)
            lines += ("\n" if lines else "") + f"`⏳` {label}…"
            await msg.edit(embed=_embed(
                f"• __**Executing Backup Plan**__\n\n{lines}"
            ))
            result = await coro
            done.append(label)
            return result

        role_map: dict[int, discord.Role]            = {}
        cat_map:  dict[int, discord.CategoryChannel] = {}
        roles_n = cats_n = chs_n = emojis_n = sounds_n = 0

        try:
            # ── Phase 1: WIPE (channels first so the bot keeps its msg channel) ──
            if "channels" in wipe:
                await step("Wiping channels",
                           _wipe_channels(self.guild, self.invoke_channel_id))
            if "roles" in wipe:
                await step("Wiping roles", _wipe_roles(self.guild))
            if "emojis" in wipe:
                await step("Wiping emojis", _wipe_emojis(self.guild))
            if "soundboard" in wipe:
                await step("Wiping soundboard",
                           _wipe_soundboard(self.guild, self.bot_token))

            # ── Phase 2: LOAD (strict order: roles → cats → channels → emojis → sb) ──
            if "roles" in load:
                roles_n = await step(
                    "Creating roles",
                    _restore_roles(self.guild, self.data, role_map),
                )
            else:
                # Use existing guild roles so channel overwrites resolve correctly
                role_map = {r.id: r for r in self.guild.roles}

            if "channels" in load:
                cats_n = await step(
                    "Creating categories",
                    _restore_categories(self.guild, self.data, cat_map, role_map),
                )
                chs_n = await step(
                    "Creating channels",
                    _restore_channels(self.guild, self.data, cat_map, role_map),
                )

            if "emojis" in load:
                emojis_n = await step(
                    "Uploading emojis",
                    _restore_emojis(self.guild, self.data),
                )

            if "soundboard" in load:
                sounds_n = await step(
                    "Uploading soundboard",
                    _restore_soundboard(self.guild, self.data, self.bot_token),
                )

            # ── Summary ──────────────────────────────────────────────────────
            parts: list[str] = []
            if "roles"      in load: parts.append(f"`{roles_n}` roles")
            if "channels"   in load:
                parts.append(f"`{cats_n}` categories")
                parts.append(f"`{chs_n}` channels")
            if "emojis"     in load: parts.append(f"`{emojis_n}` emojis")
            if "soundboard" in load: parts.append(f"`{sounds_n}` sounds")

            channel_note = (
                "\n\n⚠️ *The command channel was preserved — delete it manually.*"
                if "channels" in wipe else ""
            )

            await msg.edit(embed=_embed(
                f"• __**Restore Complete**__\n"
                f"Backup `{self.backup_id}` applied successfully.\n\n"
                f"• __**Restored**__\n"
                + (" · ".join(parts) if parts else "*(nothing loaded)*")
                + channel_note
            ), view=None)

        except Exception as exc:
            log.error("Backup restore failed: %s", exc, exc_info=True)
            try:
                await msg.edit(embed=_embed(
                    f"• __**Restore Failed**__\n`{exc}`\n\n"
                    "Partial changes may have been applied.",
                    color=COL_ERR,
                ), view=None)
            except Exception:
                pass

    # ── Button: Cancel ──────────────────────────────────────────────────────────

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger,
                       emoji="❌", row=2)
    async def cancel(self, interaction: discord.Interaction,
                     button: discord.ui.Button) -> None:
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            embed=_embed(
                "• __**Load Cancelled**__\nNo changes were made to this server.",
                color=COL_WARN,
            ),
            view=self,
        )


# ── Legacy full restore ────────────────────────────────────────────────────────

async def _legacy_restore(guild: discord.Guild, data: dict,
                           progress_msg: discord.Message) -> None:
    """Full restore used by the legacy +restore command (no wipe)."""
    async def upd(lines: list[str]) -> None:
        await progress_msg.edit(embed=_embed("\n".join(lines)))

    status: list[str] = [
        "• __**Restore**__",
        f"Server: **{guild.name}**",
        f"Snapshot: `{data['meta'].get('backed_up_at', 'unknown')}`",
        "",
    ]
    role_map: dict[int, discord.Role]            = {}
    cat_map:  dict[int, discord.CategoryChannel] = {}

    status.append("`⏳` Restoring roles…")
    await upd(status)
    roles_n = await _restore_roles(guild, data, role_map)
    status[-1] = f"`✅` Roles restored: `{roles_n}`"
    status.append("`⏳` Restoring categories…")
    await upd(status)

    cats_n = await _restore_categories(guild, data, cat_map, role_map)
    status[-1] = f"`✅` Categories restored: `{cats_n}`"
    status.append("`⏳` Restoring channels…")
    await upd(status)

    chs_n = await _restore_channels(guild, data, cat_map, role_map)
    status[-1] = f"`✅` Channels restored: `{chs_n}`"
    status.append("`⏳` Restoring emojis…")
    await upd(status)

    emojis_n = await _restore_emojis(guild, data)
    status[-1] = f"`✅` Emojis restored: `{emojis_n}`"

    # Member roles
    status.append("`⏳` Restoring member roles…")
    await upd(status)
    assigned = 0
    for member_data in data.get("members", []):
        member = guild.get_member(member_data["id"])
        if not member:
            continue
        new_roles = [role_map[oid] for oid in member_data.get("role_ids", [])
                     if oid in role_map]
        if not new_roles:
            continue
        try:
            await member.add_roles(*new_roles, reason="Guardian backup restore")
            assigned += 1
            await asyncio.sleep(0.25)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not assign roles to %s: %s", member_data["name"], exc)

    status[-1] = f"`✅` Member roles: `{assigned}` member(s)"
    status.extend(["", "• __**Complete**__\nServer has been fully reconstructed."])
    await upd(status)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── +backup (group) ────────────────────────────────────────────────────────

    @commands.group(name="backup", invoke_without_command=True)
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def backup(self, ctx: commands.Context) -> None:
        """Shows backup system usage when no subcommand is provided."""
        if not _has_elevated(ctx):
            return
        await ctx.send(embed=_embed(
            "• __**Backup System**__\n\n"
            "• `+backup create` — Snapshot this server (roles, channels, emojis, soundboard)\n"
            "• `+backup list` — View all saved backups (ID · server · date)\n"
            "• `+backup load <id>` — Interactive multi-select restore UI\n"
            "• `+backup delete <id>` — Permanently delete a saved backup\n\n"
            "*Run any subcommand for details.*"
        ))

    # ── +backup create ─────────────────────────────────────────────────────────

    @backup.command(name="create")
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def backup_create(self, ctx: commands.Context) -> None:
        """Snapshot the entire server and save it with a unique Backup ID."""
        if not _has_elevated(ctx):
            return

        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        backup_id  = _generate_backup_id()
        created_at = datetime.now(timezone.utc).isoformat()

        progress_msg = await ctx.send(embed=_embed(
            f"• __**Backup Initializing**__\n"
            f"Server: **{ctx.guild.name}**\n"
            f"Backup ID: `{backup_id}`\n\n"
            "*Capturing server — this may take a moment.*"
        ))

        try:
            data = await _do_backup(ctx.guild, progress_msg, self.bot.http.token)
        except Exception as exc:
            log.error("Backup failed for guild %s: %s", ctx.guild.id, exc, exc_info=True)
            await progress_msg.edit(embed=_embed(
                f"• __**Backup Failed**__\n`{exc}`", color=COL_ERR))
            return

        path = _backup_path(backup_id)
        await asyncio.to_thread(
            path.write_text, json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        await asyncio.to_thread(_add_to_index, backup_id, ctx.guild.id,
                                ctx.guild.name, created_at)

        size_kb = round(path.stat().st_size / 1024, 1)
        log.info("Backup %s saved for guild %s (%.1f KB)", backup_id,
                 ctx.guild.id, size_kb)

        await progress_msg.edit(embed=_embed(
            f"• __**Backup Complete**__\n"
            f"Server: **{ctx.guild.name}**\n\n"
            f"• __**Backup ID**__\n`{backup_id}` ← save this to load later\n\n"
            f"• __**Saved**__\n"
            f"`{len(data['roles'])}` roles · "
            f"`{len(data['categories'])}` categories · "
            f"`{len(data['channels'])}` channels · "
            f"`{len(data['emojis'])}` emojis · "
            f"`{len(data['soundboard'])}` sounds · "
            f"`{len(data['members'])}` members\n\n"
            f"• __**File**__\n`{size_kb} KB`\n\n"
            f"• __**Snapshot time**__\n"
            f"{discord.utils.format_dt(datetime.now(timezone.utc), 'F')}"
        ))

    # ── +backup list ───────────────────────────────────────────────────────────

    @backup.command(name="list")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def backup_list(self, ctx: commands.Context) -> None:
        """List all saved backups (ID · server name · date)."""
        if not _has_elevated(ctx):
            return

        index = await asyncio.to_thread(_load_index)
        if not index:
            await ctx.send(embed=_embed(
                "• __**No Backups Found**__\n"
                "Use `+backup create` to snapshot this server."
            ))
            return

        sorted_index = sorted(index, key=lambda e: e.get("created_at", ""),
                              reverse=True)
        lines = []
        for i, entry in enumerate(sorted_index[:20], 1):
            bid   = entry.get("id", "?")
            gname = entry.get("guild_name", "Unknown")
            ts    = entry.get("created_at", "")
            try:
                dt  = datetime.fromisoformat(ts)
                dts = discord.utils.format_dt(dt, "d")
            except Exception:
                dts = ts[:10] if ts else "?"
            lines.append(f"`{i}.` **`{bid}`** — {gname} — {dts}")

        overflow = (f"\n\n*…and {len(sorted_index) - 20} more*"
                    if len(sorted_index) > 20 else "")

        e = discord.Embed(
            title="Trossard ♱  —  Saved Backups",
            description="\n".join(lines) + overflow,
            color=COL,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text=f"{FOOTER}   ·   {len(sorted_index)} backup(s) total")
        await ctx.send(embed=e)

    # ── +backup load <id> ──────────────────────────────────────────────────────

    @backup.command(name="load")
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def backup_load(self, ctx: commands.Context, backup_id: str) -> None:
        """Interactive multi-select restore — choose what to wipe and what to load."""
        if not _has_elevated(ctx):
            return

        backup_id = backup_id.upper()
        path = _backup_path(backup_id)

        if not path.exists():
            await ctx.send(embed=_embed(
                f"• __**Backup Not Found**__\n"
                f"No backup with ID `{backup_id}` exists.\n"
                "Use `+backup list` to see all available backups.",
                color=COL_ERR,
            ), delete_after=12)
            return

        try:
            raw  = await asyncio.to_thread(path.read_text, encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Corrupt Backup**__\n`{exc}`", color=COL_ERR,
            ), delete_after=12)
            return

        meta       = data.get("meta", {})
        guild_name = meta.get("guild_name", "Unknown")
        backed_at  = meta.get("backed_up_at", "?")
        try:
            dts = discord.utils.format_dt(datetime.fromisoformat(backed_at), "F")
        except Exception:
            dts = backed_at

        n_roles  = len(data.get("roles", []))
        n_cats   = len(data.get("categories", []))
        n_chs    = len(data.get("channels", []))
        n_emojis = len(data.get("emojis", []))
        n_sounds = len(data.get("soundboard", []))

        e = discord.Embed(
            title="Trossard ♱  —  Load Backup",
            description=(
                f"• __**Backup ID**__\n`{backup_id}`\n\n"
                f"• __**Original Server**__\n{guild_name}\n\n"
                f"• __**Snapshot Date**__\n{dts}\n\n"
                f"• __**Contents**__\n"
                f"`{n_roles}` roles · `{n_cats}` categories · `{n_chs}` channels · "
                f"`{n_emojis}` emojis · `{n_sounds}` sounds\n\n"
                "⚠️ **Use the menus below to choose what to wipe and what to load.**\n"
                "Nothing happens until you click **Validate & Start**.\n"
                "*This menu expires in 2 minutes.*"
            ),
            color=COL_WARN,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text=FOOTER)

        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        view = LoadView(
            author_id=ctx.author.id,
            guild=ctx.guild,
            data=data,
            backup_id=backup_id,
            invoke_channel_id=ctx.channel.id,
            bot_token=self.bot.http.token,
        )
        view.message = await ctx.send(embed=e, view=view)

    # ── +backup delete <id> ────────────────────────────────────────────────────

    @backup.command(name="delete")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def backup_delete(self, ctx: commands.Context, backup_id: str) -> None:
        """Permanently delete a saved backup by its ID."""
        if not _has_elevated(ctx):
            return

        backup_id = backup_id.upper()
        path = _backup_path(backup_id)

        if not path.exists():
            await ctx.send(embed=_embed(
                f"• __**Not Found**__\nNo backup with ID `{backup_id}` exists.",
                color=COL_ERR,
            ), delete_after=10)
            return

        try:
            await asyncio.to_thread(path.unlink)
            await asyncio.to_thread(_remove_from_index, backup_id)
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Delete Failed**__\n`{exc}`", color=COL_ERR,
            ), delete_after=10)
            return

        await ctx.send(embed=_embed(
            f"• __**Backup Deleted**__\n"
            f"Backup `{backup_id}` has been permanently removed."
        ))

    # ── Error handlers ─────────────────────────────────────────────────────────

    @backup_load.error
    async def _load_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+backup load <backup_id>`\n\n"
                "Run `+backup list` to see all available backup IDs."
            ), delete_after=10)

    @backup_delete.error
    async def _delete_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+backup delete <backup_id>`"
            ), delete_after=10)

    # ── +restore (legacy) ──────────────────────────────────────────────────────

    @commands.command(name="restore")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def restore(self, ctx: commands.Context,
                      guild_id: Optional[int] = None) -> None:
        """(Legacy) Replay a guild-id-based snapshot into the current server."""
        if not _has_elevated(ctx):
            return

        target_id   = guild_id or ctx.guild.id
        legacy_path = BACKUPS_DIR / f"{target_id}.json"

        if not legacy_path.exists():
            await ctx.send(embed=_embed(
                f"• __**Error**__\nNo legacy backup found for guild ID `{target_id}`.\n"
                "Use `+backup create` to create a new-style backup.",
                color=COL_ERR,
            ), delete_after=12)
            return

        try:
            raw  = await asyncio.to_thread(legacy_path.read_text, encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Error**__\nCorrupted backup: `{exc}`", color=COL_ERR,
            ), delete_after=10)
            return

        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        backed_at = data.get("meta", {}).get("backed_up_at", "unknown")
        warning = await ctx.send(embed=_embed(
            "• __**Restore Warning**__\n"
            "This **adds** roles, categories, channels, and emojis — "
            "existing content is **not deleted** (duplicates may appear).\n\n"
            f"• __**Snapshot**__\n`{backed_at}`\n\n"
            "*Starting in 5 seconds…*",
            color=COL_WARN,
        ))
        await asyncio.sleep(5)

        progress_msg = await ctx.send(embed=_embed(
            f"• __**Restore Initializing**__\n"
            f"Replaying snapshot for **{ctx.guild.name}**…"
        ))

        try:
            await _legacy_restore(ctx.guild, data, progress_msg)
        except Exception as exc:
            log.error("Restore failed for guild %s: %s", ctx.guild.id, exc,
                      exc_info=True)
            await progress_msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`\n\n"
                "Partial changes may have been applied.",
                color=COL_ERR,
            ))

        try:
            await warning.delete()
        except Exception:
            pass

    @restore.error
    async def _restore_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+restore` — restore from this guild's legacy backup\n"
                "`+restore <guild_id>` — restore from another guild's legacy backup\n\n"
                "For the new system, use `+backup load <id>`."
            ), delete_after=10)

    # ── +cloneroles (legacy, unchanged) ───────────────────────────────────────

    @commands.command(name="cloneroles")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def cloneroles(self, ctx: commands.Context,
                         source_guild_id: int) -> None:
        """Copy roles from a backed-up server into the current server."""
        if not _has_elevated(ctx):
            return

        path = BACKUPS_DIR / f"{source_guild_id}.json"
        if not path.exists():
            await ctx.send(embed=_embed(
                f"• __**Error**__\nNo legacy backup found for guild `{source_guild_id}`.\n"
                "Use `+backup create` in that server first.",
                color=COL_ERR,
            ), delete_after=10)
            return

        try:
            raw  = await asyncio.to_thread(path.read_text, encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Error**__\nCorrupted backup: `{exc}`", color=COL_ERR,
            ), delete_after=10)
            return

        roles = [r for r in data.get("roles", []) if not r.get("is_default")]
        if not roles:
            await ctx.send(embed=_embed(
                "• __**Error**__\nNo roles found in that backup."
            ), delete_after=8)
            return

        src_name     = data.get("meta", {}).get("guild_name", str(source_guild_id))
        roles_sorted = sorted(roles, key=lambda r: r["position"])

        progress_msg = await ctx.send(embed=_embed(
            f"• __**Role Clone Initializing**__\n"
            f"Source: **{src_name}** (`{source_guild_id}`)\n"
            f"Cloning `{len(roles_sorted)}` roles → **{ctx.guild.name}**\n\n"
            "*Creating in hierarchical order — please wait…*"
        ))

        created: list[discord.Role] = []
        failed = 0
        for rd in roles_sorted:
            try:
                role = await ctx.guild.create_role(
                    name=rd["name"],
                    color=discord.Color(rd["color"]),
                    hoist=rd["hoist"],
                    mentionable=rd["mentionable"],
                    permissions=discord.Permissions(rd["permissions"]),
                    reason=f"[Guardian] Role clone from {src_name} ({source_guild_id})",
                )
                created.append(role)
                await asyncio.sleep(0.5)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not create role '%s': %s", rd["name"], exc)
                failed += 1

        if created:
            try:
                positions = {role: idx + 1 for idx, role in enumerate(created)}
                await ctx.guild.edit_role_positions(
                    positions=positions,
                    reason=f"[Guardian] Role clone hierarchy from {source_guild_id}",
                )
            except Exception as exc:
                log.warning("Could not set role positions: %s", exc)

        await progress_msg.edit(embed=_embed(
            f"• __**Role Clone Complete**__\n"
            f"Source: **{src_name}**\n\n"
            f"• __**Created**__\n`{len(created)}` roles\n\n"
            f"• __**Failed**__\n`{failed}` roles\n\n"
            f"• __**Hierarchy**__\nPositions applied — roles ordered bottom → top."
        ))

    @cloneroles.error
    async def _cloneroles_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+cloneroles <source_guild_id>`\n\n"
                "Run `+backup create` in the source server first."
            ), delete_after=8)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Backup(bot))
