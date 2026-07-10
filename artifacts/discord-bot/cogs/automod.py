"""
AutoMod Cog — Anti-Link and Anti-Spam
"""

import re
import time
import logging
import datetime
from collections import defaultdict
from urllib.parse import urlparse

import discord
from discord.ext import commands

from utils import db, embeds, coowners, logs
from cogs.warden import send_warn_dm

log = logging.getLogger("guardian.automod")

URL_RE = re.compile(
    r"(?:https?://|www\.)\S+|discord\.gg/\S+",
    re.IGNORECASE,
)

# Strict Discord invite pattern — catches every common variant:
# discord.gg/xxx, discord.com/invite/xxx, discordapp.com/invite/xxx.
# Deliberately scoped to `/invite/` paths on discord.com/discordapp.com so
# unrelated discordapp.com links (CDN, API, etc.) are never false-flagged.
DISCORD_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:discord\.gg|dsc\.gg|discord(?:app)?\.com/invite)"
    r"/\S+",
    re.IGNORECASE,
)


def _has_invite_bypass(message: discord.Message) -> bool:
    """Bot Owner, Server Co-Owner, or 'Bypass' role holders are immune."""
    author = message.author
    if db.is_owner(author.id):
        return True
    if message.guild and coowners.is_coowner(message.guild.id, author.id):
        return True
    return any(r.name == "Bypass" for r in getattr(author, "roles", []))


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._spam: dict[tuple, list] = defaultdict(list)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if message.author.bot:
            return

        # ── Strict Discord invite filter ──────────────────────────────────────
        # Checked before the whitelist so the bypass logic is self-contained.
        if DISCORD_INVITE_RE.search(message.content) and not _has_invite_bypass(message):
            content_preview = message.content
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.channel.send(
                    f"{message.author.mention} ❌  Discord invite links are not allowed here.",
                    delete_after=5,
                )
            except Exception:
                pass

            reason = "Invite Link detected"
            await send_warn_dm(message.author, message.guild, reason)
            await logs.send(
                self.bot,
                message.guild,
                "🔗  Invite Link Blocked",
                f"• __**User**__\n{message.author.mention}\n\n"
                f"• __**Reason**__\n{reason}\n\n"
                f"• __**Message Content**__\n{discord.utils.escape_markdown(content_preview)[:1000]}",
                user=message.author,
                color=logs.COL_DANGER,
            )

            log.info(
                "Discord invite blocked from %s in %s",
                message.author,
                message.guild,
            )
            return

        # ── Global whitelist: immune from anti-link and anti-spam ─────────────
        if db.is_whitelisted(message.author.id):
            return

        cfg = db.get_config().get("automod", {})

        # ── Anti-Link ─────────────────────────────────────────────────────────
        link_cfg = cfg.get("antiLink", {})
        if link_cfg.get("enabled", True) and URL_RE.search(message.content):
            allowed = [d.lower().strip() for d in link_cfg.get("allowedDomains", [])]
            blocked = False
            for m in URL_RE.finditer(message.content):
                url = m.group(0)
                if not url.startswith("http"):
                    url = "https://" + url
                try:
                    netloc = urlparse(url).netloc.lower()
                    domain = netloc.removeprefix("www.")
                except Exception:
                    domain = ""
                if not any(domain == a or domain.endswith("." + a) for a in allowed):
                    blocked = True
                    break

            if blocked:
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    await message.channel.send(
                        f"{message.author.mention} ❌  Links are not allowed here.",
                        delete_after=5,
                    )
                except Exception:
                    pass
                return

        # ── Anti-Spam ─────────────────────────────────────────────────────────
        spam_cfg = cfg.get("antiSpam", {})
        if spam_cfg.get("enabled", True):
            now = time.time()
            key = (message.guild.id, message.author.id)
            limit = spam_cfg.get("messageLimit", 5)
            interval = spam_cfg.get("interval", 3)

            self._spam[key].append(now)
            self._spam[key] = [ts for ts in self._spam[key] if now - ts <= interval]

            if len(self._spam[key]) >= limit:
                self._spam[key] = []
                member = message.author
                try:
                    until = discord.utils.utcnow() + datetime.timedelta(minutes=5)
                    await member.timeout(until, reason="[Guardian] Anti-Spam: message flood")
                    try:
                        await message.channel.send(
                            f"{member.mention} ⏱️  You've been timed out for spamming.",
                            delete_after=8,
                        )
                    except Exception:
                        pass
                    log.info("Spam timeout: %s in %s", member, message.guild)

                    ch_id = db.get_log_channel()
                    if ch_id:
                        lch = message.guild.get_channel(ch_id)
                        if lch:
                            embed = embeds.danger(
                                "🚫  Spam Detected",
                                f"{member.mention} sent `{limit}+` messages in `{interval}s`.\n"
                                f"Timed out for **5 minutes**.",
                            )
                            await lch.send(embed=embed)
                except Exception as e:
                    log.error("Spam timeout failed: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
