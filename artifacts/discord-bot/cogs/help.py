"""
Interactive Help System — Trossard
═══════════════════════════════════
discord.ui.View with:
  • StringSelect dropdown (Overview / Security Modules / Recovery / Information)
  • paginated command listing per category
  • Per-user ownership check
  • Auto-disables after 120 s
"""

import re
import time
import os as _os
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db


def _pe(emoji_str: str) -> discord.PartialEmoji:
    """Parse '<:name:id>' or '<a:name:id>' into a PartialEmoji."""
    m = re.fullmatch(r"<(a?):(\w+):(\d+)>", emoji_str.strip())
    if not m:
        raise ValueError(f"Cannot parse emoji: {emoji_str!r}")
    animated, name, eid = m.group(1) == "a", m.group(2), int(m.group(3))
    return discord.PartialEmoji(name=name, id=eid, animated=animated)


# Unicode fallbacks used when Discord rejects the custom emojis (error 50035).
# This happens when the bot is not a member of the server that owns the emoji.
CATEGORY_FALLBACKS: dict[str, str] = {
    "Security Modules": "🛡️",
    "Aliases":          "🔗",
    "Music":            "🎵",
    "DJ Whitelist":     "🎧",
    "Recovery":         "⚠️",
    "Information":      "ℹ️",
}
_FB_OVERVIEW = "🏠"
_FB_PREV     = "◀️"
_FB_LABEL    = "📄"
_FB_NEXT     = "▶️"

log = logging.getLogger("guardian.help")

# ── Constants ──────────────────────────────────────────────────────────────────
FOOTER        = "© 2026 — developed by zrx.gg"
BANNER_FILE   = _os.path.join(_os.path.dirname(__file__), "..", "assets", "banner.gif")
BANNER_ATTACH = "attachment://banner.gif"
PAGE_SIZE     = 4

# Home embed — cream/gothic palette
COL_HOME = 0xE2D6A5
# Category embeds — same cream to stay consistent
COL_CAT  = 0xE2D6A5

# The animated gif shown as the main image on the home embed
HOME_IMAGE = (
    "https://cdn.discordapp.com/attachments/1496697643127279646/"
    "1523507326923964456/f7812e1249081221bb80abb048698308.gif"
    "?ex=6a4c5c44&is=6a4b0ac4&hm=df7a87f24927938836303a2af8ba181a6e2d8e0dc65abd6114d125f9e072624e&"
)


# ── Category definitions ───────────────────────────────────────────────────────
CATEGORIES: dict[str, dict] = {
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
            ("+autoreact",                 "Run with no argument to check whether auto-react is currently on or off."),
            ("+autoreact add @user :emoji:", "Binds an emoji so the bot auto-reacts to every message that user sends."),
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
        "commands": [
            ("+loadrole <@role>",             "Deep-clones a role (Color, Hoist, Permissions, Icon) and bulk re-assigns it to all original members."),
            ("+clonerole <@role>",            "Alias for +loadrole — identical functionality, alternate syntax."),
            ("+loadchannel <#ch>",            "Clones a channel with every setting and all role/member permission overwrites perfectly restored."),
            ("+clonechannel <#ch>",           "Alias for +loadchannel — identical functionality, alternate syntax."),
            ("+backup create",                "Snapshots the entire server (roles, categories, channels, emojis, soundboard, members) and saves it with a unique Backup ID."),
            ("+backup list",                  "Displays an embed listing all saved backups — shows Backup ID, original server name, and creation date."),
            ("+backup load <id>",             "Opens the interactive multi-select restore UI: choose what to Wipe (Roles / Channels / Emojis / Soundboard) and what to Load independently, then click Validate & Start. Nothing is touched until you confirm."),
            ("+backup delete <id>",           "Permanently deletes a saved backup by its Backup ID."),
            ("+restore [guild_id]",           "(Legacy) Replays a guild-id-based snapshot into the current server without wiping first."),
            ("+cloneroles <source_guild_id>", "Copies all roles from another server's legacy backup into this server, sorted by exact hierarchical position."),
            ("+deleteroles",                  "Deletes all non-managed roles below the bot's top role. Skips @everyone, Nitro/integration roles, and any role above the bot. Requires Administrator permission."),
            ("+deletechannels",               "Deletes all channels and categories in the server. The command channel is always preserved so the bot can report completion. Requires Administrator permission."),
            ("+deleteemojis",                 "Deletes all custom emojis in the server. Requires Administrator permission."),
            ("+massrole <@role>",                        "Assigns the specified role to every member in the server. Shows live progress and a final count of assigned/skipped members. Requires Administrator permission."),
            ("+massroleusers <@role> <...>",             "Assigns a role to a specific list of members (mentions or IDs). Example: `+massroleusers @Role @User1 @User2`. Requires Administrator permission."),
            ("+massreactchannel <limit> <emoji...>",     "Adds one or more emoji reactions to the last <limit> messages in the current channel (max 100). Example: `+massreactchannel 10 👍 ❤️`. Requires Manage Messages."),
            ("+massreactuser <@user> <limit> <emoji...>","Scans the last <limit> messages (max 500) and reacts to every message sent by the specified user. Example: `+massreactuser @User 100 🔥`. Requires Manage Messages."),
            ("+massdm <server_id> <limit> <message>", "DM-only, owner-only. Sends <message> to up to <limit> non-bot, non-admin members of <server_id>. Live progress tracker. 1 250 ms delay per DM."),
            ("+massban <server_id> <limit>",          "DM-only, owner-only. Bans up to <limit> non-bot, non-admin, non-owner members of <server_id>. Live tracker with batch edits. 200 ms delay per ban."),
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
            ("+searchuser [length]",    "Finds available Discord usernames of a given length (3–15 chars, default 5). Checks availability via Discord's API."),
            ("+support",                "Sends contact information to reach the Trossard development team."),
            ("+setup",                  "Opens the interactive fast-setup wizard to configure all security modules."),
        ],
    },
}


