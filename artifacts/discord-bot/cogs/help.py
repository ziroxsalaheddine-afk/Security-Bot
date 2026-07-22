"""
Help System — Split public/owner menus
═══════════════════════════════════════

+help  (server channels)
    Public-facing interactive help.  Displays only safe, standard commands
    that regular server members and staff are allowed to know about.
    Sensitive or destructive commands (Mass DM, Mass Ban, all owner-DM-only
    tools) are completely absent — they never appear in this menu.

+dm   (DMs only, bot owner only)
    Owner-only DM command reference.  Displays every DM management command:
    server inspection, channel/role control, mass-action tools, and the
    auto-backup toggle.  Silently ignored if used in a server or by anyone
    who is not the bot owner.
"""

import re
import time
import os as _os
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db

log = logging.getLogger("guardian.help")


def _pe(emoji_str: str) -> discord.PartialEmoji:
    """Parse '<:name:id>' or '<a:name:id>' into a PartialEmoji."""
    m = re.fullmatch(r"<(a?):(\w+):(\d+)>", emoji_str.strip())
    if not m:
        raise ValueError(f"Cannot parse emoji: {emoji_str!r}")
    animated, name, eid = m.group(1) == "a", m.group(2), int(m.group(3))
    return discord.PartialEmoji(name=name, id=eid, animated=animated)


# ── Unicode fallbacks (used when Discord rejects custom emojis, error 50035) ──
CATEGORY_FALLBACKS: dict[str, str] = {
    "Security Modules": "🛡️",
    "Aliases":          "🔗",
    "Music":            "🎵",
    "DJ Whitelist":     "🎧",
    "Recovery":         "⚠️",
    "Information":      "ℹ️",
    # DM-menu categories
    "Server Management":    "🖥️",
    "Channel & Role Tools": "⚙️",
    "Mass Actions":         "⚡",
    "Auto-Backup":          "💾",
}
_FB_OVERVIEW = "🏠"
_FB_PREV     = "◀️"
_FB_LABEL    = "📄"
_FB_NEXT     = "▶️"

# ── Embed styling ──────────────────────────────────────────────────────────────
FOOTER       = "© 2026 — developed by zrx.gg"
BANNER_FILE  = _os.path.join(_os.path.dirname(__file__), "..", "assets", "banner.gif")
PAGE_SIZE    = 4
COL_HOME     = 0xE2D6A5
COL_CAT      = 0xE2D6A5
COL_DM       = 0x2B2D31

HOME_IMAGE = (
    "https://cdn.discordapp.com/attachments/1496697643127279646/"
    "1523507326923964456/f7812e1249081221bb80abb048698308.gif"
    "?ex=6a4c5c44&is=6a4b0ac4&hm=df7a87f24927938836303a2af8ba181a6e2d8e0dc65abd6114d125f9e072624e&"
)


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC HELP  — safe commands only, shown to every server member
#
#  Rules applied:
#    • +massdm  is REMOVED  (owner-DM-only destructive action)
#    • +massban is REMOVED  (owner-DM-only destructive action)
#    All owner-DM tools (list, linkserver, leftserver, etc.) never appeared
#    in the public menu — they live exclusively in the +dm menu below.
# ══════════════════════════════════════════════════════════════════════════════

