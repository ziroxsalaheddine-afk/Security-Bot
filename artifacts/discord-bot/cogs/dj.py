"""
DJ Cog — Music whitelist management
════════════════════════════════════════════════════════════
+dj               — Show usage / subcommand help
+dj add <user>    — Add a user to the DJ whitelist (Owner only)
+dj remove <user> — Remove a user from the DJ whitelist (Owner only)
+dj list          — List all whitelisted DJ users
"""

import logging

import discord
from discord.ext import commands

from utils import db, dj_db, logs

log = logging.getLogger("guardian.dj")

COL    = 0x2B2D31
FOOTER = "© 2026 — developed by zrx.gg"


def _embed(desc: str) -> discord.Embed:
    return discord.Embed(description=desc, color=COL).set_footer(text=FOOTER)


class DJ(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +dj (group) ────────────────────────────────────────────────────────────

    @commands.group(name="dj", invoke_without_command=True)
    async def dj_group(self, ctx: commands.Context):
        await ctx.send(embed=_embed(
            "• __**DJ Whitelist**__\n\n"
            "`+dj add <@user|id>` — Add a user to the DJ whitelist\n"
            "`+dj remove <@user|id>` — Remove a user from the DJ whitelist\n"
            "`+dj list` — Show all current DJ users\n\n"
            "*Only the Bot Owner can add/remove DJ users.*\n"
            "*DJs can use all music commands and music embed buttons.*"
        ))

    # ── +dj add ────────────────────────────────────────────────────────────────

    @dj_group.command(name="add")
    async def dj_add(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            return
        if dj_db.is_dj(user.id):
            return await ctx.send(embed=_embed(
                f"• __**Already Added**__\n"
                f"{user.mention} (`{user.id}`) is already in the DJ whitelist."
            ))
        dj_db.add_dj(user.id)
        log.info("DJ added: %s (%d) by %s", user, user.id, ctx.author)
        await ctx.send(embed=_embed(
            f"• __**DJ Added**__\n"
            f"{user.mention} (`{user.id}`) has been added to the DJ whitelist.\n\n"
            "They can now use all music commands and interact with music embed buttons."
        ))
        await logs.send(
            self.bot, ctx.guild, "✅  DJ Whitelist — User Added",
            f"• __**User**__\n{user.mention}\n\n• __**Added By**__\n{ctx.author.mention}",
            user=user, color=logs.COL_SUCCESS,
        )

    # ── +dj remove ─────────────────────────────────────────────────────────────

    @dj_group.command(name="remove")
    async def dj_remove(self, ctx: commands.Context, user: discord.User):
        if not db.is_owner(ctx.author.id):
            return
        if not dj_db.is_dj(user.id):
            return await ctx.send(embed=_embed(
                f"• __**Not Found**__\n"
                f"{user.mention} (`{user.id}`) is not in the DJ whitelist."
            ))
        dj_db.remove_dj(user.id)
        log.info("DJ removed: %s (%d) by %s", user, user.id, ctx.author)
        await ctx.send(embed=_embed(
            f"• __**DJ Removed**__\n"
            f"{user.mention} (`{user.id}`) has been removed from the DJ whitelist.\n"
            "They can no longer use music commands or interact with music buttons."
        ))
        await logs.send(
            self.bot, ctx.guild, "🚫  DJ Whitelist — User Removed",
            f"• __**User**__\n{user.mention}\n\n• __**Removed By**__\n{ctx.author.mention}",
            user=user, color=logs.COL_DANGER,
        )

    # ── +dj list ───────────────────────────────────────────────────────────────

    @dj_group.command(name="list")
    async def dj_list(self, ctx: commands.Context):
        djs = dj_db.get_dj_list()
        if not djs:
            return await ctx.send(embed=_embed(
                "• __**DJ Whitelist**__\n"
                "No users are currently in the DJ whitelist.\n\n"
                "Use `+dj add <@user>` to add someone."
            ))

        lines: list[str] = []
        for uid in djs:
            user = ctx.bot.get_user(uid)
            tag  = f"{user} (`{uid}`)" if user else f"Unknown User (`{uid}`)"
            lines.append(f"• {tag}")

        e = discord.Embed(
            title="🎵  DJ Whitelist",
            description="\n".join(lines),
            color=COL,
        )
        e.set_footer(text=f"{FOOTER}  ·  {len(djs)} DJ(s) total")
        await ctx.send(embed=e)

    # ── Error handlers ─────────────────────────────────────────────────────────

    @dj_add.error
    async def _add_err(self, ctx: commands.Context, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(
                embed=_embed("• __**Usage**__\n`+dj add <@user or user_id>`"),
                delete_after=8,
            )

    @dj_remove.error
    async def _remove_err(self, ctx: commands.Context, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(
                embed=_embed("• __**Usage**__\n`+dj remove <@user or user_id>`"),
                delete_after=8,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(DJ(bot))
