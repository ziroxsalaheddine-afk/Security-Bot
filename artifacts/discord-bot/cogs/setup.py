"""
Fast Setup System — Guardian Bot
══════════════════════════════════════════════════════
Interactive, paginated server configuration wizard.

  Sections:
    ──────────────────────────────────────────────
    • Embed builders      — _page1/2/3_embed()
    • Modals              — AntiSpamModal, ActionLimitsModal, WhitelistDomainModal
    • Select views        — WhitelistUserSelect, RemoveUserSelect, LogChannelSelect
    • Config buttons      — one class per action button
    • SetupView           — main paginated nav + dynamic button rows
    • SetupCog            — +setup command entry point
    ──────────────────────────────────────────────
"""

import os
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db
from utils import guild_db as gdb

log = logging.getLogger("guardian.setup")

# ── Constants ──────────────────────────────────────────────────────────────────

BANNER_FILE  = os.path.join(os.path.dirname(__file__), "..", "assets", "banner.gif")
BANNER_ATTACH = "attachment://banner.gif"
FOOTER       = "GUARDIAN SHIELD  ·  FAST SETUP"
TOTAL_PAGES  = 3

COL_CRIMSON  = 0x8A0303
COL_VOID     = 0x0A0A0A
COL_TEAL     = 0x003333

SEP = "─" * 42


# ══════════════════════════════════════════════════════════════════════════════
# EMBED BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def _badge(val: bool) -> str:
    return "`◉ ON `" if val else "`○ OFF`"


