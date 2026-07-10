"""
Warden Cog
──────────
Provides:
  • Warning DM system — red embed sent to any user caught by anti-nuke
  • Owner DM notifications every time a user is warned
  • +bypass command group — partial immunity with per-user abuse tracking
  • on_audit_log_entry_create listener that:
      - Skips fully-whitelisted users (100% immune, db.is_whitelisted)
      - Monitors bypass users and auto-revokes on threshold breach
      - Sends warning DM + owner notification for non-whitelisted users

The AntiNuke cog handles punishment/restore; this cog handles warnings and
the bypass system. Both listen to the same audit-log gateway event.
"""

import time
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db
from utils.bypass_db import is_bypassed, add_bypass, remove_bypass, get_bypass_list

log = logging.getLogger("guardian.warden")

# Destructive actions tracked for bypass abuse detection
_BYPASS_ACTIONS = {
    discord.AuditLogAction.channel_delete,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.ban,
    discord.AuditLogAction.kick,
    discord.AuditLogAction.webhook_create,
    discord.AuditLogAction.member_prune,
}

# Bypass users may commit this many destructive actions within BYPASS_WINDOW
# seconds before their bypass is revoked and they are warned/punished.
BYPASS_THRESHOLD = 3
BYPASS_WINDOW    = 10   # seconds

COL_WARN = 0xFF0000
COL_INFO = 0x2B2D31
FOOTER   = "Guardian Security System"


# ── Warning helpers ────────────────────────────────────────────────────────────

async def send_warn_dm(
    user: discord.User | discord.Member,
    guild: discord.Guild,
    reason: str,
) -> None:
    """
    Send the standard red warning embed to a user via DM.

    Embed spec (exact):
      • Color  : 0xFF0000
      • Author : "user warned"
      • Desc   : "You have been jailed in server **{guild.name}**\n• **Reason :** {reason}"
      • Footer : "{user.name} - warn"  /  icon = user avatar
    """
    embed = discord.Embed(
        description=(
            f"You have been jailed in server **{guild.name}**\n"
            f"• **Reason :** {reason}"
        ),
        color=COL_WARN,
    )
    embed.set_author(name="user warned")
    embed.set_footer(
        text=f"{user.name} - warn",
        icon_url=user.display_avatar.url,
    )
    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        log.warning("Cannot DM %s (%d) — DMs are closed.", user, user.id)
    except Exception as e:
        log.error("Failed to send warn DM to %s: %s", user, e)