# ── Embed builders ─────────────────────────────────────────────────────────────

def _home_embed() -> discord.Embed:
    e = discord.Embed(
        title="Trossard ♱",
        description=(
            "Welcome, I'm Trossard ♱ a premium security bot for admins and I have "
            "powerful tools so I hope you're happy with my service."
            "\n\n"
            "*if you need any help, just use **+support** cmd to report it to the developers*"
        ),
        color=COL_HOME,
    )
    e.set_image(url=HOME_IMAGE)
    e.set_footer(text=FOOTER)
    return e


def _fmt_commands(cmds: list[tuple], page: int) -> str:
    start = page * PAGE_SIZE
    chunk = cmds[start : start + PAGE_SIZE]
    lines = []
    for cmd, desc in chunk:
        lines.append(f"• __**{cmd}**__\n{desc}")
    return "\n\n".join(lines)


def _category_embed(cat_name: str, page: int) -> discord.Embed:
    cat   = CATEGORIES[cat_name]
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


# ── UI components ──────────────────────────────────────────────────────────────

class CategorySelect(discord.ui.Select):
    def __init__(self, use_fallback: bool = False):
        if use_fallback:
            overview_emoji: discord.PartialEmoji | str = _FB_OVERVIEW
        else:
            overview_emoji = _pe("<a:vrs_blackstar:1483194986622091505>")

        opts = [
            discord.SelectOption(
                label="Overview",
                value="__home__",
                description="Welcome page & bot introduction",
                emoji=overview_emoji,
            ),
        ]
        for name, data in CATEGORIES.items():
            if use_fallback:
                cat_emoji: discord.PartialEmoji | str = CATEGORY_FALLBACKS.get(name, "📁")
            else:
                cat_emoji = _pe(data["emoji"])
            opts.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=data["tagline"][:100],
                    emoji=cat_emoji,
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
    def __init__(self, author_id: int, use_fallback: bool = False):
        super().__init__(timeout=120)
        self.author_id         = author_id
        self.current_category: str | None = None
        self.current_page      = 0
        self.message: discord.Message | None = None

        self._select = CategorySelect(use_fallback=use_fallback)
        self._prev   = PrevButton(use_fallback=use_fallback)
        self._label  = PageLabel(use_fallback=use_fallback)
        self._next   = NextButton(use_fallback=use_fallback)

        for item in (self._select, self._prev, self._label, self._next):
            self.add_item(item)

        self._update_buttons()

    def _total_pages(self) -> int:
        if self.current_category is None:
            return 1
        cmds = CATEGORIES[self.current_category]["commands"]
        return max(1, (len(cmds) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _update_buttons(self):
        # NOTE: deliberately NOT named _refresh — discord.py's View base class
        # has an internal _refresh(components) method.  Shadowing it with a
        # zero-argument override causes a TypeError on every MESSAGE_UPDATE
        # event and crashes the entire bot process.
        tp      = self._total_pages()
        on_home = self.current_category is None
        self._prev.disabled  = on_home or self.current_page <= 0
        self._next.disabled  = on_home or self.current_page >= tp - 1
        self._label.disabled = True
        self._label.label    = f"{self.current_page + 1}/{tp}" if not on_home else None

    def _embed(self) -> discord.Embed:
        if self.current_category is None:
            return _home_embed()
        return _category_embed(self.current_category, self.current_page)

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


# ── Cog ────────────────────────────────────────────────────────────────────────

class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self._start = time.time()

    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        if not db.is_owner(ctx.author.id):
            owner_ids = db.get_owners()
            owner_id  = owner_ids[0] if owner_ids else 0
            await ctx.reply(
                f"owner only <@{owner_id}> <:locksc:1497746903394287626>"
            )
            return
        view = HelpView(ctx.author.id)
        try:
            view.message = await ctx.send(embed=_home_embed(), view=view)
        except discord.HTTPException as exc:
            # Discord rejects custom emojis the bot can't access (error 50035).
            # Rebuild the view with unicode fallbacks and retry once.
            if exc.code == 50035:
                log.warning(
                    "Custom emojis rejected by Discord (50035) — retrying with unicode fallbacks"
                )
                fallback_view = HelpView(ctx.author.id, use_fallback=True)
                fallback_view.message = await ctx.send(embed=_home_embed(), view=fallback_view)
            else:
                raise

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
        if an.get("enabled", True):                            modules.append("Anti-Nuke")
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
