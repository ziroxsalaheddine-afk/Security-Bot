"""
Interactive Help System — Trossard
═══════════════════════════════════
discord.ui.View with:
  • StringSelect dropdown (Overview / Security Modules / Recovery / Information)
  • paginated command listing per category
  • Per-user ownership check
  • Auto-disables after 120 s
"""

import time
import os as _os
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db

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
        "emoji": "<:vrs_security:1496957017858773133>",
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
            ("+whitelist add <@user>",     "Grants a user full bypass of all security systems."),
            ("+whitelist remove <@user>",  "Revokes a user's whitelist access immediately."),
            ("+whitelist list",            "Lists every currently whitelisted user."),
        ],
    },
    "Recovery": {
        "emoji": "<a:vrs_blackstar:1483194986622091505>",
        "tagline": "Precision restoration — rebuild exactly what was destroyed.",
        "commands": [
            ("+loadrole <@role>",      "Deep-clones a role (Color, Hoist, Permissions, Icon) and bulk re-assigns it to all original members."),
            ("+clonerole <@role>",     "Alias for +loadrole — identical functionality, alternate syntax."),
            ("+loadchannel <#ch>",     "Clones a channel with every setting and all role/member permission overwrites perfectly restored."),
            ("+clonechannel <#ch>",    "Alias for +loadchannel — identical functionality, alternate syntax."),
        ],
    },
    "Information": {
        "emoji": "<a:vrs_working:1498377074434506762>",
        "tagline": "Intelligence tools — inspect, verify, and monitor your server.",
        "commands": [
            ("+scaninvites",       "Scans all active invites and flags dangerous ones (unlimited uses, no expiry, etc.)."),
            ("+checkalt <@user>",  "Verifies a user's account age against the minimum threshold to detect alts."),
            ("+botinfo",           "Displays live ping, uptime, and all currently active security modules."),
            ("+userinfo <@user>",  "Shows full member details — roles, account age, join date, and whitelist status."),
            ("+support",           "Sends contact information to reach the Trossard development team."),
            ("+setup",             "Opens the interactive fast-setup wizard to configure all security modules."),
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
    def __init__(self):
        opts = [
            discord.SelectOption(
                label="Overview",
                value="__home__",
                description="Welcome page & bot introduction",
                emoji="<a:vrs_blackearth:1483195023280443577>",
            ),
        ]
        for name, data in CATEGORIES.items():
            opts.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=data["tagline"][:100],
                    emoji=data["emoji"],
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
        v._refresh()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class PrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            emoji="<a:vrs_arrow2:1483376240919314588>",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        v.current_page = max(0, v.current_page - 1)
        v._refresh()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class PageLabel(discord.ui.Button):
    def __init__(self):
        super().__init__(
            emoji="<a:ugh:1497199349460107425>",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            emoji="<a:arrowco:1401177337034309702>",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        tp = v._total_pages()
        v.current_page = min(tp - 1, v.current_page + 1)
        v._refresh()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class HelpView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id         = author_id
        self.current_category: str | None = None
        self.current_page      = 0
        self.message: discord.Message | None = None

        self._select = CategorySelect()
        self._prev   = PrevButton()
        self._label  = PageLabel()
        self._next   = NextButton()

        for item in (self._select, self._prev, self._label, self._next):
            self.add_item(item)

        self._refresh()

    def _total_pages(self) -> int:
        if self.current_category is None:
            return 1
        cmds = CATEGORIES[self.current_category]["commands"]
        return max(1, (len(cmds) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _refresh(self):
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
        if not db.is_whitelisted(ctx.author.id):
            return
        view = HelpView(ctx.author.id)
        view.message = await ctx.send(embed=_home_embed(), view=view)

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
