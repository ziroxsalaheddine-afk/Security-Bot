"""
Danger Cog — Unauthorized role assignment protection & mass-mention blocking.

Danger Roles (+danger roles …):
  - Marks specific roles as "protected".
  - on_member_update: when a protected role (or any Administrator role) is
    granted by an unauthorized executor, the role is immediately stripped from
    the recipient and a warning is posted.

Danger Tags (+danger tag …):
  - Maintains a per-guild list of users permitted to use @everyone / @here.
  - on_message: if a message contains a mass mention (@everyone or @here) and
    the author is neither whitelisted nor in danger_tags, the message is deleted
    instantly and a temporary warning is sent.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from database import Database
from utils import error_embed, get_audit_executor, info_embed, is_whitelisted, success_embed, warn_embed

log = logging.getLogger("secbot.danger")


class Danger(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    # ══════════════════════════════════════════════════════════════════════════
    #  +danger — top-level group
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(name="danger", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def danger(self, ctx: commands.Context) -> None:
        embed = info_embed(
            "Danger Module",
            "**Subcommands:**\n"
            "`+danger roles` — manage protected/admin roles\n"
            "`+danger tag` — manage who may use @everyone / @here\n\n"
            "Use `+help` for the full command reference.",
        )
        await ctx.send(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  +danger roles — protected role management
    # ══════════════════════════════════════════════════════════════════════════

    @danger.group(name="roles", invoke_without_command=True)
    @commands.guild_only()
    async def danger_roles(self, ctx: commands.Context) -> None:
        """List all roles currently marked as protected."""
        rows = await self.db.danger_role_list(ctx.guild.id)
        if not rows:
            return await ctx.send(
                embed=info_embed("Danger Roles", "No roles are currently marked as protected.")
            )

        lines = []
        for row in rows:
            role = ctx.guild.get_role(row["role_id"])
            label = role.mention if role else f"`{row['role_id']}`"
            has_admin = " *(Admin)*" if role and role.permissions.administrator else ""
            lines.append(f"• {label}{has_admin}")

        embed = discord.Embed(
            title="⚠️  Protected (Danger) Roles",
            description="\n".join(lines),
            color=0xE67E22,
        )
        embed.set_footer(text=f"Security Bot • {len(rows)} protected role(s)")
        await ctx.send(embed=embed)

    @danger_roles.command(name="add")
    @commands.guild_only()
    async def danger_roles_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Mark a role as protected — unauthorized assignment will be reversed."""
        await self.db.danger_role_add(ctx.guild.id, role.id)
        log.info("Danger role added: guild=%d role=%d (%s) by %s", ctx.guild.id, role.id, role.name, ctx.author)
        await ctx.send(
            embed=success_embed(
                "Danger Role Added",
                f"{role.mention} is now **protected**.\n"
                "Any unauthorized member granted this role will have it removed instantly.",
            )
        )

    @danger_roles.command(name="remove", aliases=["rm"])
    @commands.guild_only()
    async def danger_roles_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Remove a role from the protected list."""
        removed = await self.db.danger_role_remove(ctx.guild.id, role.id)
        if not removed:
            return await ctx.send(
                embed=warn_embed("Not Found", f"{role.mention} was not in the protected list."),
                delete_after=8,
            )
        log.info("Danger role removed: guild=%d role=%d (%s) by %s", ctx.guild.id, role.id, role.name, ctx.author)
        await ctx.send(
            embed=success_embed("Danger Role Removed", f"{role.mention} is no longer protected.")
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  on_member_update — unauthorized danger role guard
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        gained = set(after.roles) - set(before.roles)
        if not gained:
            return

        guild = after.guild

        for role in gained:
            is_danger = await self.db.danger_role_check(guild.id, role.id)
            has_admin = role.permissions.administrator

            if not is_danger and not has_admin:
                continue  # Not a protected role — no action needed.

            # Identify who granted the role.
            executor = await get_audit_executor(
                guild, discord.AuditLogAction.member_role_update, after.id, limit=5
            )

            # Skip if executor is whitelisted or is the guild owner.
            if executor:
                if executor.id == guild.owner_id or executor.id == self.bot.user.id:
                    continue
                exec_member = guild.get_member(executor.id)
                if exec_member and await is_whitelisted(self.db, guild, exec_member):
                    continue

            # Remove the unauthorized role immediately.
            reason = "[Security Bot] Unauthorized danger/admin role assignment reversed"
            try:
                await after.remove_roles(role, reason=reason)
                log.warning(
                    "Danger role stripped: guild=%d member=%d role=%r executor=%s",
                    guild.id, after.id, role.name, executor,
                )
            except discord.Forbidden:
                log.error("Forbidden removing danger role %r from %d in guild %d", role.name, after.id, guild.id)
                continue
            except Exception as exc:
                log.error("Failed to strip danger role: %s", exc)
                continue

            # Post a warning in the first available channel.
            embed = discord.Embed(
                title="⚠️  Unauthorized Role Assignment Blocked",
                color=0xE67E22,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Target Member", value=f"{after.mention} (`{after.id}`)", inline=True)
            embed.add_field(name="Role", value=role.mention, inline=True)
            flag = "Protected Role" + (" + Administrator" if has_admin else "")
            embed.add_field(name="Flag", value=f"`{flag}`", inline=True)
            embed.add_field(
                name="Executor",
                value=f"{executor} (`{executor.id}`)" if executor else "*Unknown*",
                inline=False,
            )
            embed.add_field(name="Action", value="Role **removed** immediately.", inline=False)
            embed.set_footer(text="Security Bot • Danger Roles")

            await self._log(guild, embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  +danger tag — mass-mention allowlist management
    # ══════════════════════════════════════════════════════════════════════════

    @danger.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def danger_tag(self, ctx: commands.Context) -> None:
        """List all users allowed to use @everyone / @here."""
        rows = await self.db.danger_tag_list(ctx.guild.id)
        if not rows:
            return await ctx.send(
                embed=info_embed("Danger Tag", "No users are currently allowed to use @everyone / @here.")
            )

        lines = []
        for row in rows:
            member = ctx.guild.get_member(row["user_id"])
            label = member.mention if member else f"`{row['user_id']}`"
            lines.append(f"• {label}")

        embed = discord.Embed(
            title="🏷️  Danger Tag — Allowed Users",
            description="\n".join(lines),
            color=0xE67E22,
        )
        embed.set_footer(text=f"Security Bot • {len(rows)} user(s) allowed")
        await ctx.send(embed=embed)

    @danger_tag.command(name="add")
    @commands.guild_only()
    async def danger_tag_add(
        self,
        ctx: commands.Context,
        user: discord.Member,
    ) -> None:
        """Allow a user to use @everyone / @here mentions."""
        await self.db.danger_tag_add(ctx.guild.id, user.id)
        log.info("Danger tag added: guild=%d user=%d by %s", ctx.guild.id, user.id, ctx.author)
        await ctx.send(
            embed=success_embed(
                "Danger Tag Added",
                f"{user.mention} may now use `@everyone` and `@here` mentions.",
            )
        )

    @danger_tag.command(name="remove", aliases=["rm"])
    @commands.guild_only()
    async def danger_tag_remove(
        self,
        ctx: commands.Context,
        user: discord.Member,
    ) -> None:
        """Remove a user from the mass-mention allowlist."""
        removed = await self.db.danger_tag_remove(ctx.guild.id, user.id)
        if not removed:
            return await ctx.send(
                embed=warn_embed("Not Found", f"{user.mention} was not in the danger tag list."),
                delete_after=8,
            )
        log.info("Danger tag removed: guild=%d user=%d by %s", ctx.guild.id, user.id, ctx.author)
        await ctx.send(
            embed=success_embed("Danger Tag Removed", f"{user.mention} can no longer use mass mentions.")
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  on_message — 0-ms mass mention guard
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Only fire in guilds on human messages that actually contain a mass mention.
        if message.author.bot or not message.guild:
            return
        if not message.mention_everyone:
            return

        guild = message.guild
        author = message.author

        # Whitelisted users may always use mass mentions.
        if await is_whitelisted(self.db, guild, author):
            return

        # Explicitly permitted users (danger_tags) may also use them.
        if await self.db.danger_tag_check(guild.id, author.id):
            return

        # ── Unauthorized — delete the message immediately ──────────────────
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        log.warning(
            "Mass mention blocked: guild=%d author=%d channel=%d",
            guild.id, author.id, message.channel.id,
        )

        # Temporary warning in the same channel.
        try:
            await message.channel.send(
                embed=discord.Embed(
                    title="🚫  Mass Mention Blocked",
                    description=(
                        f"{author.mention} is not authorized to use `@everyone` or `@here`.\n"
                        "Your message has been removed."
                    ),
                    color=0xE74C3C,
                    timestamp=discord.utils.utcnow(),
                ).set_footer(text="Security Bot • Danger Tag"),
                delete_after=8,
            )
        except discord.Forbidden:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  Error handlers
    # ══════════════════════════════════════════════════════════════════════════

    @danger.error
    @danger_roles.error
    @danger_tag.error
    async def _danger_perm_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                embed=error_embed("Permission Denied", "Administrator permission required."),
                delete_after=8,
            )

    @danger_roles_add.error
    @danger_roles_remove.error
    async def _role_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.RoleNotFound):
            await ctx.send(
                embed=error_embed("Role Not Found", "Could not find that role. Mention it or use its ID."),
                delete_after=8,
            )

    @danger_tag_add.error
    @danger_tag_remove.error
    async def _tag_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(
                embed=error_embed("Member Not Found", "Could not find that member. Mention them or use their ID."),
                delete_after=8,
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  Log helper
    # ══════════════════════════════════════════════════════════════════════════

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass
                return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Danger(bot))
