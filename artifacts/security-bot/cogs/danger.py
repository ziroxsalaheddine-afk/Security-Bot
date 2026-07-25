"""
Danger Cog — Unauthorized role assignment protection and mass-mention blocking.
No emojis in any user-facing text.

Danger Roles (+danger roles ...):
  - Marks specific roles as protected.
  - Accepts multiple roles in a single add/remove command.
  - on_member_update: if a protected role (or any Administrator role) is granted
    by a non-whitelisted executor, it is immediately stripped from the recipient
    and a security alert is posted.

Danger Tags (+danger tag ...):
  - Per-guild allowlist of users permitted to use @everyone / @here.
  - on_message: if a mass mention appears from an unauthorized user, the message
    is deleted instantly and a 5-second temporary warning is posted.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from database import Database
from utils import (
    error_embed,
    get_audit_executor,
    info_embed,
    is_whitelisted,
    log_embed,
    send_log,
    success_embed,
    warn_embed,
    FOOTER,
)

log = logging.getLogger("trossard.danger")


class Danger(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    # ══════════════════════════════════════════════════════════════════════════
    #  +danger
    # ══════════════════════════════════════════════════════════════════════════

    @commands.group(name="danger", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def danger(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=info_embed(
                "Danger Module",
                "**Subcommands**\n"
                "`+danger roles` — manage protected roles\n"
                "`+danger tag` — manage mass-mention allowlist\n\n"
                "Use `+help` for the full command reference.",
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  +danger roles
    # ══════════════════════════════════════════════════════════════════════════

    @danger.group(name="roles", invoke_without_command=True)
    @commands.guild_only()
    async def danger_roles(self, ctx: commands.Context) -> None:
        rows = await self.db.danger_role_list(ctx.guild.id)
        if not rows:
            return await ctx.send(
                embed=info_embed(
                    "Danger Roles",
                    "No roles are currently marked as protected.",
                )
            )

        lines = []
        for row in rows:
            role = ctx.guild.get_role(row["role_id"])
            label = role.mention if role else f"`{row['role_id']}`"
            admin_note = " (Administrator)" if role and role.permissions.administrator else ""
            lines.append(f"- {label}{admin_note}")

        embed = discord.Embed(
            title="Protected Roles",
            description="\n".join(lines),
            color=0xE67E22,
        )
        embed.set_footer(text=f"{FOOTER} — {len(rows)} protected role(s)")
        await ctx.send(embed=embed)

    @danger_roles.command(name="add")
    @commands.guild_only()
    async def danger_roles_add(self, ctx: commands.Context, *roles: discord.Role) -> None:
        if not roles:
            return await ctx.send(
                embed=error_embed(
                    "No Roles Specified",
                    "Mention one or more roles to protect.\n"
                    "Usage: `+danger roles add @role [@role ...]`",
                ),
                delete_after=8,
            )

        for role in roles:
            await self.db.danger_role_add(ctx.guild.id, role.id)
            log.info(
                "Danger role added: guild=%d role=%d (%s) by %s",
                ctx.guild.id, role.id, role.name, ctx.author,
            )

        if len(roles) == 1:
            body = (
                f"{roles[0].mention} is now protected.\n"
                "Any unauthorized member granted this role will have it removed immediately."
            )
        else:
            mentions = "\n".join(f"- {r.mention}" for r in roles)
            body = (
                f"**{len(roles)} roles** are now protected:\n{mentions}\n\n"
                "Any unauthorized member granted these roles will have them removed immediately."
            )

        await ctx.send(embed=success_embed("Danger Role(s) Added", body))

    @danger_roles.command(name="remove", aliases=["rm"])
    @commands.guild_only()
    async def danger_roles_remove(self, ctx: commands.Context, *roles: discord.Role) -> None:
        if not roles:
            return await ctx.send(
                embed=error_embed(
                    "No Roles Specified",
                    "Mention one or more roles to unprotect.\n"
                    "Usage: `+danger roles remove @role [@role ...]`",
                ),
                delete_after=8,
            )

        removed = []
        not_found = []
        for role in roles:
            ok = await self.db.danger_role_remove(ctx.guild.id, role.id)
            if ok:
                removed.append(role)
                log.info(
                    "Danger role removed: guild=%d role=%d (%s) by %s",
                    ctx.guild.id, role.id, role.name, ctx.author,
                )
            else:
                not_found.append(role)

        parts: list[str] = []
        if removed:
            mentions = "\n".join(f"- {r.mention}" for r in removed)
            parts.append(f"**Removed ({len(removed)}):**\n{mentions}")
        if not_found:
            mentions = "\n".join(f"- {r.mention}" for r in not_found)
            parts.append(f"**Not in protected list ({len(not_found)}):**\n{mentions}")

        if not removed:
            await ctx.send(
                embed=warn_embed("Not Found", "\n\n".join(parts)),
                delete_after=8,
            )
        else:
            await ctx.send(embed=success_embed("Danger Role(s) Removed", "\n\n".join(parts)))

    # ── on_member_update — unauthorized danger / admin role guard ─────────────

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
                continue

            executor = await get_audit_executor(
                guild, discord.AuditLogAction.member_role_update, after.id, limit=5
            )

            if executor:
                if executor.id in (guild.owner_id, self.bot.user.id):
                    continue
                exec_member = guild.get_member(executor.id)
                if exec_member and await is_whitelisted(self.db, guild, exec_member):
                    continue

            try:
                await after.remove_roles(
                    role,
                    reason="[Trossard] Unauthorized danger/admin role assignment reversed",
                )
                log.warning(
                    "Danger role stripped: guild=%d member=%d role=%r executor=%s",
                    guild.id, after.id, role.name, executor,
                )
            except discord.Forbidden:
                log.error(
                    "Forbidden removing danger role %r from %d in guild %d",
                    role.name, after.id, guild.id,
                )
                continue
            except Exception as exc:
                log.error("Failed to strip danger role: %s", exc)
                continue

            embed = log_embed("Unauthorized Role Assignment Blocked")
            embed.color = 0xE67E22
            embed.timestamp = discord.utils.utcnow()
            embed.add_field(
                name="Target Member",
                value=f"{after.mention} (`{after.id}`)",
                inline=True,
            )
            embed.add_field(name="Role", value=role.mention, inline=True)
            flag = "Protected Role" + (" + Administrator" if has_admin else "")
            embed.add_field(name="Flag", value=f"`{flag}`", inline=True)
            embed.add_field(
                name="Executor",
                value=f"{executor} (`{executor.id}`)" if executor else "Unknown",
                inline=False,
            )
            embed.add_field(name="Action", value="Role removed immediately.", inline=False)
            await send_log(guild, embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  +danger tag
    # ══════════════════════════════════════════════════════════════════════════

    @danger.group(name="tag", invoke_without_command=True)
    @commands.guild_only()
    async def danger_tag(self, ctx: commands.Context) -> None:
        rows = await self.db.danger_tag_list(ctx.guild.id)
        if not rows:
            return await ctx.send(
                embed=info_embed(
                    "Danger Tag",
                    "No users are currently allowed to use @everyone / @here.",
                )
            )

        lines = []
        for row in rows:
            member = ctx.guild.get_member(row["user_id"])
            label = member.mention if member else f"`{row['user_id']}`"
            lines.append(f"- {label}")

        embed = discord.Embed(
            title="Mass Mention Allowlist",
            description="\n".join(lines),
            color=0xE67E22,
        )
        embed.set_footer(text=f"{FOOTER} — {len(rows)} user(s) allowed")
        await ctx.send(embed=embed)

    @danger_tag.command(name="add")
    @commands.guild_only()
    async def danger_tag_add(self, ctx: commands.Context, user: discord.Member) -> None:
        await self.db.danger_tag_add(ctx.guild.id, user.id)
        log.info(
            "Danger tag added: guild=%d user=%d by %s",
            ctx.guild.id, user.id, ctx.author,
        )
        await ctx.send(
            embed=success_embed(
                "Danger Tag Added",
                f"{user.mention} may now use `@everyone` and `@here` mentions.",
            )
        )

    @danger_tag.command(name="remove", aliases=["rm"])
    @commands.guild_only()
    async def danger_tag_remove(self, ctx: commands.Context, user: discord.Member) -> None:
        removed = await self.db.danger_tag_remove(ctx.guild.id, user.id)
        if not removed:
            return await ctx.send(
                embed=warn_embed("Not Found", f"{user.mention} was not in the danger tag list."),
                delete_after=8,
            )
        log.info(
            "Danger tag removed: guild=%d user=%d by %s",
            ctx.guild.id, user.id, ctx.author,
        )
        await ctx.send(
            embed=success_embed(
                "Danger Tag Removed",
                f"{user.mention} can no longer use mass mentions.",
            )
        )

    # ── on_message — 0ms mass mention guard ──────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if not message.mention_everyone:
            return

        guild = message.guild
        author = message.author

        if await is_whitelisted(self.db, guild, author):
            return
        if await self.db.danger_tag_check(guild.id, author.id):
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        log.warning(
            "Mass mention blocked: guild=%d author=%d channel=%d",
            guild.id, author.id, message.channel.id,
        )

        try:
            warning = discord.Embed(
                title="Mass Mention Blocked",
                description=(
                    f"{author.mention} is not authorized to use `@everyone` or `@here`.\n"
                    "Your message has been removed."
                ),
                color=0xE74C3C,
                timestamp=discord.utils.utcnow(),
            )
            warning.set_footer(text=FOOTER)
            await message.channel.send(embed=warning, delete_after=5)
        except discord.Forbidden:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  Error handlers
    # ══════════════════════════════════════════════════════════════════════════

    @danger.error
    @danger_roles.error
    @danger_tag.error
    async def _perm_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                embed=error_embed("Permission Denied", "Administrator permission is required."),
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Danger(bot))
