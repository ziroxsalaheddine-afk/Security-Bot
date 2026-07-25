"use strict";

/**
 * roleDelete — fires when any role in a guild is deleted.
 *
 * Workflow
 * ────────
 * 1. Pull the member ID list from the DB before touching anything.
 * 2. Check the audit log — skip if the bot itself deleted the role.
 * 3. Recreate the role with identical properties (name, color, permissions,
 *    hoist, mentionable, position).
 * 4. Re-assign the new role to every original member using batched concurrent
 *    requests + inter-batch delays to stay inside Discord rate limits.
 * 5. Update the DB so old_role_id → new_role_id for future events.
 */

const { AuditLogEvent } = require("discord.js");

/** How many role.add() calls fire concurrently inside one batch. */
const BATCH_SIZE = 5;

/**
 * Milliseconds to wait between batches.
 * Discord's global rate limit is ~50 requests/second.
 * With BATCH_SIZE=5 we send 5 requests then wait 250 ms → ~20 req/s — safe.
 */
const BATCH_DELAY_MS = 250;

/** Small pause before reading the audit log so Discord has time to write it. */
const AUDIT_LOG_DELAY_MS = 750;

const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

module.exports = {
  name: "roleDelete",
  once: false,

  async execute(client, role) {
    const guild = role.guild;

    // ── Step 1: Capture member list from DB immediately ───────────────────────
    //   Discord purges role-member associations on deletion, so we must read
    //   our own cache before anything else.
    const memberIds = client.db.getMembersWithRole(guild.id, role.id);

    console.log(
      `[AntiNuke] roleDelete: "${role.name}" (ID: ${role.id}) — ` +
        `${memberIds.length} cached member(s).`
    );

    if (memberIds.length === 0) {
      // Nothing to restore — bail early without touching the DB.
      return;
    }

    // ── Step 2: Audit log check — skip if the bot deleted the role ────────────
    await sleep(AUDIT_LOG_DELAY_MS);
    try {
      const logs = await guild.fetchAuditLogs({
        limit: 5,
        type: AuditLogEvent.RoleDelete,
      });
      const entry = logs.entries.find((e) => e.target?.id === role.id);

      if (entry?.executor?.id === client.user.id) {
        console.log(
          `[AntiNuke] Role "${role.name}" was deleted by the bot itself — skipping restore.`
        );
        // Clean up stale DB record and exit.
        client.db.deleteRole(guild.id, role.id);
        return;
      }

      if (entry?.executor) {
        console.log(
          `[AntiNuke] Executor: ${entry.executor.tag} (${entry.executor.id})`
        );
      }
    } catch (err) {
      // Missing MANAGE_GUILD / VIEW_AUDIT_LOG permission — proceed anyway.
      console.warn(`[AntiNuke] Could not read audit log: ${err.message}`);
    }

    // ── Step 3: Recreate the role ─────────────────────────────────────────────
    let newRole;
    try {
      newRole = await guild.roles.create({
        name:        role.name,
        color:       role.color,
        hoist:       role.hoist,
        mentionable: role.mentionable,
        permissions: role.permissions,
        reason:      "[AntiNuke] Auto-restored deleted role",
      });

      // Restore original sort position (best-effort; may fail if hierarchy conflict)
      try {
        await newRole.setPosition(role.rawPosition, { reason: "[AntiNuke] Restoring position" });
      } catch {
        // Non-fatal — position mismatch is cosmetic.
      }

      console.log(
        `[AntiNuke] Recreated role "${newRole.name}" — new ID: ${newRole.id}`
      );
    } catch (err) {
      console.error(
        `[AntiNuke] Could not recreate role "${role.name}": ${err.message}`
      );
      // Remove stale DB record so it doesn't linger.
      client.db.deleteRole(guild.id, role.id);
      return;
    }

    // ── Step 4: Re-assign to original members (batched + rate-limited) ────────
    let reassigned = 0;
    let skipped = 0;
    let failed = 0;

    for (let i = 0; i < memberIds.length; i += BATCH_SIZE) {
      const batch = memberIds.slice(i, i + BATCH_SIZE);

      await Promise.all(
        batch.map(async (userId) => {
          try {
            // guild.members.fetch() returns a cached member if available,
            // otherwise fires an API call — handles members who left and rejoin.
            const member = await guild.members.fetch(userId).catch(() => null);
            if (!member) {
              skipped++;
              return;
            }

            await member.roles.add(newRole, "[AntiNuke] Role restore");
            reassigned++;
          } catch (err) {
            console.warn(
              `[AntiNuke] Could not assign role to member ${userId}: ${err.message}`
            );
            failed++;
          }
        })
      );

      // Pause between batches — keeps us well under Discord's rate limit ceiling.
      if (i + BATCH_SIZE < memberIds.length) {
        await sleep(BATCH_DELAY_MS);
      }
    }

    console.log(
      `[AntiNuke] Restore complete for "${newRole.name}": ` +
        `${reassigned} assigned, ${skipped} not in server, ${failed} failed ` +
        `(total ${memberIds.length}).`
    );

    // ── Step 5: Update DB — old role ID → new role ID ─────────────────────────
    //   Future roleDelete events for this role will now find the correct new ID.
    client.db.updateRoleId(guild.id, role.id, newRole.id);
    console.log(
      `[AntiNuke] DB updated: role ${role.id} -> ${newRole.id}`
    );
  },
};
