"""
Interactive Help System — Trossard
═══════════════════════════════════
discord.ui.View with:
  • Category Select Menu (dropdown)
  • ⬅️  1/X  ➡️  pagination buttons
  • Per-category embeds with `+cmd` / ↳ desc formatting
  • Auto-disables after 120 s
"""

import time
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import db

log = logging.getLogger("guardian.help")

FOOTER = "© 2026 — Advanced Security by Trossard Shield"
BANNER = "https://i.imgur.com/wSTFkRM.gif"   # dark shield banner — swap freely
PAGE_SIZE = 4

# ── Category definitions ──────────────────────────────────────────────────────
#   Each entry: ("+command_name", "Description text")
CATEGORIES: dict[str, dict] = {
    "🛡️ Anti-Nuke & Security": {
        "emoji": "🛡️",
        "color": 0x1C1C3A,
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
    "🔄 Recovery & Deep Clone": {
        "emoji": "🔄",
        "color": 0x0D2B0D,
        "tagline": "Precision restoration — rebuild exactly what was destroyed.",
        "commands": [
            ("+loadrole <@role>",      "Deep-clones a role (Color, Hoist, Permissions, Icon) and bulk re-assigns it to all original members. Shows live execution time."),
            ("+clonerole <@role>",     "Alias for +loadrole — identical functionality, alternate syntax."),
            ("+loadchannel <#ch>",     "Clones a channel with every setting and all role/member permission overwrites perfectly restored."),
            ("+clonechannel <#ch>",    "Alias for +loadchannel — identical functionality, alternate syntax."),
        ],
    },
    "🔍 Investigation & Utility": {
        "emoji": "🔍",
        "color": 0x0D0D2B,
        "tagline": "Intelligence tools — inspect, verify, and monitor your server.",
        "commands": [
            ("+scaninvites",       "Scans all active invites and flags dangerous ones (unlimited uses, no expiry, etc.)."),
            ("+checkalt <@user>",  "Verifies a user's account age against the minimum threshold to detect alts."),
            ("+botinfo",           "Displays Trossard's live ping, uptime, and all currently active security modules."),
            ("+userinfo <@user>",  "Shows full member details — roles, account age, join date, and whitelist status."),
            ("+support",           "Sends contact information to reach the Trossard development team."),
        ],
    },
}


# ── Embed builders ────────────────────────────────────────────────────────────

def _home_embed() -> discord.Embed:
    e = discord.Embed(
        title="Trossard 🛡️ — Ultimate Server Protection",
        description=(
            "```\n"
            "◆  GUARDIAN PROTOCOL ACTIVE — ALL MODULES ONLINE\n"
            "```\n"
            "> Trossard is the **unbreakable shield** between your community and total\n"
            "> destruction. Built for **anti-nuke perfection** — threats are neutralized\n"
            "> before they can finish executing.\n\n"
            "Engineered with **sub-50ms** gateway audit-event detection, surgical\n"
            "role & channel auto-recovery, and a zero-trust architecture that gives\n"
            "admins absolute **peace of mind** around the clock.\n\n"
            "**⚡  Active Security Modules**\n"
            "` 🛡️ `  Anti-Nuke  ·  Gateway Audit Events (not REST)\n"
            "` 🔄 `  Deep Clone  ·  Instant Role & Channel Recovery\n"
            "` 🔒 `  Anti-Raid  ·  Auto-Lockdown & Raider Punishment\n"
            "` 🔍 `  Alt Detection  ·  Invite Scanner  ·  Anti-Link\n\n"
            "**Use the dropdown below** to explore every command category.\n"
            "-# If you need assistance, use the `+support` command to contact the developers."
        ),
        color=0x0A0A1E,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_image(url=BANNER)
    e.set_footer(text=FOOTER)
    return e


def _fmt_commands(cmds: list[tuple], page: int) -> str:
    start = page * PAGE_SIZE
    chunk = cmds[start : start + PAGE_SIZE]
    lines = []
    for cmd, desc in chunk:
        lines.append(f"`{cmd}`\n↳ {desc}")
    return "\n\n".join(lines)


def _category_embed(cat_name: str, page: int) -> discord.Embed:
    cat = CATEGORIES[cat_name]
    cmds = cat["commands"]
    total = max(1, (len(cmds) + PAGE_SIZE - 1) // PAGE_SIZE)

    e = discord.Embed(
        title=cat_name,
        description=(
            f"*{cat['tagline']}*\n"
            f"{'─' * 38}\n\n"
            f"{_fmt_commands(cmds, page)}"
        ),
        color=cat["color"],
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=f"{FOOTER}   ·   Page {page + 1} of {total}")
    return e


# ── UI components ─────────────────────────────────────────────────────────────

class CategorySelect(discord.ui.Select):
    def __init__(self):
        opts = [
            discord.SelectOption(
                label="Home",
                value="__home__",
                description="Overview & feature summary",
                emoji="🏠",
            )
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
            placeholder="📂   Select a command category...",
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
        super().__init__(emoji="⬅️", style=discord.ButtonStyle.secondary, row=1, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        v.current_page = max(0, v.current_page - 1)
        v._refresh()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class PageLabel(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◆", style=discord.ButtonStyle.primary, row=1, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="➡️", style=discord.ButtonStyle.secondary, row=1, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        v: HelpView = self.view  # type: ignore[assignment]
        tp = v._total_pages()
        v.current_page = min(tp - 1, v.current_page + 1)
        v._refresh()
        await interaction.response.edit_message(embed=v._embed(), view=v)


class HelpView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.current_category: str | None = None
        self.current_page = 0
        self.message: discord.Message | None = None

        self._select = CategorySelect()
        self._prev   = PrevButton()
        self._label  = PageLabel()
        self._next   = NextButton()

        for item in (self._select, self._prev, self._label, self._next):
            self.add_item(item)

        self._refresh()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _total_pages(self) -> int:
        if self.current_category is None:
            return 1
        cmds = CATEGORIES[self.current_category]["commands"]
        return max(1, (len(cmds) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _refresh(self):
        tp = self._total_pages()
        on_home = self.current_category is None
        self._prev.disabled  = on_home or self.current_page <= 0
        self._next.disabled  = on_home or self.current_page >= tp - 1
        self._label.disabled = True
        self._label.label    = "◆" if on_home else f"{self.current_page + 1}/{tp}"

    def _embed(self) -> discord.Embed:
        if self.current_category is None:
            return _home_embed()
        return _category_embed(self.current_category, self.current_page)

    # ── Checks / lifecycle ────────────────────────────────────────────────────

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌  This help menu belongs to someone else.", ephemeral=True
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


# ── Cog ───────────────────────────────────────────────────────────────────────

class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._start = time.time()

    # ── +help ─────────────────────────────────────────────────────────────────

    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        view = HelpView(ctx.author.id)
        view.message = await ctx.send(embed=_home_embed(), view=view)

    # ── +checkalt ─────────────────────────────────────────────────────────────

    @commands.command(name="checkalt")
    async def checkalt(self, ctx: commands.Context, member: discord.Member):
        if not db.is_whitelisted(ctx.author.id):
            return
        age = (discord.utils.utcnow() - member.created_at).days
        cfg = db.get_config().get("altProtection", {})
        min_age = cfg.get("minAccountAge", 7)
        is_alt = age < min_age

        e = discord.Embed(
            title=f"🔍  Alt Check — {member}",
            color=0xC0392B if is_alt else 0x1A5C2A,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Account Age",   value=f"`{age}d`",      inline=True)
        e.add_field(name="Min Required",  value=f"`{min_age}d`",  inline=True)
        e.add_field(
            name="Verdict",
            value="`⚠️  LIKELY ALT`" if is_alt else "`✅  CLEAN`",
            inline=True,
        )
        e.add_field(
            name="Account Created",
            value=discord.utils.format_dt(member.created_at, "F"),
            inline=False,
        )
        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── +botinfo ──────────────────────────────────────────────────────────────

    @commands.command(name="botinfo")
    async def botinfo(self, ctx: commands.Context):
        if not db.is_whitelisted(ctx.author.id):
            return
        uptime_s = int(time.time() - self._start)
        h, r = divmod(uptime_s, 3600)
        m, s = divmod(r, 60)
        uptime_str = f"{h}h {m}m {s}s"
        latency_ms = round(self.bot.latency * 1000, 2)

        cfg = db.get_config()
        an  = cfg.get("antinuke", {})
        am  = cfg.get("automod", {})

        modules: list[str] = []
        if an.get("enabled", True):                               modules.append("🛡️  Anti-Nuke")
        if am.get("antiLink", {}).get("enabled", True):          modules.append("🔗  Anti-Link")
        if am.get("antiSpam", {}).get("enabled", True):          modules.append("🚫  Anti-Spam")
        if am.get("antiRaid", {}).get("enabled", True):          modules.append("⚔️  Anti-Raid")
        if cfg.get("altProtection", {}).get("enabled", True):    modules.append("🔍  Alt Protection")

        e = discord.Embed(
            title="⚙️  Trossard — System Status",
            color=0xC0C0C0,
            timestamp=datetime.now(timezone.utc),
        )
        e.add_field(name="🏓  Latency",  value=f"`{latency_ms}ms`",      inline=True)
        e.add_field(name="⏱️  Uptime",   value=f"`{uptime_str}`",        inline=True)
        e.add_field(name="🏠  Guilds",   value=f"`{len(self.bot.guilds)}`", inline=True)
        e.add_field(
            name="✅  Active Modules",
            value="\n".join(f"> {mod}" for mod in modules) or "`None active`",
            inline=False,
        )
        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── +support ──────────────────────────────────────────────────────────────

    @commands.command(name="support")
    async def support(self, ctx: commands.Context):
        e = discord.Embed(
            title="💬  Trossard — Support",
            description=(
                "Need help with **Trossard**?\n\n"
                "Reach the development team through the official support server.\n\n"
                "> For security incidents, run `+botinfo` and `+scaninvites` first\n"
                "> to gather context before contacting support.\n\n"
                "> Attach screenshots of any embeds or error messages."
            ),
            color=0xC0C0C0,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text=FOOTER)
        await ctx.send(embed=e)

    # ── Error handlers ────────────────────────────────────────────────────────

    @checkalt.error
    async def _checkalt_error(self, ctx, error):
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(
                embed=discord.Embed(description="❌  Member not found.", color=0xC0392B),
                delete_after=5,
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=discord.Embed(description="❌  Usage: `+checkalt <@user>`", color=0xC0392B),
                delete_after=5,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
