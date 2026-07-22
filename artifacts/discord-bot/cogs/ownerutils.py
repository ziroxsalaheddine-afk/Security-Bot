"""
Owner Utility Commands — DM-only, bot owner only
═════════════════════════════════════════════════
+list                        — list every server the bot is in
+linkserver  <id>            — get a vanity URL or fresh invite for a server
+leftserver  <id>            — make the bot leave a server
+permserver  <id>            — list every permission the bot holds in a server
+serverinfo  <id>            — handled in information.py (shared command)
+deleteserverchannels <id>   — pick channels to delete via a multi-select dropdown
+deleteserverroles    <id>   — pick roles to delete via a multi-select dropdown
+manageroles <id> [user_id]  — toggle (add/remove) roles on yourself or another member
"""

import asyncio
import logging

import discord
from discord.ext import commands

log = logging.getLogger("guardian.ownerutils")

# ── Constants ───────────────────────────────────────────────────────────────────

COL          = 0x2B2D31
FOOTER       = "© 2026 — developed by zrx.gg"
DELETE_DELAY = 0.35   # seconds between every deletion (keeps us off rate-limit 429s)
SELECT_MAX   = 25     # Discord's absolute cap on options in a single StringSelect

# Emoji prefixes that appear in the channel-delete menu labels.
_CH_ICON: dict[discord.ChannelType, str] = {
    discord.ChannelType.text:          "💬",
    discord.ChannelType.voice:         "🔊",
    discord.ChannelType.category:      "📁",
    discord.ChannelType.news:          "📢",
    discord.ChannelType.stage_voice:   "🎙️",
    discord.ChannelType.forum:         "💬",
    discord.ChannelType.public_thread: "#",
    discord.ChannelType.private_thread:"#",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

def _e(description: str) -> discord.Embed:
    """Return a branded embed with a single description block."""
    e = discord.Embed(description=description, color=COL)
    e.set_footer(text=FOOTER)
    return e


async def _resolve_guild(
    ctx: commands.Context, server_id: int
) -> discord.Guild | None:
    """
    Return the Guild from the bot's cache.
    Sends an error embed and returns None if the bot isn't in that server.
    """
    guild = ctx.bot.get_guild(server_id)
    if guild is None:
        await ctx.send(
            embed=_e(
                f"• __**Error**__\n"
                f"Server `{server_id}` not found — the bot may not be a member of it."
            )
        )
    return guild


def _send_chunks(lines: list[str], header: str = "") -> list[str]:
    """
    Pack a list of lines into message-sized chunks (≤ 1990 chars each).
    Returns a list of strings ready to be sent individually.
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


# ══════════════════════════════════════════════════════════════════════════════
#  +deleteserverchannels UI
#  A multi-select dropdown that lists up to 25 channels.  When the owner
#  confirms their selection the bot deletes each channel with a 350 ms pause.
# ══════════════════════════════════════════════════════════════════════════════

class ChannelDeleteSelect(discord.ui.Select):
    """
    Multi-select StringSelect that lists guild channels.
    - min_values = 1   (must pick at least one)
    - max_values = number of options shown (up to SELECT_MAX)
    The callback fires once, with all chosen channel IDs in self.values.
    """

    def __init__(
        self,
        guild:    discord.Guild,
        channels: list[discord.abc.GuildChannel],
    ) -> None:
        self.guild = guild

        # Build one SelectOption per channel.  Label includes a type icon so
        # the owner can distinguish text / voice / category at a glance.
        options: list[discord.SelectOption] = []
        for ch in channels:
            icon  = _CH_ICON.get(ch.type, "•")
            label = f"{icon} {ch.name}"[:100]               # Discord label cap
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(ch.id),
                    description=f"Type: {ch.type.name}  •  ID: {ch.id}",
                )
            )

        super().__init__(
            placeholder=f"Select channels to delete  ({len(channels)} shown)…",
            min_values=1,
            max_values=len(options),   # allow selecting all shown channels
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # ── 1. Lock the view immediately so the owner can't submit twice. ──────
        for item in self.view.children:
            item.disabled = True

        selected_ids = [int(v) for v in self.values]
        total = len(selected_ids)

        # ── 2. Acknowledge the interaction and show a "working…" state. ────────
        await interaction.response.edit_message(
            embed=_e(
                f"• __**Deleting Channels — {self.guild.name}**__\n"
                f"Deleting `{total}` channel(s)…  `0 / {total}` done"
            ),
            view=self.view,
        )

        # ── 3. Delete each selected channel with a 350 ms delay in between. ───
        deleted, failed = 0, 0
        for ch_id in selected_ids:
            ch = self.guild.get_channel(ch_id)
            if ch is None:
                # Channel was already deleted or the cache is stale — count as failed.
                failed += 1
                log.warning("Channel %s not found in cache (already deleted?)", ch_id)
            else:
                try:
                    await ch.delete(reason="Owner utility: +deleteserverchannels")
                    deleted += 1
                    log.info(
                        "Deleted channel '%s' (%s) in guild '%s' (%s)",
                        ch.name, ch_id, self.guild.name, self.guild.id,
                    )
                except discord.Forbidden:
                    failed += 1
                    log.warning("Forbidden: cannot delete channel '%s' (%s)", ch.name, ch_id)
                except discord.HTTPException as exc:
                    failed += 1
                    log.warning("HTTPException deleting channel %s: %s", ch_id, exc)

            # Mandatory 350 ms pause between every deletion to stay off 429s.
            await asyncio.sleep(DELETE_DELAY)

        # ── 4. Edit the original message to show the final tally. ─────────────
        await interaction.edit_original_response(
            embed=_e(
                f"• __**Done — {self.guild.name}**__\n"
                f"Deleted: `{deleted}` · Failed / Not Found: `{failed}`"
            ),
            view=None,   # remove the now-useless select menu
        )
        self.view.stop()


class ChannelDeleteView(discord.ui.View):
    """
    Container view for ChannelDeleteSelect.
    - Only the bot owner may interact with it (interaction_check).
    - Times out after 120 s if the owner never picks anything.
    """

    def __init__(
        self,
        guild:    discord.Guild,
        channels: list[discord.abc.GuildChannel],
        owner_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(ChannelDeleteSelect(guild, channels))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This menu is not for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        # The message lives in a DM — Discord doesn't allow editing DM messages
        # after a view times out without holding the message object, so we
        # simply swallow the timeout silently.
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  +deleteserverroles UI
#  Same pattern as the channel-delete UI but filters to only deletable roles.
# ══════════════════════════════════════════════════════════════════════════════

class RoleDeleteSelect(discord.ui.Select):
    """
    Multi-select StringSelect that lists only the roles the bot is allowed to
    delete (non-managed, not @everyone, below the bot's own top role).
    """

    def __init__(
        self,
        guild: discord.Guild,
        roles: list[discord.Role],
    ) -> None:
        self.guild = guild

        options: list[discord.SelectOption] = [
            discord.SelectOption(
                label=role.name[:100],
                value=str(role.id),
                description=f"Position: {role.position}  •  ID: {role.id}",
            )
            for role in roles
        ]

        super().__init__(
            placeholder=f"Select roles to delete  ({len(roles)} shown)…",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # ── 1. Lock the view immediately. ──────────────────────────────────────
        for item in self.view.children:
            item.disabled = True

        selected_ids = [int(v) for v in self.values]
        total = len(selected_ids)

        # ── 2. Acknowledge and show a working state. ────────────────────────────
        await interaction.response.edit_message(
            embed=_e(
                f"• __**Deleting Roles — {self.guild.name}**__\n"
                f"Deleting `{total}` role(s)…  `0 / {total}` done"
            ),
            view=self.view,
        )

        # ── 3. Delete each role with a 350 ms delay between each. ──────────────
        deleted, failed = 0, 0
        for role_id in selected_ids:
            role = self.guild.get_role(role_id)
            if role is None:
                failed += 1
                log.warning("Role %s not found in cache (already deleted?)", role_id)
            else:
                try:
                    await role.delete(reason="Owner utility: +deleteserverroles")
                    deleted += 1
                    log.info(
                        "Deleted role '%s' (%s) in guild '%s' (%s)",
                        role.name, role_id, self.guild.name, self.guild.id,
                    )
                except discord.Forbidden:
                    failed += 1
                    log.warning("Forbidden: cannot delete role '%s' (%s)", role.name, role_id)
                except discord.HTTPException as exc:
                    failed += 1
                    log.warning("HTTPException deleting role %s: %s", role_id, exc)

            await asyncio.sleep(DELETE_DELAY)

        # ── 4. Show final tally. ────────────────────────────────────────────────
        await interaction.edit_original_response(
            embed=_e(
                f"• __**Done — {self.guild.name}**__\n"
                f"Deleted: `{deleted}` · Failed / Not Found: `{failed}`"
            ),
            view=None,
        )
        self.view.stop()


class RoleDeleteView(discord.ui.View):
    """Container view for RoleDeleteSelect. Owner-only, 120 s timeout."""

    def __init__(
        self,
        guild:    discord.Guild,
        roles:    list[discord.Role],
        owner_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(RoleDeleteSelect(guild, roles))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This menu is not for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  +manageroles UI
#  Single-select that lists assignable roles annotated with the target member's
#  current role state.  Selecting a role toggles it on or off.  The menu
#  remains active after each toggle so the owner can modify multiple roles
#  in one session without re-running the command.
# ══════════════════════════════════════════════════════════════════════════════

class ManageRoleSelect(discord.ui.Select):
    """
    Single-select role toggle menu.

    Label format:
      ✅ Role Name  →  member currently HAS the role  (selecting removes it)
      ➕ Role Name  →  member does NOT have the role (selecting adds it)

    After each interaction the member object is re-fetched so the indicators
    stay accurate across multiple selections in the same session.
    """

    def __init__(
        self,
        guild:          discord.Guild,
        member:         discord.Member,
        eligible_roles: list[discord.Role],
        owner_id:       int,
    ) -> None:
        self.guild          = guild
        self.member         = member
        self.eligible_roles = eligible_roles
        self.owner_id       = owner_id

        super().__init__(
            placeholder="Select a role to add or remove…",
            min_values=1,
            max_values=1,         # one role per interaction — keeps toggle logic unambiguous
            options=self._build_options(),
        )

    # ── Option builder ─────────────────────────────────────────────────────────

    def _build_options(self) -> list[discord.SelectOption]:
        """
        Rebuild option list based on the member's current roles.
        ✅ = already has the role, ➕ = doesn't have it yet.
        Called on first render and again after each successful toggle.
        """
        member_role_ids = {r.id for r in self.member.roles}
        options: list[discord.SelectOption] = []

        for role in self.eligible_roles:
            has_role = role.id in member_role_ids
            label    = f"{'✅' if has_role else '➕'} {role.name}"[:100]
            desc     = (
                f"Click to REMOVE  •  ID: {role.id}"
                if has_role
                else f"Click to ADD  •  ID: {role.id}"
            )
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(role.id),
                    description=desc,
                )
            )

        return options

    # ── Callback ────────────────────────────────────────────────────────────────

    async def callback(self, interaction: discord.Interaction) -> None:
        role_id = int(self.values[0])
        role    = self.guild.get_role(role_id)

        if role is None:
            # Role was deleted between the menu being sent and now.
            return await interaction.response.edit_message(
                embed=_e(
                    "• __**Error**__\n"
                    "That role no longer exists in the server. "
                    "Re-run `+manageroles` to get a fresh menu."
                ),
                view=None,
            )

        # ── Determine current state and toggle. ────────────────────────────────
        member_role_ids = {r.id for r in self.member.roles}
        has_role        = role_id in member_role_ids

        try:
            if has_role:
                await self.member.remove_roles(role, reason="Owner utility: +manageroles")
                action = f"✅ → ➕  Removed **{role.name}** from **{self.member.display_name}**."
                log.info(
                    "Removed role '%s' (%s) from member %s in guild '%s' (%s)",
                    role.name, role_id, self.member.id, self.guild.name, self.guild.id,
                )
            else:
                await self.member.add_roles(role, reason="Owner utility: +manageroles")
                action = f"➕ → ✅  Added **{role.name}** to **{self.member.display_name}**."
                log.info(
                    "Added role '%s' (%s) to member %s in guild '%s' (%s)",
                    role.name, role_id, self.member.id, self.guild.name, self.guild.id,
                )

        except discord.Forbidden:
            return await interaction.response.edit_message(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Cannot {'remove' if has_role else 'add'} **{role.name}** — "
                    f"it may be above the bot's highest role or the bot lacks `Manage Roles`."
                ),
                view=None,
            )
        except discord.HTTPException as exc:
            return await interaction.response.edit_message(
                embed=_e(f"• __**Error**__\nDiscord API error: `{exc}`"),
                view=None,
            )

        # ── Re-fetch the member so the indicators reflect the new role state. ──
        try:
            self.member = await self.guild.fetch_member(self.member.id)
        except discord.HTTPException:
            pass  # Proceed with stale member — indicators may be off by one action.

        # ── Rebuild options with updated ✅/➕ markers and refresh the message. ─
        self.options = self._build_options()

        target_line = (
            f"`{self.member.display_name}` (`{self.member.id}`) "
            f"in **{self.guild.name}**"
        )
        desc = (
            f"• __**Manage Roles — {self.guild.name}**__\n"
            f"Target: {target_line}\n\n"
            f"✅ = has role (select to **remove**) · ➕ = no role (select to **add**)\n\n"
            f"**Last action:** {action}"
        )

        # Respond by editing the original message in place — the menu stays open.
        await interaction.response.edit_message(embed=_e(desc), view=self.view)


class ManageRoleView(discord.ui.View):
    """Container view for ManageRoleSelect. Owner-only, 120 s timeout."""

    def __init__(
        self,
        guild:          discord.Guild,
        member:         discord.Member,
        eligible_roles: list[discord.Role],
        owner_id:       int,
    ) -> None:
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(ManageRoleSelect(guild, member, eligible_roles, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This menu is not for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════════════════════

class OwnerUtils(commands.Cog, name="OwnerUtils"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── +list ───────────────────────────────────────────────────────────────────

    @commands.command(name="list")
    @commands.is_owner()
    async def list_servers(self, ctx: commands.Context) -> None:
        """DM-only: list every server the bot is currently in."""
        if ctx.guild is not None:
            return

        guilds = self.bot.guilds
        lines  = [f"`{g.id}` — {g.name}" for g in guilds]
        header = f"• __**Servers — {len(guilds)} total**__\n\n"

        for chunk in _send_chunks(lines, header):
            await ctx.send(chunk)

    # ── +linkserver <server_id> ─────────────────────────────────────────────────

    @commands.command(name="linkserver")
    @commands.is_owner()
    async def linkserver(self, ctx: commands.Context, server_id: int) -> None:
        """DM-only: return the vanity URL or create a permanent invite for a server."""
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Prefer the vanity URL — already cached, no extra API call.
        if guild.vanity_url_code:
            return await ctx.send(
                embed=_e(
                    f"• __**Vanity URL — {guild.name}**__\n"
                    f"https://discord.gg/{guild.vanity_url_code}"
                )
            )

        # Walk text channels until we find one we can create an invite for.
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                try:
                    invite = await channel.create_invite(
                        max_age=0,
                        max_uses=0,
                        reason="Owner utility: +linkserver",
                    )
                    return await ctx.send(
                        embed=_e(f"• __**Invite — {guild.name}**__\n{invite.url}")
                    )
                except discord.Forbidden:
                    continue

        await ctx.send(
            embed=_e(
                f"• __**Error**__\n"
                f"Could not create an invite for **{guild.name}** — "
                f"no accessible text channel with `Create Invite` permission."
            )
        )

    # ── +leftserver <server_id> ─────────────────────────────────────────────────

    @commands.command(name="leftserver")
    @commands.is_owner()
    async def leftserver(self, ctx: commands.Context, server_id: int) -> None:
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
            embed=_e(f"• __**Left Server**__\nSuccessfully left **{name}**.")
        )

    # ── +permserver <server_id> ─────────────────────────────────────────────────

    @commands.command(name="permserver")
    @commands.is_owner()
    async def permserver(self, ctx: commands.Context, server_id: int) -> None:
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
        await ctx.send(embed=_e(f"• __**Bot Permissions — {guild.name}**__\n\n{body}"))

    # ── +deleteserverchannels <server_id> ───────────────────────────────────────

    @commands.command(name="deleteserverchannels")
    @commands.is_owner()
    async def deleteserverchannels(
        self, ctx: commands.Context, server_id: int
    ) -> None:
        """
        DM-only: present a multi-select dropdown of the server's channels.
        The owner picks which ones to delete; the bot deletes them one by one
        with a 350 ms pause between each to stay off rate-limit 429s.

        If the server has more than 25 channels Discord's select-menu cap limits
        the list to the first 25 sorted by position.  The note in the embed tells
        the owner how many additional channels were not shown.
        """
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Verify the bot can actually delete channels in this server.
        if not guild.me.guild_permissions.manage_channels:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Missing `Manage Channels` permission in **{guild.name}**."
                )
            )

        # Sort by position so the list matches what members see in the sidebar.
        all_channels: list[discord.abc.GuildChannel] = sorted(
            guild.channels, key=lambda c: c.position
        )

        if not all_channels:
            return await ctx.send(
                embed=_e(f"• __**Error**__\nNo channels found in **{guild.name}**.")
            )

        # Slice to Discord's 25-option hard cap.
        shown   = all_channels[:SELECT_MAX]
        clipped = len(all_channels) - len(shown)

        desc = (
            f"• __**Delete Channels — {guild.name}**__\n"
            f"Select one or more channels from the menu below.\n"
            f"The bot will delete them with a `350 ms` delay between each.\n"
            + (
                f"\n⚠️ *`{clipped}` channel(s) not shown — Discord limits "
                f"dropdowns to {SELECT_MAX} options.*"
                if clipped
                else ""
            )
        )

        view = ChannelDeleteView(guild, shown, ctx.author.id)
        await ctx.send(embed=_e(desc), view=view)

    # ── +deleteserverroles <server_id> ──────────────────────────────────────────

    @commands.command(name="deleteserverroles")
    @commands.is_owner()
    async def deleteserverroles(
        self, ctx: commands.Context, server_id: int
    ) -> None:
        """
        DM-only: present a multi-select dropdown of the server's deletable roles.
        Automatically excludes @everyone, managed/bot-integration roles, and any
        role at or above the bot's own top role.

        The owner picks which roles to delete; the bot deletes them one by one
        with a 350 ms pause between each.
        """
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Verify the bot has Manage Roles.
        if not guild.me.guild_permissions.manage_roles:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Missing `Manage Roles` permission in **{guild.name}**."
                )
            )

        bot_top_pos = guild.me.top_role.position  # bot can't touch roles at/above its own

        def _is_deletable(role: discord.Role) -> bool:
            if role.is_default():           return False  # @everyone
            if role.managed:                return False  # integration / bot role
            if role.position >= bot_top_pos: return False  # above or equal to bot's top role
            return True

        # Sort by position descending (highest roles first, matching Discord's UI).
        eligible = [
            r for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if _is_deletable(r)
        ]

        if not eligible:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"No deletable roles found in **{guild.name}**.\n"
                    f"All roles are managed, above the bot's top role, or @everyone."
                )
            )

        # Slice to Discord's 25-option cap.
        shown   = eligible[:SELECT_MAX]
        clipped = len(eligible) - len(shown)

        desc = (
            f"• __**Delete Roles — {guild.name}**__\n"
            f"Select one or more roles from the menu below.\n"
            f"The bot will delete them with a `350 ms` delay between each.\n"
            f"*(Managed roles, @everyone, and roles above the bot's position "
            f"are automatically excluded.)*"
            + (
                f"\n\n⚠️ *`{clipped}` role(s) not shown — Discord limits "
                f"dropdowns to {SELECT_MAX} options.*"
                if clipped
                else ""
            )
        )

        view = RoleDeleteView(guild, shown, ctx.author.id)
        await ctx.send(embed=_e(desc), view=view)

    # ── +manageroles <server_id> [target_user_id] ───────────────────────────────

    @commands.command(name="manageroles")
    @commands.is_owner()
    async def manageroles(
        self,
        ctx:            commands.Context,
        server_id:      int,
        target_user_id: int | None = None,
    ) -> None:
        """
        DM-only: add or remove roles on yourself (or another member) via a
        single-select dropdown.

        Usage:
          +manageroles <server_id>                  — target = bot owner
          +manageroles <server_id> <target_user_id> — target = specified member

        How it works:
          • ✅ prefix → member already has the role → selecting it REMOVES it
          • ➕ prefix → member doesn't have the role → selecting it ADDS it
          The menu stays live after each action so you can toggle multiple
          roles in one session without re-running the command.
        """
        if ctx.guild is not None:
            return

        guild = await _resolve_guild(ctx, server_id)
        if guild is None:
            return

        # Verify the bot can manage roles in this server.
        if not guild.me.guild_permissions.manage_roles:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"Missing `Manage Roles` permission in **{guild.name}**."
                )
            )

        # Default target is the owner themselves.
        target_id = target_user_id if target_user_id is not None else ctx.author.id

        # Fetch the target member — they must actually be in the guild.
        try:
            member = await guild.fetch_member(target_id)
        except discord.NotFound:
            noun = "You are" if target_id == ctx.author.id else f"User `{target_id}` is"
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"{noun} not a member of **{guild.name}**."
                )
            )
        except discord.HTTPException as exc:
            return await ctx.send(
                embed=_e(f"• __**Error**__\nFailed to fetch member: `{exc}`")
            )

        bot_top_pos = guild.me.top_role.position

        # Collect roles the bot is actually allowed to assign/remove.
        eligible = [
            role
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if not role.is_default()
            and not role.managed
            and role.position < bot_top_pos
        ]

        if not eligible:
            return await ctx.send(
                embed=_e(
                    f"• __**Error**__\n"
                    f"No assignable roles found in **{guild.name}**.\n"
                    f"All roles are managed, above the bot's top role, or @everyone."
                )
            )

        # Slice to Discord's 25-option cap.
        shown   = eligible[:SELECT_MAX]
        clipped = len(eligible) - len(shown)

        target_line = f"`{member.display_name}` (`{member.id}`) in **{guild.name}**"
        desc = (
            f"• __**Manage Roles — {guild.name}**__\n"
            f"Target: {target_line}\n\n"
            f"✅ = has role (select to **remove**) · ➕ = no role (select to **add**)"
            + (
                f"\n\n⚠️ *`{clipped}` role(s) not shown — Discord limits "
                f"dropdowns to {SELECT_MAX} options.*"
                if clipped
                else ""
            )
        )

        view = ManageRoleView(guild, member, shown, ctx.author.id)
        await ctx.send(embed=_e(desc), view=view)

    # ── Shared error handler ────────────────────────────────────────────────────

    @list_servers.error
    @linkserver.error
    @leftserver.error
    @permserver.error
    @deleteserverchannels.error
    @deleteserverroles.error
    @manageroles.error
    async def _owner_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.NotOwner):
            return  # Silently ignore all non-owner invocations.

        if isinstance(error, commands.MissingRequiredArgument):
            usage_map = {
                "linkserver":           "`+linkserver <server_id>`",
                "leftserver":           "`+leftserver <server_id>`",
                "permserver":           "`+permserver <server_id>`",
                "deleteserverchannels": "`+deleteserverchannels <server_id>`",
                "deleteserverroles":    "`+deleteserverroles <server_id>`",
                "manageroles":          "`+manageroles <server_id> [target_user_id]`",
            }
            await ctx.send(
                embed=_e(
                    f"• __**Usage**__\n"
                    f"{usage_map.get(ctx.command.name, f'`+{ctx.command.name}`')}"
                )
            )

        elif isinstance(error, commands.BadArgument):
            await ctx.send(
                embed=_e(
                    "• __**Error**__\n"
                    "Invalid argument — server and user IDs must be plain numeric values."
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OwnerUtils(bot))