def _page1_embed(guild_id: int) -> discord.Embed:
    spam  = gdb.get_guild_value(guild_id, ["automod", "antiSpam"],  {})
    raid  = gdb.get_guild_value(guild_id, ["automod", "antiRaid"],  {})
    scan  = gdb.get_guild_value(guild_id, ["automod", "antiLink", "scanInvites"], True)

    e = discord.Embed(
        title="⚔️  SENTINEL CORE  —  Anti-Raid & Flood",
        description=(
            f"> Your server's **first line of defense**.\n"
            f"> Every module below triggers in **real-time**.\n"
            f"`{SEP}`\n"
            f"**🔍  Scan Invites** {_badge(scan)}\n"
            f"> Detects and removes malicious invite links before they spread.\n\n"
            f"**🛑  Anti-Spam** {_badge(spam.get('enabled', True))}\n"
            f"> Flood limit: **{spam.get('messageLimit', 5)} msgs** per **{spam.get('interval', 3)}s** window.\n\n"
            f"**⚙️  Action Limits** — `{raid.get('joinThreshold', 10)} joins / {raid.get('joinInterval', 10)}s`\n"
            f"> Punishment: **{str(raid.get('action', 'kick')).upper()}** on threshold breach."
        ),
        color=COL_CRIMSON,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_thumbnail(url=BANNER_ATTACH)
    e.set_footer(text=f"{FOOTER}  ·  PAGE 1 / {TOTAL_PAGES}")
    return e


def _page2_embed(guild_id: int) -> discord.Embed:
    domains = gdb.get_guild_value(guild_id, ["automod", "antiLink", "allowedDomains"], [])
    users   = gdb.get_whitelisted_users(guild_id)
    preview = ("`, `".join(domains[:4]) + ("…" if len(domains) > 4 else "")) if domains else "none"

    e = discord.Embed(
        title="🔗  ACCESS CONTROL  —  Whitelist Management",
        description=(
            f"> Define **who and what** bypasses your security layers.\n"
            f"> Every entry here is logged and auditable.\n"
            f"`{SEP}`\n"
            f"**🔗  Allowed Domains** — `{len(domains)} configured`\n"
            f"> Permitted: `{preview}`\n\n"
            f"**👥  Whitelisted Users** — `{len(users)} user(s)`\n"
            f"> These users bypass **all** security checks unconditionally.\n\n"
            f"**🚫  Revoke Access**\n"
            f"> Strip whitelist privileges from a user instantly."
        ),
        color=COL_VOID,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_thumbnail(url=BANNER_ATTACH)
    e.set_footer(text=f"{FOOTER}  ·  PAGE 2 / {TOTAL_PAGES}")
    return e


def _page3_embed(guild_id: int) -> discord.Embed:
    restore = gdb.get_guild_value(guild_id, ["antinuke", "autoRestore"], True)
    clear   = gdb.get_guild_value(guild_id, ["antinuke", "clearRoles"],  True)
    log_ch  = gdb.get_guild_value(guild_id, ["logs", "channelId"],       None)

    e = discord.Embed(
        title="⚡  PUNISHMENT ENGINE  —  Recovery & Logging",
        description=(
            f"> Configure **consequences** for malicious actors.\n"
            f"> Guardian acts with **zero delay** when triggered.\n"
            f"`{SEP}`\n"
            f"**🔄  Auto-Restore** {_badge(restore)}\n"
            f"> Re-creates deleted channels & roles with exact permission overwrites.\n\n"
            f"**🧹  Clear Roles** {_badge(clear)}\n"
            f"> Strips **all roles** from any admin who exceeds action thresholds.\n\n"
            f"**📋  Log Channel** — {f'<#{log_ch}>' if log_ch else '`not configured`'}\n"
            f"> All Guardian events are streamed here in real-time."
        ),
        color=COL_TEAL,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_thumbnail(url=BANNER_ATTACH)
    e.set_footer(text=f"{FOOTER}  ·  PAGE 3 / {TOTAL_PAGES}")
    return e


def _success_embed(title: str, body: str) -> discord.Embed:
    e = discord.Embed(
        title=f"✅  {title}",
        description=body,
        color=0x0D2B0D,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=FOOTER)
    return e


def _denied_embed(reason: str = "Only the setup initiator can use this.") -> discord.Embed:
    return discord.Embed(
        title="🛑  ACCESS DENIED",
        description=f"> {reason}",
        color=COL_CRIMSON,
        timestamp=datetime.now(timezone.utc),
    )


_PAGE_BUILDERS = [_page1_embed, _page2_embed, _page3_embed]


# ══════════════════════════════════════════════════════════════════════════════
# MODALS
# ══════════════════════════════════════════════════════════════════════════════

class AntiSpamModal(discord.ui.Modal, title="🛑  Anti-Spam — Set Limits"):
    msg_limit = discord.ui.TextInput(
        label="Message Limit",
        placeholder="e.g. 5  (messages before auto-mute)",
        default="5",
        min_length=1,
        max_length=3,
    )
    interval = discord.ui.TextInput(
        label="Time Window (seconds)",
        placeholder="e.g. 3  (rolling window)",
        default="3",
        min_length=1,
        max_length=3,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.msg_limit.value)
            ivl   = int(self.interval.value)
        except ValueError:
            await interaction.response.send_message(
                embed=_denied_embed("Values must be whole numbers."), ephemeral=True
            )
            return
        gdb.set_guild_value(self.guild_id, ["automod", "antiSpam", "messageLimit"], limit)
        gdb.set_guild_value(self.guild_id, ["automod", "antiSpam", "interval"],     ivl)
        gdb.set_guild_value(self.guild_id, ["automod", "antiSpam", "enabled"],      True)
        await interaction.response.send_message(
            embed=_success_embed(
                "Anti-Spam Updated",
                f"> Flood limit: **{limit} messages** per **{ivl}s** window.\n"
                "> Anti-Spam is now **ENABLED** and active.",
            ),
            ephemeral=True,
        )


class ActionLimitsModal(discord.ui.Modal, title="⚙️  Action Limits — Raid Threshold"):
    threshold = discord.ui.TextInput(
        label="Join Threshold",
        placeholder="e.g. 10  (joins that trigger punishment)",
        default="10",
        min_length=1,
        max_length=3,
    )
    interval = discord.ui.TextInput(
        label="Time Window (seconds)",
        placeholder="e.g. 10  (rolling join window)",
        default="10",
        min_length=1,
        max_length=3,
    )
    action = discord.ui.TextInput(
        label="Punishment  (ban / kick)",
        placeholder="ban  or  kick",
        default="kick",
        min_length=3,
        max_length=4,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            thresh = int(self.threshold.value)
            ivl    = int(self.interval.value)
        except ValueError:
            await interaction.response.send_message(
                embed=_denied_embed("Threshold and interval must be whole numbers."),
                ephemeral=True,
            )
            return
        act = self.action.value.lower().strip()
        if act not in ("ban", "kick"):
            await interaction.response.send_message(
                embed=_denied_embed("Action must be `ban` or `kick`."), ephemeral=True
            )
            return
        gdb.set_guild_value(self.guild_id, ["automod", "antiRaid", "joinThreshold"], thresh)
        gdb.set_guild_value(self.guild_id, ["automod", "antiRaid", "joinInterval"],  ivl)
        gdb.set_guild_value(self.guild_id, ["automod", "antiRaid", "action"],        act)
        await interaction.response.send_message(
            embed=_success_embed(
                "Action Limits Updated",
                f"> Triggers at **{thresh} joins** within a **{ivl}s** window.\n"
                f"> Punishment: **{act.upper()}** — live immediately.",
            ),
            ephemeral=True,
        )


class WhitelistDomainModal(discord.ui.Modal, title="🔗  Allow a Domain"):
    domain = discord.ui.TextInput(
        label="Domain",
        placeholder="e.g. youtube.com  (no https:// needed)",
        min_length=3,
        max_length=100,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        raw   = self.domain.value.strip()
        clean = raw.lstrip("https://").lstrip("http://").split("/")[0].lower()
        gdb.add_whitelisted_domain(self.guild_id, clean)
        await interaction.response.send_message(
            embed=_success_embed(
                "Domain Whitelisted",
                f"> `{clean}` is now **permitted** through the link filter.\n"
                "> Links from this domain will no longer be blocked.",
            ),
            ephemeral=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SELECT VIEWS  (ephemeral child panels)
# ══════════════════════════════════════════════════════════════════════════════

class _AuthCheck(discord.ui.View):
    """Mixin that restricts all interactions to a single author."""

    def __init__(self, author_id: int, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=_denied_embed(), ephemeral=True
            )
            return False
        return True


class WhitelistUserSelect(_AuthCheck):
    def __init__(self, guild_id: int, author_id: int):
        super().__init__(author_id)
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Select user(s) to whitelist…",
        min_values=1,
        max_values=10,
    )
    async def select_cb(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        added = []
        for user in select.values:
            gdb.add_whitelisted_user(self.guild_id, user.id)
            added.append(user.mention)
        await interaction.response.edit_message(
            embed=_success_embed(
                "Users Whitelisted",
                f"> Added **{len(added)}** user(s) — they now bypass all security checks:\n"
                + "".join(f"> • {m}\n" for m in added),
            ),
            view=None,
        )


class RemoveUserSelect(_AuthCheck):
    def __init__(self, guild_id: int, author_id: int):
        super().__init__(author_id)
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Select user(s) to remove…",
        min_values=1,
        max_values=10,
    )
    async def select_cb(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        removed = []
        for user in select.values:
            gdb.remove_whitelisted_user(self.guild_id, user.id)
            removed.append(user.mention)
        await interaction.response.edit_message(
            embed=_success_embed(
                "Access Revoked",
                f"> Removed **{len(removed)}** user(s) — security checks reinstated:\n"
                + "".join(f"> • {m}\n" for m in removed),
            ),
            view=None,
        )


class LogChannelSelect(_AuthCheck):
    def __init__(self, guild_id: int, author_id: int):
        super().__init__(author_id)
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select a text channel for logs…",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
    )
    async def select_cb(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        ch = select.values[0]
        gdb.set_guild_value(self.guild_id, ["logs", "channelId"], ch.id)
        await interaction.response.edit_message(
            embed=_success_embed(
                "Log Channel Set",
                f"> All Guardian events will stream to {ch.mention}.\n"
                "> This takes effect immediately.",
            ),
            view=None,
        )


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG BUTTONS  (one class per action — row 0 of SetupView)
# ══════════════════════════════════════════════════════════════════════════════

class _CfgButton(discord.ui.Button):
    """Base config button — rejects non-author interactions."""

    def __init__(self, author_id: int, **kwargs):
        super().__init__(row=0, **kwargs)
        self.author_id = author_id

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=_denied_embed(), ephemeral=True
            )
            return False
        return True


class ScanInvitesBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        state = gdb.get_guild_value(guild_id, ["automod", "antiLink", "scanInvites"], True)
        super().__init__(
            author_id,
            label=f"Scan Invites: {'ON' if state else 'OFF'}",
            emoji="🔍",
            style=discord.ButtonStyle.success if state else discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        new = gdb.toggle(self.guild_id, ["automod", "antiLink", "scanInvites"])
        await interaction.response.send_message(
            embed=_success_embed(
                "Scan Invites Toggled",
                f"> Invite scanning is now **{'ENABLED' if new else 'DISABLED'}**.\n"
                + ("> Malicious invites will be auto-removed." if new
                   else "> ⚠️  Invite scanning is OFF — use with caution."),
            ),
            ephemeral=True,
        )


class AntiSpamBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        state = gdb.get_guild_value(guild_id, ["automod", "antiSpam", "enabled"], True)
        super().__init__(
            author_id,
            label=f"Anti-Spam: {'ON' if state else 'OFF'}",
            emoji="🛑",
            style=discord.ButtonStyle.danger,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(AntiSpamModal(self.guild_id))


class ActionLimitsBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        super().__init__(
            author_id,
            label="Action Limits",
            emoji="⚙️",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(ActionLimitsModal(self.guild_id))


class WhitelistLinkBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        super().__init__(
            author_id,
            label="Allow Domain",
            emoji="🔗",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(WhitelistDomainModal(self.guild_id))


class WhitelistUsersBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        super().__init__(
            author_id,
            label="Whitelist Users",
            emoji="👥",
            style=discord.ButtonStyle.success,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        e = discord.Embed(
            title="👥  ADD WHITELIST USERS",
            description=(
                f"> Select up to **10 users** to grant full bypass access.\n"
                f"> **Warning:** Whitelisted users bypass **ALL** security checks.\n"
                f"`{SEP}`"
            ),
            color=COL_CRIMSON,
        )
        e.set_footer(text=FOOTER)
        await interaction.response.send_message(
            embed=e,
            view=WhitelistUserSelect(self.guild_id, self.author_id),
            ephemeral=True,
        )


class RevokeAccessBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        super().__init__(
            author_id,
            label="Revoke Access",
            emoji="🚫",
            style=discord.ButtonStyle.danger,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        e = discord.Embed(
            title="🚫  REVOKE WHITELIST ACCESS",
            description=(
                f"> Select users to **remove** from the bypass list.\n"
                f"> Their security checks will be **immediately reinstated**.\n"
                f"`{SEP}`"
            ),
            color=COL_VOID,
        )
        e.set_footer(text=FOOTER)
        await interaction.response.send_message(
            embed=e,
            view=RemoveUserSelect(self.guild_id, self.author_id),
            ephemeral=True,
        )


class AutoRestoreBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        state = gdb.get_guild_value(guild_id, ["antinuke", "autoRestore"], True)
        super().__init__(
            author_id,
            label=f"Auto-Restore: {'ON' if state else 'OFF'}",
            emoji="🔄",
            style=discord.ButtonStyle.success if state else discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        new = gdb.toggle(self.guild_id, ["antinuke", "autoRestore"])
        await interaction.response.send_message(
            embed=_success_embed(
                "Auto-Restore Toggled",
                ("> Deleted channels & roles will be **automatically re-created**\n"
                 "> with exact permission overwrites preserved."
                 if new else
                 "> ⚠️  Auto-Restore is **OFF** — deleted structures must be recovered manually."),
            ),
            ephemeral=True,
        )


class ClearRolesBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        state = gdb.get_guild_value(guild_id, ["antinuke", "clearRoles"], True)
        super().__init__(
            author_id,
            label=f"Clear Roles: {'ON' if state else 'OFF'}",
            emoji="🧹",
            style=discord.ButtonStyle.success if state else discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        new = gdb.toggle(self.guild_id, ["antinuke", "clearRoles"])
        await interaction.response.send_message(
            embed=_success_embed(
                "Clear Roles Toggled",
                ("> Admins exceeding action limits will have **ALL roles stripped** immediately."
                 if new else
                 "> ⚠️  Role clearing is **OFF** — offending admins keep their roles on breach."),
            ),
            ephemeral=True,
        )


class LogChannelBtn(_CfgButton):
    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        super().__init__(
            author_id,
            label="Log Channel",
            emoji="📋",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        e = discord.Embed(
            title="📋  SET LOG CHANNEL",
            description=(
                f"> Guardian security events will be streamed to this channel in real-time.\n"
                f"> Select a **text channel** from the dropdown below.\n"
                f"`{SEP}`"
            ),
            color=COL_TEAL,
        )
        e.set_footer(text=FOOTER)
        await interaction.response.send_message(
            embed=e,
            view=LogChannelSelect(self.guild_id, self.author_id),
            ephemeral=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SETUP VIEW  (main paginated container)
# ══════════════════════════════════════════════════════════════════════════════

_PAGE_BUTTONS = [
    lambda gid, aid: [ScanInvitesBtn(gid, aid),   AntiSpamBtn(gid, aid),     ActionLimitsBtn(gid, aid)],
    lambda gid, aid: [WhitelistLinkBtn(gid, aid),  WhitelistUsersBtn(gid, aid), RevokeAccessBtn(gid, aid)],
    lambda gid, aid: [AutoRestoreBtn(gid, aid),    ClearRolesBtn(gid, aid),   LogChannelBtn(gid, aid)],
]


class SetupView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.guild_id  = guild_id
        self.page      = 0
        self.message: discord.Message | None = None
        self._rebuild()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild(self):
        self.clear_items()
        for btn in _PAGE_BUTTONS[self.page](self.guild_id, self.author_id):
            self.add_item(btn)
        self._add_nav()

    def _add_nav(self):
        back = discord.ui.Button(
            emoji="<a:vrs_arrow2:1483376240919314588>",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page == 0),
            row=1,
        )
        back.callback = self._back_cb

        label = discord.ui.Button(
            label=f"{self.page + 1} / {TOTAL_PAGES}",
            style=discord.ButtonStyle.primary,
            disabled=True,
            row=1,
        )

        nxt = discord.ui.Button(
            emoji="<a:arrowco:1401177337034309702>",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= TOTAL_PAGES - 1),
            row=1,
        )
        nxt.callback = self._next_cb

        self.add_item(back)
        self.add_item(label)
        self.add_item(nxt)

    # ── Nav callbacks ─────────────────────────────────────────────────────────

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=_denied_embed("Only the setup initiator can navigate."),
                ephemeral=True,
            )
            return False
        return True

    async def _back_cb(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(
            embed=_PAGE_BUILDERS[self.page](self.guild_id), view=self
        )

    async def _next_cb(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page = min(TOTAL_PAGES - 1, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(
            embed=_PAGE_BUILDERS[self.page](self.guild_id), view=self
        )

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# COG
# ══════════════════════════════════════════════════════════════════════════════

class SetupCog(commands.Cog, name="Setup"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="setup")
    async def setup_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            await ctx.send(
                embed=discord.Embed(
                    title="🛑  ACCESS DENIED",
                    description=(
                        "> You are **not authorized** to run the setup wizard.\n"
                        "> Contact the server owner to be whitelisted."
                    ),
                    color=COL_CRIMSON,
                ),
                delete_after=6,
            )
            return

        if ctx.guild is None:
            await ctx.send(
                embed=discord.Embed(
                    description="> `+setup` must be used inside a server.",
                    color=COL_CRIMSON,
                ),
                delete_after=5,
            )
            return

        gid  = ctx.guild.id
        view = SetupView(ctx.author.id, gid)
        banner = discord.File(BANNER_FILE, filename="banner.gif")
        view.message = await ctx.send(file=banner, embed=_page1_embed(gid), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupCog(bot))
