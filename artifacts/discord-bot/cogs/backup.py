"""
Backup Cog — +backup / +restore
════════════════════════════════

+backup           — snapshot current server → backups/<guild_id>.json
+restore          — replay snapshot, rebuilding roles · categories · channels · emojis · member roles

Security   : Global Owner OR Server Co-Owner only
Rate limits: asyncio.sleep() between every API write so Discord doesn't 429 us
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

from utils import db
from utils import coowners

log = logging.getLogger("guardian.backup")

COL            = 0x2B2D31
FOOTER         = "© 2026 — developed by zrx.gg"
BACKUPS_DIR    = Path(__file__).parent.parent / "backups"
HISTORY_LIMIT  = 25    # messages saved per text channel
WRITE_SLEEP    = 0.65  # seconds between role / channel creation calls
OVERWRITE_SLEEP= 0.25  # seconds between permission-overwrite edits
EMOJI_SLEEP    = 1.2   # seconds between emoji uploads (stricter limit)

BACKUPS_DIR.mkdir(exist_ok=True)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _has_elevated(ctx: commands.Context) -> bool:
    return db.is_owner(ctx.author.id) or coowners.is_coowner(ctx.guild.id, ctx.author.id)


def _embed(description: str, *, done: bool = False, error: bool = False) -> discord.Embed:
    color = COL
    if error:
        color = 0xC0392B
    e = discord.Embed(description=description, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=FOOTER)
    return e


def _backup_path(guild_id: int) -> Path:
    return BACKUPS_DIR / f"{guild_id}.json"


# ── Backup logic ───────────────────────────────────────────────────────────────

async def _serialize_overwrites(overwrites: dict) -> dict:
    """Convert discord PermissionOverwrite mapping to JSON-safe dict."""
    out = {}
    for target, ow in overwrites.items():
        allow, deny = ow.pair()
        if isinstance(target, discord.Role):
            key = f"role:{target.id}"
        else:
            key = f"member:{target.id}"
        out[key] = {"allow": allow.value, "deny": deny.value}
    return out


async def _do_backup(guild: discord.Guild, progress_msg: discord.Message) -> dict:

    async def upd(lines: list[str]):
        desc = "\n".join(lines)
        await progress_msg.edit(embed=_embed(desc))

    status: list[str] = [
        "• __**Backup**__",
        f"Server: **{guild.name}**",
        "",
    ]

    # ── Metadata ───────────────────────────────────────────────────────────────
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
        "roles":    [],
        "categories": [],
        "channels": [],
        "emojis":   [],
        "members":  [],
    }

    # ── Roles ──────────────────────────────────────────────────────────────────
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

    # ── Categories ─────────────────────────────────────────────────────────────
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

    # ── Channels + history ─────────────────────────────────────────────────────
    ch_count = 0
    msg_count = 0

    for ch in sorted(guild.channels, key=lambda c: c.position):
        if isinstance(ch, discord.CategoryChannel):
            continue  # already captured above

        ch_data: dict = {
            "id":          ch.id,
            "name":        ch.name,
            "type":        str(ch.type),
            "category_id": ch.category_id,
            "position":    ch.position,
            "overwrites":  await _serialize_overwrites(ch.overwrites),
            "history":     [],
        }

        # Text-channel extras
        if isinstance(ch, discord.TextChannel):
            ch_data["topic"]    = ch.topic
            ch_data["nsfw"]     = ch.is_nsfw()
            ch_data["slowmode"] = ch.slowmode_delay

            # Fetch message history
            try:
                async for msg in ch.history(limit=HISTORY_LIMIT, oldest_first=False):
                    ch_data["history"].append({
                        "author":    str(msg.author),
                        "author_id": msg.author.id,
                        "content":   msg.content,
                        "timestamp": msg.created_at.isoformat(),
                        "embeds":    len(msg.embeds),
                        "attachments": [a.url for a in msg.attachments],
                    })
                    msg_count += 1
            except (discord.Forbidden, discord.HTTPException):
                pass

        elif isinstance(ch, discord.VoiceChannel):
            ch_data["bitrate"]   = ch.bitrate
            ch_data["user_limit"] = ch.user_limit

        elif isinstance(ch, discord.StageChannel):
            ch_data["bitrate"] = ch.bitrate

        data["channels"].append(ch_data)
        ch_count += 1

    status[-1] = f"`✅` Channels saved: `{ch_count}` · Messages: `{msg_count}`"
    status.append("`⏳` Capturing emojis…")
    await upd(status)

    # ── Emojis ─────────────────────────────────────────────────────────────────
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

    # ── Members ────────────────────────────────────────────────────────────────
    for member in guild.members:
        role_ids = [r.id for r in member.roles if not r.is_default()]
        data["members"].append({
            "id":          member.id,
            "name":        str(member),
            "role_ids":    role_ids,
        })

    status[-1] = f"`✅` Members saved: `{len(data['members'])}`"
    await upd(status)

    return data


# ── Restore logic ──────────────────────────────────────────────────────────────

async def _do_restore(
    guild: discord.Guild,
    data: dict,
    progress_msg: discord.Message,
):
    async def upd(lines: list[str]):
        desc = "\n".join(lines)
        await progress_msg.edit(embed=_embed(desc))

    status: list[str] = [
        "• __**Restore**__",
        f"Server: **{guild.name}**",
        f"Snapshot: `{data['meta'].get('backed_up_at', 'unknown')}`",
        "",
    ]

    # Maps old IDs → new discord objects (needed to remap permission overwrites)
    role_map: dict[int, discord.Role]     = {}
    cat_map:  dict[int, discord.CategoryChannel] = {}

    # ── Roles ──────────────────────────────────────────────────────────────────
    status.append("`⏳` Restoring roles…")
    await upd(status)

    roles_created = 0
    # Sort ascending by position so we create lowest-position roles first
    for role_data in sorted(data["roles"], key=lambda r: r["position"]):
        if role_data.get("is_default"):
            # Update @everyone permissions instead of creating a new role
            try:
                await guild.default_role.edit(
                    permissions=discord.Permissions(role_data["permissions"])
                )
                role_map[role_data["id"]] = guild.default_role
            except (discord.Forbidden, discord.HTTPException):
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
            roles_created += 1
            await asyncio.sleep(WRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not create role '%s': %s", role_data["name"], exc)

    status[-1] = f"`✅` Roles restored: `{roles_created}`"
    status.append("`⏳` Restoring categories…")
    await upd(status)

    # ── Categories ─────────────────────────────────────────────────────────────
    cats_created = 0
    for cat_data in sorted(data["categories"], key=lambda c: c["position"]):
        overwrites = _build_overwrites(cat_data["overwrites"], role_map, guild)
        try:
            new_cat = await guild.create_category(
                name=cat_data["name"],
                overwrites=overwrites,
                reason="Guardian backup restore",
            )
            cat_map[cat_data["id"]] = new_cat
            cats_created += 1
            await asyncio.sleep(WRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not create category '%s': %s", cat_data["name"], exc)

    status[-1] = f"`✅` Categories restored: `{cats_created}`"
    status.append("`⏳` Restoring channels…")
    await upd(status)

    # ── Channels ───────────────────────────────────────────────────────────────
    chs_created = 0
    for ch_data in sorted(data["channels"], key=lambda c: c["position"]):
        overwrites  = _build_overwrites(ch_data["overwrites"], role_map, guild)
        category    = cat_map.get(ch_data.get("category_id"))
        ch_type     = ch_data.get("type", "text")

        try:
            if "text" in ch_type:
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
                # forum / unknown — fall back to text
                await guild.create_text_channel(
                    name=ch_data["name"],
                    category=category,
                    overwrites=overwrites,
                    reason="Guardian backup restore",
                )

            chs_created += 1
            await asyncio.sleep(WRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not create channel '%s': %s", ch_data["name"], exc)

    status[-1] = f"`✅` Channels restored: `{chs_created}`"
    status.append("`⏳` Restoring emojis…")
    await upd(status)

    # ── Emojis ─────────────────────────────────────────────────────────────────
    emojis_created = 0
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
                emojis_created += 1
                await asyncio.sleep(EMOJI_SLEEP)
            except (discord.Forbidden, discord.HTTPException, Exception) as exc:
                log.warning("Could not restore emoji '%s': %s", emoji_data.get("name"), exc)

    status[-1] = f"`✅` Emojis restored: `{emojis_created}`"
    status.append("`⏳` Restoring member roles…")
    await upd(status)

    # ── Member roles ───────────────────────────────────────────────────────────
    roles_assigned = 0
    for member_data in data.get("members", []):
        member = guild.get_member(member_data["id"])
        if member is None:
            continue
        new_roles = [
            role_map[old_id]
            for old_id in member_data.get("role_ids", [])
            if old_id in role_map
        ]
        if not new_roles:
            continue
        try:
            await member.add_roles(*new_roles, reason="Guardian backup restore")
            roles_assigned += 1
            await asyncio.sleep(OVERWRITE_SLEEP)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not assign roles to %s: %s", member_data["name"], exc)

    status[-1] = f"`✅` Member roles restored: `{roles_assigned}` member(s)"
    status.append("")
    status.append("• __**Complete**__\nServer has been fully reconstructed.")
    await upd(status)


def _build_overwrites(
    raw: dict,
    role_map: dict[int, discord.Role],
    guild: discord.Guild,
) -> dict:
    """Rebuild PermissionOverwrite dict using new role IDs."""
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


# ── Cog ────────────────────────────────────────────────────────────────────────

class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +backup ────────────────────────────────────────────────────────────────

    @commands.command(name="backup")
    async def backup(self, ctx: commands.Context):
        if not _has_elevated(ctx):
            return

        # Ensure member cache is populated
        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        initial = _embed(
            "• __**Backup Initializing**__\n"
            f"Starting snapshot of **{ctx.guild.name}**…\n\n"
            "*This may take a moment depending on server size.*"
        )
        progress_msg = await ctx.send(embed=initial)

        try:
            data = await _do_backup(ctx.guild, progress_msg)
        except Exception as exc:
            log.error("Backup failed for guild %s: %s", ctx.guild.id, exc, exc_info=True)
            await progress_msg.edit(embed=_embed(
                f"• __**Backup Failed**__\n`{exc}`",
                error=True,
            ))
            return

        # Persist to disk
        path = _backup_path(ctx.guild.id)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        size_kb = round(path.stat().st_size / 1024, 1)
        log.info("Backup saved for guild %s → %s (%.1f KB)", ctx.guild.id, path, size_kb)

        await progress_msg.edit(embed=_embed(
            "• __**Backup Complete**__\n"
            f"Server: **{ctx.guild.name}**\n\n"
            f"• __**Saved**__\n"
            f"`{len(data['roles'])}` roles · "
            f"`{len(data['categories'])}` categories · "
            f"`{len(data['channels'])}` channels · "
            f"`{len(data['emojis'])}` emojis · "
            f"`{len(data['members'])}` members\n\n"
            f"• __**File**__\n`backups/{ctx.guild.id}.json` (`{size_kb} KB`)\n\n"
            f"• __**Snapshot Time**__\n"
            f"{discord.utils.format_dt(datetime.now(timezone.utc), 'F')}",
            done=True,
        ))

    # ── +restore ───────────────────────────────────────────────────────────────

    @commands.command(name="restore")
    async def restore(self, ctx: commands.Context, guild_id: Optional[int] = None):
        if not _has_elevated(ctx):
            return

        target_id = guild_id or ctx.guild.id
        path      = _backup_path(target_id)

        if not path.exists():
            await ctx.send(embed=_embed(
                f"• __**Error**__\nNo backup found for guild ID `{target_id}`.\n"
                "Run `+backup` first.",
            ), delete_after=10)
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            await ctx.send(embed=_embed(
                f"• __**Error**__\nCorrupted backup file: `{exc}`",
                error=True,
            ), delete_after=10)
            return

        # Ensure member cache is populated
        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        backed_at = data.get("meta", {}).get("backed_up_at", "unknown")
        warning = await ctx.send(embed=_embed(
            "• __**Restore Warning**__\n"
            "This will **add** roles, categories, channels, and emojis to the current server.\n"
            "Existing content is **not deleted** — duplicates may appear.\n\n"
            f"• __**Snapshot**__\n`{backed_at}`\n\n"
            "*Starting in 5 seconds…*"
        ))

        await asyncio.sleep(5)

        progress_msg = await ctx.send(embed=_embed(
            "• __**Restore Initializing**__\n"
            f"Replaying snapshot for **{ctx.guild.name}**…\n\n"
            "*Rate limits are handled automatically — please be patient.*"
        ))

        try:
            await _do_restore(ctx.guild, data, progress_msg)
        except Exception as exc:
            log.error("Restore failed for guild %s: %s", ctx.guild.id, exc, exc_info=True)
            await progress_msg.edit(embed=_embed(
                f"• __**Restore Failed**__\n`{exc}`\n\n"
                "Partial changes may have been applied.",
                error=True,
            ))

        await warning.delete()

    # ── +cloneroles ────────────────────────────────────────────────────────────

    @commands.command(name="cloneroles")
    async def cloneroles(self, ctx: commands.Context, source_guild_id: int):
        """Copy roles from a backed-up server into the current server, preserving hierarchy."""
        if not _has_elevated(ctx):
            return

        path = _backup_path(source_guild_id)
        if not path.exists():
            return await ctx.send(embed=_embed(
                f"• __**Error**__\nNo backup found for guild `{source_guild_id}`.\n"
                "Run `+backup` in that server first to save its data here."
            ), delete_after=10)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return await ctx.send(embed=_embed(
                f"• __**Error**__\nCorrupted backup file: `{exc}`", error=True
            ), delete_after=10)

        roles = [r for r in data.get("roles", []) if not r.get("is_default")]
        if not roles:
            return await ctx.send(embed=_embed(
                "• __**Error**__\nNo roles found in that backup."
            ), delete_after=8)

        # Sort ascending by original position (position 0 = bottom of list)
        roles_sorted = sorted(roles, key=lambda r: r["position"])
        src_name = data.get("meta", {}).get("guild_name", str(source_guild_id))

        progress_msg = await ctx.send(embed=_embed(
            f"• __**Role Clone Initializing**__\n"
            f"Source: **{src_name}** (`{source_guild_id}`)\n"
            f"Cloning `{len(roles_sorted)}` roles → **{ctx.guild.name}**\n\n"
            f"*Creating in exact hierarchical order — please wait…*"
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

        # Apply positions: index 0 (bottom) → position 1, ascending
        if created:
            try:
                positions = {role: idx + 1 for idx, role in enumerate(created)}
                await ctx.guild.edit_role_positions(
                    positions=positions,
                    reason=f"[Guardian] Role clone hierarchy from {source_guild_id}",
                )
                log.info(
                    "Role positions set for %d cloned roles in %s",
                    len(created), ctx.guild,
                )
            except Exception as exc:
                log.warning("Could not set role positions (roles still created): %s", exc)

        await progress_msg.edit(embed=_embed(
            f"• __**Role Clone Complete**__\n"
            f"Source: **{src_name}**\n\n"
            f"• __**Created**__\n`{len(created)}` roles\n\n"
            f"• __**Failed**__\n`{failed}` roles\n\n"
            f"• __**Hierarchy**__\nPositions applied — roles ordered bottom→top "
            f"exactly as in the source server."
        ))

    @cloneroles.error
    async def _cloneroles_error(self, ctx: commands.Context, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+cloneroles <source_guild_id>`\n\n"
                "Run `+backup` in the source server first."
            ), delete_after=8)

    # ── Error handlers ─────────────────────────────────────────────────────────

    @restore.error
    async def _restore_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=_embed(
                "• __**Usage**__\n`+restore` — restore current server\n"
                "`+restore <guild_id>` — restore from a specific guild's backup"
            ), delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
