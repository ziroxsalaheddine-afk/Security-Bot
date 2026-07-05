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

from utils import db, embeds

log = logging.getLogger("guardian.automod")

URL_RE = re.compile(
    r"(?:https?://|www\.)\S+|discord\.gg/\S+",
    re.IGNORECASE,
)


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
