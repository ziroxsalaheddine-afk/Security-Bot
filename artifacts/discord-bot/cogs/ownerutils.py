"""
Owner Utility Commands — DM-only, bot owner only
═════════════════════════════════════════════════
+list               — list every server the bot is in
+linkserver <id>    — get a vanity URL or fresh invite for a server
+leftserver <id>    — make the bot leave a server
+permserver <id>    — list every permission the bot holds in a server
"""

import logging

import discord
from discord.ext import commands

log = logging.getLogger("guardian.ownerutils")

COL    = 0x2B2D31
FOOTER = "© 2026 — developed by zrx.gg"


def _e(description: str) -> discord.Embed:
    e = discord.Embed(description=description, color=COL)
    e.set_footer(text=FOOTER)
    return e


async def _resolve_guild(
    ctx: commands.Context, server_id: int
) -> discord.Guild | None:
    """Return the Guild from cache; send an error embed and return None if missing."""
    guild = ctx.bot.get_guild(server_id)
    if guild is None:
        await ctx.send(
            embed=_e(
                f"• __**Error**__\n"
                f"Server `{server_id}` not found. "
                f"The bot may not be a member of that server."
            )
        )
    return guild


class OwnerUtils(commands.Cog, name="OwnerUtils"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +list ──────────────────────────────────────────────────────────────────

    @commands.command(name="list")
    @commands.is_owner()
    async def list_servers(self, ctx: commands.Context):
        """DM-only: list every server the bot is currently in."""
        if ctx.guild is not None:
            return

        guilds = self.bot.guilds
        header = f"• __**Servers — {len(guilds)} total**__\n\n"
        lines  = [f"`{g.id}` — {g.name}" for g in guilds]

        # Build chunks that stay safely under Discord's 2 000-char limit.
        chunks: list[str] = []
        current = header
        for line in lines:
            candidate = current + line + "\n"
            if len(candidate) > 1990:
                chunks.append(current)
                current = line + "\n"
            else:
                current = candidate
        if current.strip():
            chunks.append(current)

        for chunk in chunks:
            await ctx.send(chunk)

    # ── +linkserver <server_id> ────────────────────────────────────────────────

    @commands.command(name="linkserver")
    @commands.is_owner()
    async def linkserver(self, ctx: commands.Context, server_id: int):
        """DM-only: return the vanity URL or create a permanent invite for a server."""
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Prefer vanity URL — no extra API call required.
        if guild.vanity_url_code:
            return await ctx.send(
                embed=_e(
                    f"• __**Vanity URL — {guild.name}**__\n"
                    f"https://discord.gg/{guild.vanity_url_code}"
                )
            )

        # Walk text channels until we find one where we can create an invite.
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                try:
                    invite = await channel.create_invite(
                        max_age=0,
                        max_uses=0,
                        reason="Owner utility: +linkserver",
                    )
                    return await ctx.send(
                        embed=_e(
                            f"• __**Invite — {guild.name}**__\n"
                            f"{invite.url}"
                        )
                    )
                except discord.Forbidden:
                    continue  # Try next channel.

        await ctx.send(
            embed=_e(
                f"• __**Error**__\n"
                f"Could not create an invite for **{guild.name}**. "
                f"No accessible text channel with `Create Invite` permission."
            )
        )

    # ── +leftserver <server_id> ────────────────────────────────────────────────

    @commands.command(name="leftserver")
    @commands.is_owner()
    async def leftserver(self, ctx: commands.Context, server_id: int):
        """DM-only: make the bot leave a server by ID."""
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        name = guild.name
        await guild.leave()
        log.info("Left guild '%s' (%s) via +leftserver by %s", name, server_id, ctx.author)
        await ctx.send(
            embed=_e(f"• __**Left Server**__\nSuccessfully left the server: **{name}**")
        )

    # ── +permserver <server_id> ────────────────────────────────────────────────

    @commands.command(name="permserver")
    @commands.is_owner()
    async def permserver(self, ctx: commands.Context, server_id: int):
        """DM-only: list every guild-level permission the bot holds in a server."""
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        perms = guild.me.guild_permissions
        granted = [
            name.replace("_", " ").title()
            for name, value in perms
            if value
        ]

        body = "\n".join(f"• `{p}`" for p in granted) or "`No permissions granted.`"
        await ctx.send(
            embed=_e(f"• __**Bot Permissions — {guild.name}**__\n\n{body}")
        )

    # ── Error handlers ─────────────────────────────────────────────────────────

    @list_servers.error
    @linkserver.error
    @leftserver.error
    @permserver.error
    async def _owner_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.NotOwner):
            return  # Silently ignore non-owner attempts.
        if isinstance(error, commands.MissingRequiredArgument):
            usage = {
                "linkserver": "`+linkserver <server_id>`",
                "leftserver": "`+leftserver <server_id>`",
                "permserver": "`+permserver <server_id>`",
            }
            await ctx.send(
                embed=_e(f"• __**Usage**__\n{usage.get(ctx.command.name, f'`+{ctx.command.name}`')}")
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.send(
                embed=_e("• __**Error**__\nInvalid server ID. Provide a plain numeric ID.")
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerUtils(bot))
