"""
Backup Cog — Advanced Interactive Backup System
════════════════════════════════════════════════

+backup create          — snapshot server → backups/<ID>.json (unique Backup ID)
+backup list            — embed listing all saved backups (ID, server, date)
+backup load <id>       — interactive UI: choose what to restore, wipes first

Legacy kept for backward compatibility:
+restore [guild_id]     — restore from old-style guild-id backups
+cloneroles <guild_id>  — copy roles from any saved backup

Security   : Global Owner OR Server Co-Owner only
Rate limits: asyncio.sleep() between every API write to avoid 429 errors
"""

import asyncio
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

COL            = 0x2B2D31
COL_ERR        = 0xC0392B
COL_WARN       = 0xE67E22
FOOTER         = "© 2026 — developed by zrx.gg"
BACKUPS_DIR    = Path(__file__).parent.parent / "backups"
INDEX_FILE     = BACKUPS_DIR / "index.json"
HISTORY_LIMIT  = 25
WRITE_SLEEP    = 0.5    # between role / channel creates
OVERWRITE_SLEEP= 0.25   # between permission-overwrite edits
EMOJI_SLEEP    = 1.2    # stricter limit for emoji uploads
DELETE_SLEEP   = 0.4    # between deletions during wipe

BACKUPS_DIR.mkdir(exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _has_elevated(ctx: commands.Context) -> bool:
    return db.is_owner(ctx.author.id) or coowners.is_coowner(ctx.guild.id, ctx.author.id)


def _embed(description: str, *, color: int = COL) -> discord.Embed:
    e = discord.Embed(description=description, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=FOOTER)
    return e


def _generate_backup_id() -> str:
    """Generate a short, unique 8-character uppercase hex Backup ID."""
    return uuid.uuid4().hex[:8].upper()


def _backup_path(backup_id: str) -> Path:
    return BACKUPS_DIR / f"{backup_id}.json"


# ── Index management ───────────────────────────────────────────────────────────

def _load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        raw = INDEX_FILE.read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        return []


def _save_index(index: list[dict]) -> None:
    INDEX_FILE.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _add_to_index(backup_id: str, guild_id: int, guild_name: str, created_at: str) -> None:
    index = _load_index()
    index.append({
        "id":         backup_id,
        "guild_id":   guild_id,
        "guild_name": guild_name,
        "created_at": created_at,
    })
    _save_index(index)


def _remove_from_index(backup_id: str) -> None:
    index = [e for e in _load_index() if e.get("id") != backup_id]
    _save_index(index)


# ── Serialization helpers ──────────────────────────────────────────────────────

async def _serialize_overwrites(overwrites: dict) -> dict:
    out = {}
    for target, ow in overwrites.items():
        allow, deny = ow.pair()
        key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
        out[key] = {"allow": allow.value, "deny": deny.value}
    return out


def _build_overwrites(
    raw: dict,
    role_map: dict[int, discord.Role],
    guild: discord.Guild,
) -> dict:
    result = {}
    for key, val in raw.items():
        kind, old_id_str = key.split(":", 1)
        old_id = int(old_id_str)
        allow  = discord.Permissions(val["allow"])
        deny   = discord.Permissions(val["deny"])
        ow     = discord.PermissionOverwrite.from_pair(allow, deny)
        if kind == "role":
            if old_id in role_map:
                result[role_map[old_id]] = ow
            elif old_id == guild.default_role.id:
                result[guild.default_role] = ow
        elif kind == "member":
            member = guild.get_member(old_id)
            if member:
                result[member] = ow
    return result


# ── Backup creation ────────────────────────────────────────────────────────────

async def _do_backup(guild: discord.Guild, progress_msg: discord.Message) -> dict:

    async def upd(lines: list[str]):
        await progress_msg.edit(embed=_embed("\n".join(lines)))

    status: list[str] = [
        "• __**Backup**__",
        f"Server: **{guild.name}**",
        "",
    ]

    # Metadata
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
        "members":    [],
    }

    # Roles
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
    status[-1] = f"`✅` Roles saved: `{len(data['roles'])}`"
    status.append("`⏳` Capturing categories…")
    await upd(status)

    # Categories
    for cat in sorted(guild.categories, key=lambda c: c.position):
        data["categories"].append({
            "id":         cat.id,
            "name":       cat.name,
            "position":   cat.position,
            "overwrites": await _serialize_overwrites(cat.overwrites),
        })
    status[-1] = f"`✅` Categories saved: `{len(data['categories'])}`"
    status.append("`⏳` Capturing channels…")
    await upd(status)

    # Channels
    ch_count  = 0
    msg_count = 0
    for ch in sorted(guild.channels, key=lambda c: c.position):
        if isinstance(ch, discord.CategoryChannel):
            continue
        ch_data: dict = {
            "id":          ch.id,
            "name":        ch.name,
            "type":        str(ch.type),
            "category_id": ch.category_id,
            "position":    ch.position,
            "overwrites":  await _serialize_overwrites(ch.overwrites),
            "history":     [],
        }
        if isinstance(ch, discord.TextChannel):
            ch_data["topic"]    = ch.topic
            ch_data["nsfw"]     = ch.is_nsfw()
            ch_data["slowmode"] = ch.slowmode_delay
            try:
                async for msg in ch.history(limit=HISTORY_LIMIT, oldest_first=False):
                    ch_data["history"].append({
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
            ch_data["bitrate"]    = ch.bitrate
            ch_data["user_limit"] = ch.user_limit
        elif isinstance(ch, discord.StageChannel):
            ch_data["bitrate"] = ch.bitrate
        data["channels"].append(ch_data)
        ch_count += 1

    status[-1] = f"`✅` Channels saved: `{ch_count}` · Messages: `{msg_count}`"
    status.append("`⏳` Capturing emojis…")
    await upd(status)

    # Emojis
    for emoji in guild.emojis:
        data["emojis"].append({
            "name":     emoji.name,
            "url":      str(emoji.url),
            "animated": emoji.animated,
            "id":       emoji.id,
        })
    status[-1] = f"`✅` Emojis saved: `{len(data['emojis'])}`"
    status.append("`⏳` Capturing member roles…")
    await upd(status)

    # Members
    for member in guild.members:
        role_ids = [r.id for r in member.roles if not r.is_default()]
        data["members"].append({
            "id":       member.id,
            "name":     str(member),
            "role_ids": role_ids,
        })
    status[-1] = f"`✅` Members saved: `{len(data['members'])}`"
    await upd(status)

    return data


# ── Wipe helpers ───────────────────────────────────────────────────────────────

async def _wipe_channels(guild: discord.Guild, progress_msg: discord.Message) -> None:
    """Delete all channels (non-categories first, then categories)."""
    await progress_msg.edit(embed=_embed(
        "• __**Wiping Channels**__\nDeleting existing channels before restoration…"
    ))
    non_cats = [c for c in guild.channels if not isinstance(c, discord.CategoryChannel)]
    for ch in non_cats:
        try:
            await ch.delete(reason="Guardian backup wipe")
            await asyncio.sleep(DELETE_SLEEP)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass
    for cat in guild.categories:
        try:
            await cat.delete(reason="Guardian backup wipe")
            await asyncio.sleep(DELETE_SLEEP)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass


async def _wipe_roles(guild: discord.Guild, progress_msg: discord.Message) -> None:
    """Delete all deletable roles (skip @everyone, managed/integration, and roles above bot)."""
    await progress_msg.edit(embed=_embed(
        "• __**Wiping Roles**__\nDeleting existing roles before restoration…"
    ))
    bot_member = guild.me
    bot_top    = bot_member.top_role.position if bot_member else 0
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if role.is_default() or role.managed or role.position >= bot_top:
            continue
        try:
            await role.delete(reason="Guardian backup wipe")
            await asyncio.sleep(DELETE_SLEEP)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass


async def _wipe_emojis(guild: discord.Guild, progress_msg: discord.Message) -> None:
    """Delete all custom emojis."""
    await progress_msg.edit(embed=_embed(
        "• __**Wiping Emojis**__\nDeleting existing emojis before restoration…"
    ))
    for emoji in list(guild.emojis):
        try:
            await emoji.delete(reason="Guardian backup wipe")
            await asyncio.sleep(0.5)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass


# ── Selective restore helpers ──────────────────────────────────────────────────

async def _restore_roles(
    guild: discord.Guild,
    data: dict,
    role_map: dict,
    progress_msg: discord.Message,
) -> int:
    created = 0
    for role_data in sorted(data.get("roles", []), key=lambda r: r["position"]):
        if role_data.get("is_default"):
            try:
                await guild.default_role.edit(
                    permissions=discord.Permissions(role_data["permissions"])
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            role_map[role_data["id"]] = guild.default_role
            continue
        try:
            new_role = await guild.create_role(
                name=role_data["name"],
                color=discord.Color(role_data["color"]),
                hoist=role_data["hoist"],
                mentionable=role_data["mentionable"],
                permissions=discord.Permissions(role_data["permissions"]),
                reason="Guardian backup restore",
            )
            role_map[role_data["id"]] = new_role
            created += 1
            await asyncio.sleep(WRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not create role '%s': %s", role_data["name"], exc)
    return created


async def _restore_categories(
    guild: discord.Guild,
    data: dict,
    cat_map: dict,
    role_map: dict,
    progress_msg: discord.Message,
) -> int:
    created = 0
    for cat_data in sorted(data.get("categories", []), key=lambda c: c["position"]):
        overwrites = _build_overwrites(cat_data["overwrites"], role_map, guild)
        try:
            new_cat = await guild.create_category(
                name=cat_data["name"],
                overwrites=overwrites,
                reason="Guardian backup restore",
            )
            cat_map[cat_data["id"]] = new_cat
            created += 1
            await asyncio.sleep(WRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not create category '%s': %s", cat_data["name"], exc)
    return created


async def _restore_channels(
    guild: discord.Guild,
    data: dict,
    cat_map: dict,
    role_map: dict,
    progress_msg: discord.Message,
) -> int:
    created = 0
    for ch_data in sorted(data.get("channels", []), key=lambda c: c["position"]):
        overwrites = _build_overwrites(ch_data["overwrites"], role_map, guild)
        category   = cat_map.get(ch_data.get("category_id"))
        ch_type    = ch_data.get("type", "text")
        try:
            if "text" in ch_type or "forum" in ch_type:
                await guild.create_text_channel(
                    name=ch_data["name"],
                    category=category,
                    overwrites=overwrites,
                    topic=ch_data.get("topic") or "",
                    nsfw=ch_data.get("nsfw", False),
                    slowmode_delay=ch_data.get("slowmode", 0),
                    reason="Guardian backup restore",
                )
            elif "voice" in ch_type:
                await guild.create_voice_channel(
                    name=ch_data["name"],
                    category=category,
                    overwrites=overwrites,
                    bitrate=min(ch_data.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=ch_data.get("user_limit", 0),
                    reason="Guardian backup restore",
                )
            elif "stage" in ch_type:
                await guild.create_stage_channel(
                    name=ch_data["name"],
                    category=category,
                    overwrites=overwrites,
                    reason="Guardian backup restore",
                )
            else:
                await guild.create_text_channel(
                    name=ch_data["name"],
                    category=category,
                    overwrites=overwrites,
                    reason="Guardian backup restore",
                )
            created += 1
            await asyncio.sleep(WRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not create channel '%s': %s", ch_data["name"], exc)
    return created


async def _restore_emojis(
    guild: discord.Guild,
    data: dict,
    progress_msg: discord.Message,
) -> int:
    created = 0
    async with aiohttp.ClientSession() as session:
        for emoji_data in data.get("emojis", []):
            try:
                async with session.get(emoji_data["url"]) as resp:
                    if resp.status != 200:
                        continue
                    image_bytes = await resp.read()
                await guild.create_custom_emoji(
                    name=emoji_data["name"],
                    image=image_bytes,
                    reason="Guardian backup restore",
                )
                created += 1
                await asyncio.sleep(EMOJI_SLEEP)
            except (discord.Forbidden, discord.HTTPException, Exception) as exc:
                log.warning("Could not restore emoji '%s': %s", emoji_data.get("name"), exc)
    return created


# ── Interactive Load View ──────────────────────────────────────────────────────

class LoadView(discord.ui.View):
    """
    Interactive prompt asking the user what parts of the backup to restore.
    Shown by +backup load <id> before any destructive action is taken.
    """

    def __init__(self, author_id: int, guild: discord.Guild, data: dict, backup_id: str):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.guild     = guild
        self.data      = data
        self.backup_id = backup_id
        self.message: Optional[discord.Message] = None

    # ── Interaction guard ──────────────────────────────────────────────────────

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This menu belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(
                    embed=_embed(
                        "• __**Load Cancelled**__\nNo option was selected within 60 seconds.",
                        color=COL_WARN,
                    ),
                    view=self,
                )
            except Exception:
                pass

    def _disable_all(self):
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]

    # ── Button: Load Everything ────────────────────────────────────────────────

    @discord.ui.button(label="Load Everything", style=discord.ButtonStyle.success, emoji="🔄", row=0)
    async def load_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        prog = _embed(
            "• __**Loading Everything**__\n"
            "Starting full restore — channels, roles, and emojis.\n\n"
            "`⏳` Step 1/6 — Wiping channels…"
        )
        await interaction.response.edit_message(embed=prog, view=self)
        msg = interaction.message

        try:
            # Step 1: Wipe channels
            await _wipe_channels(self.guild, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Everything**__\n\n"
                "`✅` Channels wiped\n"
                "`⏳` Step 2/6 — Wiping roles…"
            ))

            # Step 2: Wipe roles
            await _wipe_roles(self.guild, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Everything**__\n\n"
                "`✅` Channels wiped\n"
                "`✅` Roles wiped\n"
                "`⏳` Step 3/6 — Creating roles (bottom → top)…"
            ))

            # Step 3: Create roles
            role_map: dict[int, discord.Role] = {}
            roles_n = await _restore_roles(self.guild, self.data, role_map, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Everything**__\n\n"
                "`✅` Channels wiped\n"
                "`✅` Roles wiped\n"
                f"`✅` Roles created: `{roles_n}`\n"
                "`⏳` Step 4/6 — Creating categories…"
            ))

            # Step 4: Create categories
            cat_map: dict[int, discord.CategoryChannel] = {}
            cats_n = await _restore_categories(self.guild, self.data, cat_map, role_map, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Everything**__\n\n"
                "`✅` Channels wiped · Roles wiped\n"
                f"`✅` Roles: `{roles_n}` · Categories: `{cats_n}`\n"
                "`⏳` Step 5/6 — Creating channels with permission overwrites…"
            ))

            # Step 5: Create channels
            chs_n = await _restore_channels(self.guild, self.data, cat_map, role_map, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Everything**__\n\n"
                f"`✅` Roles: `{roles_n}` · Cats: `{cats_n}` · Channels: `{chs_n}`\n"
                "`⏳` Step 6/6 — Uploading emojis…"
            ))

            # Step 6: Emojis
            emojis_n = await _restore_emojis(self.guild, self.data, msg)

            await msg.edit(embed=_embed(
                f"• __**Restore Complete**__\n"
                f"Backup `{self.backup_id}` has been fully applied.\n\n"
                f"• __**Restored**__\n"
                f"`{roles_n}` roles · `{cats_n}` categories · "
                f"`{chs_n}` channels · `{emojis_n}` emojis"
            ), view=None)

        except Exception as exc:
            log.error("Full restore failed: %s", exc, exc_info=True)
            await msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`\n\n"
                "Partial changes may have been applied.",
                color=COL_ERR,
            ), view=None)

    # ── Button: Roles Only ─────────────────────────────────────────────────────

    @discord.ui.button(label="Roles Only", style=discord.ButtonStyle.primary, emoji="🛡️", row=0)
    async def load_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            embed=_embed("• __**Loading Roles**__\n`⏳` Wiping existing roles…"),
            view=self,
        )
        msg = interaction.message
        try:
            await _wipe_roles(self.guild, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Roles**__\n`✅` Roles wiped\n`⏳` Creating roles (bottom → top)…"
            ))
            role_map: dict[int, discord.Role] = {}
            n = await _restore_roles(self.guild, self.data, role_map, msg)
            await msg.edit(embed=_embed(
                f"• __**Roles Restored**__\n"
                f"Backup `{self.backup_id}` — roles applied.\n\n"
                f"• __**Created**__\n`{n}` roles"
            ), view=None)
        except Exception as exc:
            log.error("Roles-only restore failed: %s", exc, exc_info=True)
            await msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`", color=COL_ERR
            ), view=None)

    # ── Button: Channels Only ──────────────────────────────────────────────────

    @discord.ui.button(label="Channels Only", style=discord.ButtonStyle.primary, emoji="📁", row=0)
    async def load_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            embed=_embed("• __**Loading Channels**__\n`⏳` Wiping existing channels…"),
            view=self,
        )
        msg = interaction.message
        try:
            await _wipe_channels(self.guild, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Channels**__\n`✅` Channels wiped\n"
                "`⏳` Creating categories…"
            ))
            # Use existing guild roles to build overwrites as best we can
            role_map: dict[int, discord.Role] = {r.id: r for r in self.guild.roles}
            cat_map: dict[int, discord.CategoryChannel] = {}
            cats_n = await _restore_categories(self.guild, self.data, cat_map, role_map, msg)
            await msg.edit(embed=_embed(
                f"• __**Loading Channels**__\n`✅` Categories: `{cats_n}`\n"
                "`⏳` Creating channels with permission overwrites…"
            ))
            chs_n = await _restore_channels(self.guild, self.data, cat_map, role_map, msg)
            await msg.edit(embed=_embed(
                f"• __**Channels Restored**__\n"
                f"Backup `{self.backup_id}` — channels applied.\n\n"
                f"• __**Created**__\n`{cats_n}` categories · `{chs_n}` channels"
            ), view=None)
        except Exception as exc:
            log.error("Channels-only restore failed: %s", exc, exc_info=True)
            await msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`", color=COL_ERR
            ), view=None)

    # ── Button: Emojis Only ────────────────────────────────────────────────────

    @discord.ui.button(label="Emojis Only", style=discord.ButtonStyle.primary, emoji="😀", row=1)
    async def load_emojis(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            embed=_embed("• __**Loading Emojis**__\n`⏳` Wiping existing emojis…"),
            view=self,
        )
        msg = interaction.message
        try:
            await _wipe_emojis(self.guild, msg)
            await msg.edit(embed=_embed(
                "• __**Loading Emojis**__\n`✅` Emojis wiped\n`⏳` Uploading backup emojis…"
            ))
            n = await _restore_emojis(self.guild, self.data, msg)
            await msg.edit(embed=_embed(
                f"• __**Emojis Restored**__\n"
                f"Backup `{self.backup_id}` — emojis applied.\n\n"
                f"• __**Uploaded**__\n`{n}` emojis"
            ), view=None)
        except Exception as exc:
            log.error("Emojis-only restore failed: %s", exc, exc_info=True)
            await msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`", color=COL_ERR
            ), view=None)

    # ── Button: Cancel ─────────────────────────────────────────────────────────

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            embed=_embed(
                "• __**Load Cancelled**__\nNo changes were made to this server.",
                color=COL_WARN,
            ),
            view=self,
        )