PUBLIC_CATEGORIES: dict[str, dict] = {
    "Security Modules": {
        "emoji": "<:vrs_security:1528586273445511199>",
        "tagline": "Real-time threat annihilation — zero-latency, zero mercy.",
        "commands": [
            ("+antinuke",                  "Displays the current anti-nuke configuration panel."),
            ("+antinuke on/off",           "Enables or disables the entire anti-nuke engine."),
            ("+antinuke threshold <n>",    "Sets how many destructive actions trigger punishment."),
            ("+antinuke interval <s>",     "Sets the time window (seconds) for the threshold counter."),
            ("+antinuke action ban/kick",  "Defines the punishment applied to nuke perpetrators."),
            ("+antiraid",                  "Displays anti-raid config. Toggles automated mass-join defense."),
            ("+automod antilink on/off",   "Toggles the anti-link filter — blocks unauthorized URLs instantly."),
            ("+automod antispam on/off",   "Toggles spam detection — auto-times out message flooders."),
            ("+lockdown",                  "Instantly locks all text channels to stop threats mid-action."),
            ("+unlock",                    "Lifts the lockdown and restores all channel permissions."),
            ("+whitelist add <@user>",     "Grants a user full bypass of all security systems (100% immune)."),
            ("+whitelist remove <@user>",  "Revokes a user's whitelist access immediately."),
            ("+whitelist list",            "Lists every currently whitelisted user."),
            ("+bypass add <@user|id>",     "Grants partial immunity — monitored against the abuse threshold."),
            ("+bypass remove <@user|id>",  "Revokes a user's bypass status, restoring full enforcement."),
            ("+bypass list",               "Lists every user with active bypass status."),
            ("+setprefix <prefix>",        "Changes the bot's command prefix (Owner only). Persists across restarts."),
            ("+setlog <#channel>",         "Sets the channel where all moderation/security events are logged."),
            ("+setlog",                    "Run with no channel to disable logging."),
            ("+autoreact on/off",          "Toggles auto-react globally (command confirmations + per-user reactions)."),
            ("+autoreact add @user :emoji:","Binds an emoji so the bot auto-reacts to every message that user sends."),
            ("+autoreact remove @user",    "Removes a user's auto-react binding."),
            ("+autoreact list",            "Shows global status and every user with an assigned auto-react emoji."),
        ],
    },
    "Aliases": {
        "emoji": "<:limit:1528578427517669580>",
        "tagline": "Create shortcuts for any command — server-wide or personal.",
        "commands": [
            ("+alias add <alias> <cmd>",        "Creates a server-wide alias (max 10 per target command, 30s cooldown)."),
            ("+alias remove <alias>",           "Removes a server alias."),
            ("+alias list",                     "Lists every alias configured in this server."),
            ("+alias self add <alias> <cmd>",   "Creates a personal alias that works for you in every server (max 10 per command)."),
            ("+alias self remove <alias>",      "Removes one of your personal aliases."),
            ("+alias self list",                "Lists your personal aliases."),
        ],
    },
    "Music": {
        "emoji": "<a:2m_music:1458242634513645732>",
        "tagline": "Lavalink v4 — YouTube search, playlists, interactive Now Playing buttons.",
        "commands": [
            ("+play <name|URL>",   "Search by song name or paste a URL to queue a track/playlist. Joins your VC automatically."),
            ("+pause",             "Toggles pause/resume on the current track."),
            ("+skip",              "Force-skips the current track and plays the next one in the queue."),
            ("+stop",              "Clears the queue and disconnects the bot from the voice channel."),
            ("+volume <1-100>",    "Sets playback volume (1–100). Persists until changed or the bot leaves."),
            ("+autoplay",          "Toggles Lavalink recommended-track continuation after the queue is exhausted."),
            ("+join",              "Joins your voice channel and stays indefinitely (24/7 mode)."),
            ("+leave",             "Disconnects the bot from the voice channel, stopping music if playing."),
        ],
    },
    "DJ Whitelist": {
        "emoji": "<:11pm_undeaf:1329408741514154036>",
        "tagline": "Control who can use music commands and interact with music embed buttons.",
        "commands": [
            ("+dj add <@user|id>",    "Adds a user to the DJ whitelist, granting access to all music commands and buttons. (Owner only)"),
            ("+dj remove <@user|id>", "Removes a user from the DJ whitelist, revoking their music access. (Owner only)"),
            ("+dj list",              "Displays an embed listing every user currently in the DJ whitelist."),
        ],
    },
    "Recovery": {
        "emoji": "<a:Warning:1527690506212081823>",
        "tagline": "Precision restoration — rebuild exactly what was destroyed.",
        # ⚠️  +massdm and +massban are intentionally omitted here.
        # They are DM-only owner commands and belong exclusively in +dm.
        "commands": [
            ("+loadrole <@role>",             "Deep-clones a role (Color, Hoist, Permissions, Icon) and bulk re-assigns it to all original members."),
            ("+clonerole <@role>",            "Alias for +loadrole — identical functionality, alternate syntax."),
            ("+loadchannel <#ch>",            "Clones a channel with every setting and all role/member permission overwrites perfectly restored."),
            ("+clonechannel <#ch>",           "Alias for +loadchannel — identical functionality, alternate syntax."),
            ("+backup create",                "Snapshots the entire server (roles, categories, channels, emojis, soundboard, members) and saves it with a unique Backup ID."),
            ("+backup list",                  "Displays an embed listing all saved backups — shows Backup ID, original server name, and creation date."),
            ("+backup load <id>",             "Opens the interactive multi-select restore UI: choose what to Wipe and what to Load, then click Validate & Start. Nothing is touched until you confirm."),
            ("+backup delete <id>",           "Permanently deletes a saved backup by its Backup ID."),
            ("+auto backup on",               "Enable 5-minute rolling auto-backup for this server. Each run replaces the previous snapshot automatically."),
            ("+auto backup off",              "Stop the auto-backup loop for this server."),
            ("+restore [guild_id]",           "(Legacy) Replays a guild-id-based snapshot into the current server without wiping first."),
            ("+cloneroles <source_guild_id>", "Copies all roles from another server's legacy backup into this server, sorted by exact hierarchical position."),
            ("+deleteroles",                  "Deletes all non-managed roles below the bot's top role. Skips @everyone, Nitro/integration roles, and any role above the bot. Requires Administrator permission."),
            ("+deletechannels",               "Deletes all channels and categories in the server. The command channel is always preserved. Requires Administrator permission."),
            ("+deleteemojis",                 "Deletes all custom emojis in the server. Requires Administrator permission."),
            ("+massrole <@role>",                        "Assigns the specified role to every member in the server. Shows live progress and a final count. Requires Administrator permission."),
            ("+massroleusers <@role> <...>",             "Assigns a role to a specific list of members (mentions or IDs). Example: `+massroleusers @Role @User1 @User2`. Requires Administrator permission."),
            ("+massreactchannel <limit> <emoji...>",     "Adds one or more emoji reactions to the last <limit> messages in the current channel (max 100). Requires Manage Messages."),
            ("+massreactuser <@user> <limit> <emoji...>","Scans the last <limit> messages and reacts to every message by the specified user (max 500). Requires Manage Messages."),
        ],
    },
    "Information": {
        "emoji": "<a:gh1y1nee:1458439134766039165>",
        "tagline": "Intelligence tools — inspect, verify, and monitor your server.",
        "commands": [
            ("+scaninvites",            "Scans all active invites and flags dangerous ones (unlimited uses, no expiry, etc.)."),
            ("+checkalt <@user>",       "Verifies a user's account age against the minimum threshold to detect alts."),
            ("+botinfo",                "Displays live ping, uptime, and all currently active security modules."),
            ("+userinfo <@user>",       "Shows full member details — roles, account age, join date, and whitelist status."),
            ("+serverinfo | +si",       "Displays a full server snapshot — members, channels, boosts, bans, verification, and more."),
            ("+roleinfo <@role> | +ri", "Displays detailed info for a role — color, position, permissions, and member count."),
            ("+searchuser [length]",    "Finds available Discord usernames of a given length (3–15 chars, default 5)."),
            ("+support",                "Sends contact information to reach the Trossard development team."),
            ("+setup",                  "Opens the interactive fast-setup wizard to configure all security modules."),
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  OWNER DM HELP  — shown only to the bot owner, only in DMs
#
#  Covers every DM-management and mass-action command that would be
#  inappropriate to expose in a public server context.
# ══════════════════════════════════════════════════════════════════════════════

DM_CATEGORIES: dict[str, dict] = {
    "Server Management": {
        "emoji": "🖥️",
        "tagline": "Remotely inspect and control any server the bot is in.",
        "commands": [
            ("+list",             "List every server the bot is currently in, with IDs."),
            ("+linkserver <id>",  "Return the vanity URL or generate a fresh permanent invite for a server."),
            ("+leftserver <id>",  "Force the bot to leave a server by its numeric ID."),
            ("+permserver <id>",  "Display every guild-level permission the bot holds in a server."),
            ("+serverinfo <id>",  "Pull a full server info embed remotely — same output as +serverinfo in-guild."),
        ],
    },
    "Channel & Role Tools": {
        "emoji": "⚙️",
        "tagline": "Surgically manage channels and roles via dropdown UI — no server visit needed.",
        "commands": [
            ("+deleteserverchannels <id>",        "Open a multi-select dropdown listing all channels in a server. Select any combination and the bot deletes them with a 350 ms delay between each."),
            ("+deleteserverroles <id>",           "Open a multi-select dropdown of deletable roles (non-managed, below bot's top). Select any and the bot deletes them with a 350 ms delay between each."),
            ("+manageroles <id>",                 "Open a role toggle menu for yourself in a server. ✅ = you have it (select to remove). ➕ = you don't (select to add). Menu stays active for multiple toggles."),
            ("+manageroles <id> <user_id>",       "Same toggle menu but targets another member. Specify their user ID as the second argument."),
        ],
    },
    "Mass Actions": {
        "emoji": "⚡",
        "tagline": "High-volume operations — handle with care and intent.",
        "commands": [
            ("+massdm <server_id> <limit> <msg>",  "Send <msg> to up to <limit> non-bot, non-admin members of <server_id>. Live progress tracker. 1 250 ms delay per DM."),
            ("+massban <server_id> <limit>",        "Ban up to <limit> non-bot, non-admin, non-owner members of <server_id>. Live tracker with batch edits. 200 ms delay per ban."),
        ],
    },
    "Auto-Backup": {
        "emoji": "💾",
        "tagline": "Automatic 5-minute rolling server snapshots — always up to date.",
        "commands": [
            ("+auto backup on",   "Enable 5-minute auto-backup for the current server. Runs continuously; each cycle deletes the old backup and writes a fresh one."),
            ("+auto backup off",  "Disable auto-backup and stop the interval loop for the current server. The last snapshot file remains on disk."),
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  Shared embed builders
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_commands(cmds: list[tuple], page: int) -> str:
    start = page * PAGE_SIZE
    chunk = cmds[start : start + PAGE_SIZE]
    lines = []
    for cmd, desc in chunk:
        lines.append(f"• __**{cmd}**__\n{desc}")
    return "\n\n".join(lines)


def _home_embed() -> discord.Embed:
    e = discord.Embed(
        title="Trossard ♱",
        description=(
            "Welcome, I'm Trossard ♱ a premium security bot for admins and I have "
            "powerful tools so I hope you're happy with my service."
            "\n\n"
            "*If you need any help, just use **+support** to reach the developers.*"
        ),
        color=COL_HOME,
    )
    e.set_image(url=HOME_IMAGE)
    e.set_footer(text=FOOTER)
    return e


def _category_embed(cat_name: str, page: int, *, categories: dict) -> discord.Embed:
    cat   = categories[cat_name]
    cmds  = cat["commands"]
    total = max(1, (len(cmds) + PAGE_SIZE - 1) // PAGE_SIZE)

    e = discord.Embed(
        title=f"Trossard ♱  —  {cat_name}",
        description=(
            f"*{cat['tagline']}*\n\n"
            f"{_fmt_commands(cmds, page)}"
        ),
        color=COL_CAT,
    )
    e.set_footer(text=f"{FOOTER}   ·   Page {page + 1} of {total}")
    return e


def _dm_home_embed() -> discord.Embed:
    """Home embed for the +dm owner DM menu."""
    e = discord.Embed(
        title="Trossard ♱  —  Owner DM Commands",
        description=(
            "This menu shows **all DM-management and owner-only commands**.\n"
            "These commands are hidden from the public `+help` menu.\n\n"
            "*Commands only work in DMs — using them in a server has no effect.*"
        ),
        color=COL_DM,
    )
    e.set_footer(text=FOOTER)
    return e


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC HELP UI
# ══════════════════════════════════════════════════════════════════════════════

class CategorySelect(discord.ui.Select):
    def __init__(self, categories: dict):
        self._categories = categories
        opts = [
            discord.SelectOption(
                label="Overview",
                value="__home__",
                description="Welcome page & bot introduction",
            ),
        ]
        for name, data in categories.items():
            opts.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=data["tagline"][:100],
                )
            )
        super().__init__(
            placeholder="Overview",
            options=opts,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        sel = self.values[0]
        v.current_category = None if sel == "__home__" else sel
        v.current_page = 0
        v._update_buttons()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class PrevButton(discord.ui.Button):
    def __init__(self, use_fallback: bool = False):
        super().__init__(
            emoji=_FB_PREV if use_fallback else _pe("<a:arrow_left_gn:1443948184266084402>"),
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        v.current_page = max(0, v.current_page - 1)
        v._update_buttons()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class PageLabel(discord.ui.Button):
    def __init__(self, use_fallback: bool = False):
        super().__init__(
            emoji=_FB_LABEL if use_fallback else _pe("<a:vrs_blackearth:1483195023280443577>"),
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class NextButton(discord.ui.Button):
    def __init__(self, use_fallback: bool = False):
        super().__init__(
            emoji=_FB_NEXT if use_fallback else _pe("<a:arrow_right_gn:1443948175391064147>"),
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        tp = v._total_pages()
        v.current_page = min(tp - 1, v.current_page + 1)
        v._update_buttons()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class HelpView(discord.ui.View):
    """
    Interactive public help view.
    • CategorySelect dropdown to switch between public categories.
    • Prev / Label / Next pagination buttons.
    • Owned by the invoking user (others get an ephemeral rejection).
    • Auto-disables after 120 s of inactivity.
    """

    def __init__(
        self,
        author_id: int,
        categories: dict,
        use_fallback: bool = False,
        home_embed_fn=None,
    ):
        super().__init__(timeout=120)
        self.author_id         = author_id
        self.categories        = categories
        self.home_embed_fn     = home_embed_fn or _home_embed
        self.current_category: str | None = None
        self.current_page      = 0
        self.message: discord.Message | None = None

        self._select = CategorySelect(categories)
        self._prev   = PrevButton(use_fallback=use_fallback)
        self._label  = PageLabel(use_fallback=use_fallback)
        self._next   = NextButton(use_fallback=use_fallback)

        for item in (self._select, self._prev, self._label, self._next):
            self.add_item(item)

        self._update_buttons()

    def _total_pages(self) -> int:
        if self.current_category is None:
            return 1
        cmds = self.categories[self.current_category]["commands"]
        return max(1, (len(cmds) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _update_buttons(self):
        # NOTE: deliberately NOT named _refresh — discord.py's View base class
        # has an internal _refresh(components) method.  Shadowing it causes a
        # TypeError on every MESSAGE_UPDATE event and crashes the bot process.
        tp      = self._total_pages()
        on_home = self.current_category is None
        self._prev.disabled  = on_home or self.current_page <= 0
        self._next.disabled  = on_home or self.current_page >= tp - 1
        self._label.disabled = True
        self._label.label    = f"{self.current_page + 1}/{tp}" if not on_home else None

    def _embed(self) -> discord.Embed:
        if self.current_category is None:
            return self.home_embed_fn()
        return _category_embed(self.current_category, self.current_page,
                               categories=self.categories)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This help menu belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════════════════════

class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self._start = time.time()

    # ── +help (public) ─────────────────────────────────────────────────────────

    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        """
        Interactive public help menu.

        Accessible to all server members — shows only safe, non-destructive
        commands.  Sensitive owner-DM commands (Mass DM, Mass Ban, server
        management tools) are completely hidden from this menu.

        The bot owner can also use +dm in DMs to see all owner-only commands.
        """
        view = HelpView(ctx.author.id, PUBLIC_CATEGORIES)
        try:
            view.message = await ctx.send(embed=_home_embed(), view=view)
        except discord.HTTPException as exc:
            # Discord rejects custom emojis the bot doesn't have access to (error 50035).
            # Re-send with unicode fallbacks.
            if exc.code == 50035:
                log.warning(
                    "Custom emojis rejected (50035) in +help — retrying with unicode fallbacks"
                )
                fb_view = HelpView(ctx.author.id, PUBLIC_CATEGORIES, use_fallback=True)
                fb_view.message = await ctx.send(embed=_home_embed(), view=fb_view)
            else:
                raise

    # ── +dm  (owner-only, DM-only) ─────────────────────────────────────────────

    @commands.command(name="dm")
    async def dm_help(self, ctx: commands.Context):
        """
        Owner-only DM command reference.

        Rules:
          • Must be used inside a Direct Message — silently ignored in servers.
          • Must be invoked by the bot owner — silently ignored for everyone else.

        Shows every DM-management and mass-action command that is hidden from
        the public +help menu.
        """
        # ── DM-only guard ──────────────────────────────────────────────────────
        if ctx.guild is not None:
            return   # Silently ignore — never acknowledge in server channels.

        # ── Owner-only guard ────────────────────────────────────────────────────
        if not db.is_owner(ctx.author.id):
            return   # Silently ignore — never reveal that this command exists.

        view = HelpView(
            ctx.author.id,
            DM_CATEGORIES,
            home_embed_fn=_dm_home_embed,
        )
        try:
            view.message = await ctx.send(embed=_dm_home_embed(), view=view)
        except discord.HTTPException as exc:
            if exc.code == 50035:
                log.warning(
                    "Custom emojis rejected (50035) in +dm — retrying with unicode fallbacks"
                )
                fb_view = HelpView(
                    ctx.author.id,
                    DM_CATEGORIES,
                    use_fallback=True,
                    home_embed_fn=_dm_home_embed,
                )
                fb_view.message = await ctx.send(embed=_dm_home_embed(), view=fb_view)
            else:
                raise

    # ── +checkalt ──────────────────────────────────────────────────────────────

    @commands.command(name="checkalt")
    async def checkalt(self, ctx: commands.Context, member: discord.Member):
        if not db.is_whitelisted(ctx.author.id):
            return
        age     = (discord.utils.utcnow() - member.created_at).days
        cfg     = db.get_config().get("altProtection", {})
        min_age = cfg.get("minAccountAge", 7)
        is_alt  = age < min_age

        e = discord.Embed(
            title=f"Alt Check — {member}",
            description=(
                f"• __**Account Age**__\n`{age}d`\n\n"
                f"• __**Minimum Required**__\n`{min_age}d`\n\n"
                f"• __**Account Created**__\n{discord.utils.format_dt(member.created_at, 'F')}\n\n"
                f"• __**Verdict**__\n{'`LIKELY ALT`' if is_alt else '`CLEAN`'}"
            ),
            color=COL_HOME,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── +botinfo ───────────────────────────────────────────────────────────────

    @commands.command(name="botinfo")
    async def botinfo(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        uptime_s   = int(time.time() - self._start)
        h, r       = divmod(uptime_s, 3600)
        m, s       = divmod(r, 60)
        uptime_str = f"{h}h {m}m {s}s"
        latency_ms = round(self.bot.latency * 1000, 2)

        cfg     = db.get_config()
        an      = cfg.get("antinuke", {})
        am      = cfg.get("automod", {})
        modules: list[str] = []
        if an.get("enabled", True):                            modules.append("Anti-Nuke v2")
        if am.get("antiLink", {}).get("enabled", True):       modules.append("Anti-Link")
        if am.get("antiSpam", {}).get("enabled", True):       modules.append("Anti-Spam")
        if am.get("antiRaid", {}).get("enabled", True):       modules.append("Anti-Raid")
        if cfg.get("altProtection", {}).get("enabled", True): modules.append("Alt Protection")

        modules_str = "\n".join(f"└ {mod}" for mod in modules) or "`None active`"

        e = discord.Embed(
            title="Trossard ♱  —  System Status",
            description=(
                f"• __**Latency**__\n`{latency_ms}ms`\n\n"
                f"• __**Uptime**__\n`{uptime_str}`\n\n"
                f"• __**Guilds**__\n`{len(self.bot.guilds)}`\n\n"
                f"• __**Active Modules**__\n{modules_str}"
            ),
            color=COL_HOME,
        )
        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── +support ───────────────────────────────────────────────────────────────

    @commands.command(name="support")
    async def support(self, ctx: commands.Context):
        e = discord.Embed(
            description=(
                "• __**Support & Socials**__\n"
                "If you need help or want to contact the developers, join our official server "
                "or reach out on Instagram."
            ),
            color=discord.Color(0x2B2D31),
        )
        e.set_footer(text=FOOTER)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Discord Server",
            url="https://discord.gg/hyRyjByyDn",
            style=discord.ButtonStyle.link,
        ))
        view.add_item(discord.ui.Button(
            label="Instagram",
            url="https://www.instagram.com/_svvalah_?igsh=MXB2cDVncW9ycjhiMw==",
            style=discord.ButtonStyle.link,
        ))
        await ctx.send(embed=e, view=view)

    # ── Error handlers ──────────────────────────────────────────────────────────

    @checkalt.error
    async def _checkalt_error(self, ctx, error):
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(
                embed=discord.Embed(description="Member not found.", color=COL_HOME),
                delete_after=5,
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=discord.Embed(description="Usage: `+checkalt <@user>`", color=COL_HOME),
                delete_after=5,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
