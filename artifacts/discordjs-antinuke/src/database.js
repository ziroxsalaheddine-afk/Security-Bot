"use strict";

/**
 * Role-cache database layer — better-sqlite3 (synchronous SQLite).
 *
 * Schema
 * ──────
 *   role_cache (guild_id TEXT, user_id TEXT, role_id TEXT)
 *     Tracks every non-managed, non-@everyone role each member holds.
 *     Primary key prevents duplicates; WAL mode gives safe concurrent reads.
 */

const Database = require("better-sqlite3");
const path = require("path");

const DB_PATH = path.join(__dirname, "..", "antinuke.db");
const db = new Database(DB_PATH);

// Performance / safety pragmas
db.pragma("journal_mode = WAL");
db.pragma("synchronous = NORMAL");
db.pragma("foreign_keys = ON");

db.exec(`
  CREATE TABLE IF NOT EXISTS role_cache (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    role_id  TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id, role_id)
  );
`);

// ── Prepared statements (compiled once, reused many times) ───────────────────
const stmts = {
  deleteMember: db.prepare(
    "DELETE FROM role_cache WHERE guild_id = ? AND user_id = ?"
  ),
  insertRole: db.prepare(
    "INSERT OR IGNORE INTO role_cache VALUES (?, ?, ?)"
  ),
  getMembersWithRole: db.prepare(
    "SELECT user_id FROM role_cache WHERE guild_id = ? AND role_id = ?"
  ),
  updateRoleId: db.prepare(
    "UPDATE role_cache SET role_id = ? WHERE guild_id = ? AND role_id = ?"
  ),
  deleteRole: db.prepare(
    "DELETE FROM role_cache WHERE guild_id = ? AND role_id = ?"
  ),
  clearGuild: db.prepare(
    "DELETE FROM role_cache WHERE guild_id = ?"
  ),
};

// Wrapped transaction: delete old rows then bulk-insert new ones atomically
const syncMemberTx = db.transaction((guildId, userId, roleIds) => {
  stmts.deleteMember.run(guildId, userId);
  for (const roleId of roleIds) {
    stmts.insertRole.run(guildId, userId, roleId);
  }
});

module.exports = {
  /**
   * Atomically replace a member's cached role list.
   * @param {string}   guildId
   * @param {string}   userId
   * @param {string[]} roleIds  Non-@everyone, non-managed role IDs the member currently holds
   */
  syncMember(guildId, userId, roleIds) {
    syncMemberTx(guildId, userId, roleIds);
  },

  /**
   * Return all user IDs that held a specific role at last sync.
   * @param  {string}   guildId
   * @param  {string}   roleId
   * @returns {string[]}
   */
  getMembersWithRole(guildId, roleId) {
    return stmts.getMembersWithRole
      .all(guildId, roleId)
      .map((row) => row.user_id);
  },

  /**
   * Swap every occurrence of oldRoleId with newRoleId in the cache.
   * Call this after recreating a deleted role so future deletes still work.
   * @param {string} guildId
   * @param {string} oldRoleId
   * @param {string} newRoleId
   */
  updateRoleId(guildId, oldRoleId, newRoleId) {
    stmts.updateRoleId.run(newRoleId, guildId, oldRoleId);
  },

  /**
   * Remove all cached entries for a role (call after updateRoleId if needed).
   */
  deleteRole(guildId, roleId) {
    stmts.deleteRole.run(guildId, roleId);
  },

  /**
   * Wipe a guild's entire cache (e.g. on guild leave).
   */
  clearGuild(guildId) {
    stmts.clearGuild.run(guildId);
  },
};