# ── Legacy restore (kept for backward compat) ──────────────────────────────────

async def _legacy_restore(
    guild: discord.Guild,
    data: dict,
    progress_msg: discord.Message,
):
    """Full restore used by the old +restore command."""
    async def upd(lines: list[str]):
        await progress_msg.edit(embed=_embed("\n".join(lines)))

    status: list[str] = [
        "• __**Restore**__",
        f"Server: **{guild.name}**",
        f"Snapshot: `{data['meta'].get('backed_up_at', 'unknown')}`",
        "",
    ]
    role_map: dict[int, discord.Role]          = {}
    cat_map:  dict[int, discord.CategoryChannel] = {}

    status.append("`⏳` Restoring roles…")
    await upd(status)
    roles_n = await _restore_roles(guild, data, role_map, progress_msg)
    status[-1] = f"`✅` Roles restored: `{roles_n}`"
    status.append("`⏳` Restoring categories…")
    await upd(status)

    cats_n = await _restore_categories(guild, data, cat_map, role_map, progress_msg)
    status[-1] = f"`✅` Categories restored: `{cats_n}`"
    status.append("`⏳` Restoring channels…")
    await upd(status)

    chs_n = await _restore_channels(guild, data, cat_map, role_map, progress_msg)
    status[-1] = f"`✅` Channels restored: `{chs_n}`"
    status.append("`⏳` Restoring emojis…")
    await upd(status)

    emojis_n = await _restore_emojis(guild, data, progress_msg)
    status[-1] = f"`✅` Emojis restored: `{emojis_n}`"

    # Member roles
    status.append("`⏳` Restoring member roles…")
    await upd(status)
    assigned = 0
    for member_data in data.get("members", []):
        member = guild.get_member(member_data["id"])
        if not member:
            continue
        new_roles = [
            role_map[oid] for oid in member_data.get("role_ids", []) if oid in role_map
        ]
        if not new_roles:
            continue
        try:
            await member.add_roles(*new_roles, reason="Guardian backup restore")
            assigned += 1
            await asyncio.sleep(OVERWRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not assign roles to %s: %s", member_data["name"], exc)

    status[-1] = f"`✅` Member roles: `{assigned}` member(s)"
    status.extend(["", "• __**Complete**__\nServer has been fully reconstructed."])
    await upd(status)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────────────────────────────────
    # +backup  (group)
    # ──────────────────────────────────────────────────────────────────────────

    @commands.group(name="backup", invoke_without_command=True)
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def backup(self, ctx: commands.Context):
        """Base command — shows usage when no subcommand is given."""
        if not _has_elevated(ctx):
            return
        await ctx.send(embed=_embed(
            "• __**Backup System**__\n\n"
            "• `+backup create` — Snapshot this server and save it with a unique Backup ID\n"
            "• `+backup list` — View all saved backups (ID · server · date)\n"
            "• `+backup load <id>` — Interactively restore a backup\n"
            "• `+backup delete <id>` — Permanently delete a saved backup\n\n"
            "*Run any subcommand for more details.*"
        ))

    # ── +backup create ─────────────────────────────────────────────────────────

    @backup.command(name="create")
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def backup_create(self, ctx: commands.Context):
        """Snapshot the entire server and save it with a unique Backup ID."""
        if not _has_elevated(ctx):
            return

        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        backup_id  = _generate_backup_id()
        created_at = datetime.now(timezone.utc).isoformat()

        initial = _embed(
            f"• __**Backup Initializing**__\n"
            f"Server: **{ctx.guild.name}**\n"
            f"Backup ID: `{backup_id}`\n\n"
            "*This may take a moment depending on server size.*"
        )
        progress_msg = await ctx.send(embed=initial)

        try:
            data = await _do_backup(ctx.guild, progress_msg)
        except Exception as exc:
            log.error("Backup failed for guild %s: %s", ctx.guild.id, exc, exc_info=True)
            await progress_msg.edit(embed=_embed(
                f"• __**Backup Failed**__\n`{exc}`", color=COL_ERR
            ))
            return

        path = _backup_path(backup_id)
        await asyncio.to_thread(
            path.write_text,
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Register in index
        await asyncio.to_thread(_add_to_index, backup_id, ctx.guild.id, ctx.guild.name, created_at)

        size_kb = round(path.stat().st_size / 1024, 1)
        log.info("Backup %s saved for guild %s (%.1f KB)", backup_id, ctx.guild.id, size_kb)

        await progress_msg.edit(embed=_embed(
            f"• __**Backup Complete**__\n"
            f"Server: **{ctx.guild.name}**\n\n"
            f"• __**Backup ID**__\n"
            f"`{backup_id}` ← save this to load later\n\n"
            f"• __**Saved**__\n"
            f"`{len(data['roles'])}` roles · "
            f"`{len(data['categories'])}` categories · "
            f"`{len(data['channels'])}` channels · "
            f"`{len(data['emojis'])}` emojis · "
            f"`{len(data['members'])}` members\n\n"
            f"• __**File size**__\n`{size_kb} KB`\n\n"
            f"• __**Snapshot time**__\n"
            f"{discord.utils.format_dt(datetime.now(timezone.utc), 'F')}"
        ))

    # ── +backup list ───────────────────────────────────────────────────────────

    @backup.command(name="list")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def backup_list(self, ctx: commands.Context):
        """List all saved backups with their ID, original server name, and date."""
        if not _has_elevated(ctx):
            return

        index = await asyncio.to_thread(_load_index)

        if not index:
            await ctx.send(embed=_embed(
                "• __**No Backups Found**__\n"
                "Use `+backup create` to snapshot this server."
            ))
            return

        # Sort newest first
        sorted_index = sorted(
            index,
            key=lambda e: e.get("created_at", ""),
            reverse=True,
        )

        lines = []
        for i, entry in enumerate(sorted_index[:20], 1):
            bid   = entry.get("id", "?")
            gname = entry.get("guild_name", "Unknown Server")
            ts    = entry.get("created_at", "")
            # Convert ISO timestamp to Discord timestamp if possible
            try:
                dt  = datetime.fromisoformat(ts)
                dts = discord.utils.format_dt(dt, "d")
            except Exception:
                dts = ts[:10] if ts else "?"
            lines.append(f"`{i}.` **`{bid}`** — {gname} — {dts}")

        overflow = ""
        if len(sorted_index) > 20:
            overflow = f"\n\n*…and {len(sorted_index) - 20} more backup(s)*"

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
    async def backup_load(self, ctx: commands.Context, backup_id: str):
        """Interactively restore a backup — choose roles, channels, emojis, or everything."""
        if not _has_elevated(ctx):
            return

        backup_id = backup_id.upper()
        path = _backup_path(backup_id)

        if not path.exists():
            await ctx.send(embed=_embed(
                f"• __**Backup Not Found**__\nNo backup with ID `{backup_id}` exists.\n"
                "Use `+backup list` to see all available backups.",
                color=COL_ERR,
            ), delete_after=12)
            return

        try:
            raw  = await asyncio.to_thread(path.read_text, encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Corrupt Backup**__\nCould not read backup `{backup_id}`: `{exc}`",
                color=COL_ERR,
            ), delete_after=12)
            return

        meta       = data.get("meta", {})
        guild_name = meta.get("guild_name", "Unknown Server")
        backed_at  = meta.get("backed_up_at", "?")
        try:
            dt  = datetime.fromisoformat(backed_at)
            dts = discord.utils.format_dt(dt, "F")
        except Exception:
            dts = backed_at

        e = discord.Embed(
            title="Trossard ♱  —  Load Backup",
            description=(
                f"• __**Backup ID**__\n`{backup_id}`\n\n"
                f"• __**Original Server**__\n{guild_name}\n\n"
                f"• __**Snapshot Date**__\n{dts}\n\n"
                f"• __**Contents**__\n"
                f"`{len(data.get('roles', []))}` roles · "
                f"`{len(data.get('categories', []))}` categories · "
                f"`{len(data.get('channels', []))}` channels · "
                f"`{len(data.get('emojis', []))}` emojis\n\n"
                "⚠️ **Select what to load below.**\n"
                "*Existing content will be wiped before loading the selected section.*"
            ),
            color=COL_WARN,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text=f"{FOOTER}   ·   This menu expires in 60 seconds")

        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        view = LoadView(
            author_id=ctx.author.id,
            guild=ctx.guild,
            data=data,
            backup_id=backup_id,
        )
        view.message = await ctx.send(embed=e, view=view)

    # ── +backup delete <id> ────────────────────────────────────────────────────

    @backup.command(name="delete")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def backup_delete(self, ctx: commands.Context, backup_id: str):
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
                f"• __**Delete Failed**__\n`{exc}`", color=COL_ERR
            ), delete_after=10)
            return

        await ctx.send(embed=_embed(
            f"• __**Backup Deleted**__\nBackup `{backup_id}` has been permanently removed."
        ))

    # ──────────────────────────────────────────────────────────────────────────
    # Group error handler
    # ──────────────────────────────────────────────────────────────────────────

    @backup_load.error
    async def _load_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+backup load <backup_id>`\n\n"
                "Run `+backup list` to see all available backup IDs."
            ), delete_after=10)

    @backup_delete.error
    async def _delete_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+backup delete <backup_id>`"
            ), delete_after=10)

    # ──────────────────────────────────────────────────────────────────────────
    # +restore  (legacy — kept for backward compatibility)
    # ──────────────────────────────────────────────────────────────────────────

    @commands.command(name="restore")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def restore(self, ctx: commands.Context, guild_id: Optional[int] = None):
        """(Legacy) Replay a guild-id-based snapshot into the current server."""
        if not _has_elevated(ctx):
            return

        target_id = guild_id or ctx.guild.id
        # Try legacy path first (backups/<guild_id>.json)
        legacy_path = BACKUPS_DIR / f"{target_id}.json"
        if not legacy_path.exists():
            await ctx.send(embed=_embed(
                f"• __**Error**__\nNo legacy backup found for guild ID `{target_id}`.\n"
                "Use `+backup create` to create a new backup, "
                "or `+backup list` to see all saved backups.",
                color=COL_ERR,
            ), delete_after=12)
            return

        try:
            raw  = await asyncio.to_thread(legacy_path.read_text, encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Error**__\nCorrupted backup file: `{exc}`", color=COL_ERR
            ), delete_after=10)
            return

        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        backed_at = data.get("meta", {}).get("backed_up_at", "unknown")
        warning = await ctx.send(embed=_embed(
            "• __**Restore Warning**__\n"
            "This will **add** roles, categories, channels, and emojis to the current server.\n"
            "Existing content is **not deleted** — duplicates may appear.\n\n"
            f"• __**Snapshot**__\n`{backed_at}`\n\n"
            "*Starting in 5 seconds…*",
            color=COL_WARN,
        ))
        await asyncio.sleep(5)

        progress_msg = await ctx.send(embed=_embed(
            "• __**Restore Initializing**__\n"
            f"Replaying snapshot for **{ctx.guild.name}**…\n\n"
            "*Rate limits are handled automatically — please be patient.*"
        ))

        try:
            await _legacy_restore(ctx.guild, data, progress_msg)
        except Exception as exc:
            log.error("Restore failed for guild %s: %s", ctx.guild.id, exc, exc_info=True)
            await progress_msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`\n\nPartial changes may have been applied.",
                color=COL_ERR,
            ))

        try:
            await warning.delete()
        except Exception:
            pass

    @restore.error
    async def _restore_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+restore` — restore from this guild's legacy backup\n"
                "`+restore <guild_id>` — restore from another guild's legacy backup\n\n"
                "For the new system, use `+backup load <id>`."
            ), delete_after=10)

    # ──────────────────────────────────────────────────────────────────────────
    # +cloneroles  (unchanged)
    # ──────────────────────────────────────────────────────────────────────────

    @commands.command(name="cloneroles")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def cloneroles(self, ctx: commands.Context, source_guild_id: int):
        """Copy roles from a backed-up server into the current server, preserving hierarchy."""
        if not _has_elevated(ctx):
            return

        # Try new-style index first, then legacy path
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
            return await ctx.send(embed=_embed(
                f"• __**Error**__\nCorrupted backup: `{exc}`", color=COL_ERR
            ), delete_after=10)

        roles = [r for r in data.get("roles", []) if not r.get("is_default")]
        if not roles:
            return await ctx.send(embed=_embed(
                "• __**Error**__\nNo roles found in that backup."
            ), delete_after=8)

        roles_sorted = sorted(roles, key=lambda r: r["position"])
        src_name     = data.get("meta", {}).get("guild_name", str(source_guild_id))

        progress_msg = await ctx.send(embed=_embed(
            f"• __**Role Clone Initializing**__\n"
            f"Source: **{src_name}** (`{source_guild_id}`)\n"
            f"Cloning `{len(roles_sorted)}` roles → **{ctx.guild.name}**\n\n"
            "*Creating in exact hierarchical order — please wait…*"
        ))

        created: list[discord.Role] = []
        failed = 0
        for role_data in roles_sorted:
            try:
                new_role = await ctx.guild.create_role(
                    name=role_data["name"],
                    color=discord.Color(role_data["color"]),
                    hoist=role_data["hoist"],
                    mentionable=role_data["mentionable"],
                    permissions=discord.Permissions(role_data["permissions"]),
                    reason=f"[Guardian] Role clone from {src_name} ({source_guild_id})",
                )
                created.append(new_role)
                await asyncio.sleep(WRITE_SLEEP)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not create role '%s': %s", role_data["name"], exc)
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
            f"• __**Hierarchy**__\nPositions applied — roles ordered bottom→top."
        ))

    @cloneroles.error
    async def _cloneroles_error(self, ctx: commands.Context, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+cloneroles <source_guild_id>`\n\n"
                "Run `+backup create` in the source server first."
            ), delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