async def notify_owners(
    bot: commands.Bot,
    user: discord.User | discord.Member,
    guild: discord.Guild,
    reason: str,
) -> None:
    """DM every registered bot owner with details of the warning."""
    owner_ids = db.get_owners()
    if not owner_ids:
        return

    embed = discord.Embed(
        title="⚠️  User Warned",
        description=(
            f"• __**User**__\n{user.mention}  (`{user.id}`)\n\n"
            f"• __**Server**__\n**{guild.name}**  (`{guild.id}`)\n\n"
            f"• __**Reason**__\n{reason}"
        ),
        color=COL_WARN,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=FOOTER)
    embed.set_thumbnail(url=user.display_avatar.url)

    for oid in owner_ids:
        try:
            owner = bot.get_user(oid) or await bot.fetch_user(oid)
            await owner.send(embed=embed)
        except Exception as e:
            log.error("Failed to DM owner %d: %s", oid, e)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Warden(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) → list of action timestamps for bypass tracking
        self._bypass_tracker: dict[tuple[int, int], list[float]] = defaultdict(list)
        # (guild_id, user_id) already warned this window — avoids duplicate DMs
        self._warned: set[tuple[int, int]] = set()

    # ── Audit-log listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if entry.action not in _BYPASS_ACTIONS:
            return

        user = entry.user
        if user is None or user.bot:
            return

        guild = entry.guild

        # ── Full whitelist: 100% immune, never warned ──────────────────────────
        if db.is_whitelisted(user.id):
            return

        key = (guild.id, user.id)

        # ── Bypass users: partial immunity, monitored ──────────────────────────
        if is_bypassed(user.id):
            await self._handle_bypass(guild, user, entry, key)
            return

        # ── Non-whitelisted, non-bypass: send warning DM ──────────────────────
        # Deduplicate: one warn per (guild, user) per 30-second window so rapid
        # multi-action sequences don't flood the user with identical DMs.
        if key in self._warned:
            return
        self._warned.add(key)
        self.bot.loop.call_later(30, lambda: self._warned.discard(key))

        reason = (
            f"Unauthorized destructive action — "
            f"`{entry.action.name.replace('_', ' ').title()}`"
        )
        await send_warn_dm(user, guild, reason)
        await notify_owners(self.bot, user, guild, reason)

    # ── Bypass abuse handler ───────────────────────────────────────────────────

    async def _handle_bypass(
        self,
        guild: discord.Guild,
        user: discord.User,
        entry: discord.AuditLogEntry,
        key: tuple[int, int],
    ) -> None:
        now = time.time()

        # Record timestamp, prune anything outside the window
        tracker = self._bypass_tracker[key]
        tracker.append(now)
        self._bypass_tracker[key] = [ts for ts in tracker if now - ts <= BYPASS_WINDOW]

        count = len(self._bypass_tracker[key])
        log.info(
            "Bypass user %s (%d) action %d/%d in %s",
            user, user.id, count, BYPASS_THRESHOLD, guild,
        )

        if count < BYPASS_THRESHOLD:
            # Still within allowed range — do nothing
            return

        # ── Threshold exceeded — revoke bypass, route through AntiNuke ────────
        self._bypass_tracker[key] = []   # reset so a second event doesn't re-fire
        remove_bypass(user.id)
        log.warning(
            "Bypass ABUSED by %s (%d) in %s (%d actions in %ds) — bypass revoked.",
            user, user.id, guild, count, BYPASS_WINDOW,
        )

        # Route the triggering event through AntiNuke's punish + restore pipeline
        # so the destructive action is handled identically to a non-whitelisted user.
        antinuke = self.bot.cogs.get("AntiNuke")
        if antinuke is not None:
            cfg = db.get_config().get("antinuke", {})
            member = guild.get_member(user.id)
            # Kick off punishment concurrently with restore
            punish_task = asyncio.create_task(
                antinuke._punish(guild, member, user, cfg, "Bypass abused — threshold exceeded")
            )
            if entry.action == discord.AuditLogAction.channel_delete:
                await antinuke._restore_channel(guild, entry, now, user)
            elif entry.action == discord.AuditLogAction.role_delete:
                await antinuke._restore_role(guild, entry, now, user)
            await punish_task
        else:
            # Fallback: strip roles directly if AntiNuke is unavailable
            member = guild.get_member(user.id)
            if member:
                try:
                    safe = [r for r in member.roles if r.is_default() or r >= guild.me.top_role]
                    await member.edit(
                        roles=safe,
                        reason="[Guardian] Bypass abused — threshold exceeded",
                    )
                except Exception as e:
                    log.error("Failed to strip roles from bypass abuser %d: %s", user.id, e)

        reason = (
            f"Bypass privilege abused — `{count}` destructive actions "
            f"within `{BYPASS_WINDOW}s`. Bypass has been permanently revoked."
        )
        await send_warn_dm(user, guild, reason)
        await notify_owners(self.bot, user, guild, reason)

    # ── +bypass command group ──────────────────────────────────────────────────

    @commands.group(name="bypass", invoke_without_command=True)
    async def bypass_group(self, ctx: commands.Context):
        """Partial immunity management. Requires whitelist."""
        if not db.is_whitelisted(ctx.author.id):
            return
        await ctx.send(embed=_embed(
            "Bypass",
            "• __**Usage**__\n"
            "`+bypass add <@user|id>` — grant partial immunity\n"
            "`+bypass remove <@user|id>` — revoke bypass\n"
            "`+bypass list` — show all bypassed users"
        ))

    @bypass_group.command(name="add")
    async def bypass_add(self, ctx: commands.Context, *, target: str):
        """Grant a user partial bypass immunity."""
        if not db.is_whitelisted(ctx.author.id):
            return

        user = await _resolve_user(ctx, target, self.bot)
        if user is None:
            return

        if db.is_whitelisted(user.id):
            return await ctx.send(
                embed=_embed(
                    "Already Fully Whitelisted",
                    f"{user.mention} has full whitelist immunity — bypass is redundant."
                ),
                delete_after=8,
            )

        add_bypass(user.id)
        await ctx.send(embed=_embed(
            "Bypass Granted",
            f"• __**User**__\n{user.mention}  (`{user.id}`)\n\n"
            f"• __**Immunity Level**__\nPartial — monitored\n\n"
            f"• __**Abuse Limit**__\n`{BYPASS_THRESHOLD}` destructive actions "
            f"within `{BYPASS_WINDOW}s` auto-revokes bypass and applies punishment."
        ))

    @bypass_group.command(name="remove")
    async def bypass_remove(self, ctx: commands.Context, *, target: str):
        """Revoke a user's bypass status."""
        if not db.is_whitelisted(ctx.author.id):
            return

        user = await _resolve_user(ctx, target, self.bot)
        if user is None:
            return

        remove_bypass(user.id)
        await ctx.send(embed=_embed(
            "Bypass Revoked",
            f"• __**User**__\n{user.mention}  (`{user.id}`)\n\n"
            f"• __**Status**__\nBypass removed. This user is now subject to full "
            f"anti-nuke enforcement."
        ))

    @bypass_group.command(name="list")
    async def bypass_list(self, ctx: commands.Context):
        """List all users with active bypass status."""
        if not db.is_whitelisted(ctx.author.id):
            return

        blist = get_bypass_list()
        if not blist:
            return await ctx.send(embed=_embed(
                "Bypass List",
                "No users currently have bypass status."
            ))
        lines = "\n".join(f"<@{uid}>  (`{uid}`)" for uid in blist)
        await ctx.send(embed=_embed(
            f"Bypass List — {len(blist)} user(s)",
            f"• __**Users**__\n{lines}"
        ))

    # ── Error handlers ─────────────────────────────────────────────────────────

    @bypass_add.error
    @bypass_remove.error
    async def _bypass_arg_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=_embed(
                    "Missing Argument",
                    "Provide a user mention or ID.\n"
                    "Example: `+bypass add @user`  or  `+bypass add 123456789`"
                ),
                delete_after=8,
            )


# ── Module-level helpers ───────────────────────────────────────────────────────

def _embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=COL_INFO,
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text=FOOTER)


async def _resolve_user(
    ctx: commands.Context,
    target: str,
    bot: commands.Bot,
) -> discord.User | None:
    """Resolve a @mention or raw numeric ID to a discord.User."""
    # Strip Discord mention syntax: <@id>, <@!id>
    target = target.strip().lstrip("<@").rstrip(">").replace("!", "")
    try:
        uid = int(target)
    except ValueError:
        await ctx.send(
            embed=_embed(
                "Invalid Target",
                "Provide a valid user mention or numeric ID.\n"
                "Example: `+bypass add @user`  or  `+bypass add 123456789`"
            ),
            delete_after=8,
        )
        return None

    try:
        return bot.get_user(uid) or await bot.fetch_user(uid)
    except discord.NotFound:
        await ctx.send(
            embed=_embed("User Not Found", f"No user with ID `{uid}` could be found."),
            delete_after=8,
        )
        return None
    except Exception as e:
        log.error("Failed to fetch user %d: %s", uid, e)
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(Warden(bot))
