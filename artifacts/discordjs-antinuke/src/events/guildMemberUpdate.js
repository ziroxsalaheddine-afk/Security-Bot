"use strict";

/**
 * guildMemberUpdate — fires whenever a member's profile changes (roles, nickname, etc.).
 *
 * Compares the old and new role sets; if they differ, the DB entry is
 * updated instantly so that roleDelete always has a fresh member list.
 */

module.exports = {
  name: "guildMemberUpdate",
  once: false,

  execute(client, oldMember, newMember) {
    const oldRoles = oldMember.roles.cache;
    const newRoles = newMember.roles.cache;

    // Fast equality check — sizes differ OR some ID is missing from the other set
    const changed =
      oldRoles.size !== newRoles.size ||
      oldRoles.some((_, id) => !newRoles.has(id));

    if (!changed) return;

    const roleIds = newRoles
      .filter((r) => r.id !== newMember.guild.id && !r.managed)
      .map((r) => r.id);

    client.db.syncMember(newMember.guild.id, newMember.id, roleIds);
  },
};
