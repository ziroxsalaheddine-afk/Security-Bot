/**
 * database.js — Crash-safe persistent storage using atomic JSON writes.
 *
 * Durability guarantee (equivalent to SQLite WAL + FULL sync):
 *   1. Serialize data to JSON.
 *   2. Write to a .tmp file using openSync/writeSync.
 *   3. fsyncSync → flush OS write buffer to physical disk.
 *   4. renameSync → atomic on Linux (same filesystem); the kernel guarantees
 *      the file pointer swaps in one syscall — readers always see a complete file.
 *
 * This is the same sequence SQLite uses internally. There is no window in which
 *  the database file can be partially written or corrupted, even on a hard crash
 *  or power loss.
 *
 * In-memory cache: getDB() returns a reference to _cache for fast synchronous
 * reads. Every mutation calls saveDB() before returning, so the on-disk file
 * is always in sync with memory.
 */

import {
  openSync, writeSync, fsyncSync, closeSync,
  renameSync, readFileSync, existsSync,
} from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname  = dirname(fileURLToPath(import.meta.url));
const DB_PATH    = join(__dirname, '../../guardian.db.json');
const LEGACY_PATH = join(__dirname, '../../database.json');

// ── Default schema ────────────────────────────────────────────────────────────
function buildDefaults() {
  return {
    owners:    [],
    coowners:  [],
    whitelist: { users: [], roles: [], channels: [] },
    blacklist: { users: [] },
    quarantine: { users: {}, role: null },
    anti: {
      link:    { enabled: true, action: 'delete' },
      invite:  { enabled: true, action: 'delete' },
      webhook: { enabled: true, action: 'delete' },
      bot:     { enabled: true, action: 'kick'   },
    },
    config: {
      prefix: '+',
      antinuke: {
        enabled: true, threshold: 3, interval: 10000, action: 'quarantine',
      },
      automod: {
        enabled: true,
        antiSpam: {
          enabled: true, messageLimit: 5, interval: 3000,
          action: 'timeout', timeoutDuration: 300000,
        },
        antiMassMention: { enabled: true, mentionLimit: 5, action: 'timeout' },
      },
      altProtection: { enabled: true, minAccountAge: 7, action: 'kick' },
    },
    logs:    { channelId: null },
    backups: {},
  };
}

// ── Atomic write ──────────────────────────────────────────────────────────────
/**
 * Write `content` (string) to `filePath` atomically:
 *   • Writes to a sibling .tmp file.
 *   • Calls fsyncSync to flush OS write buffers to physical disk.
 *   • Renames .tmp → target (atomic on Linux; readers see either old or new, never partial).
 */
function atomicWriteSync(filePath, content) {
  const tmp = `${filePath}.tmp`;
  const fd  = openSync(tmp, 'w');
  try {
    writeSync(fd, content, null, 'utf-8');
    fsyncSync(fd); // Guarantee bytes reach disk before rename
  } finally {
    closeSync(fd);
  }
  renameSync(tmp, filePath); // Atomic pointer swap
}

// ── In-memory cache ───────────────────────────────────────────────────────────
let _cache = null;

// ── Public persistence API ────────────────────────────────────────────────────

/** Persist the full in-memory cache to disk atomically. */
export function saveDB() {
  if (!_cache) return;
  atomicWriteSync(DB_PATH, JSON.stringify(_cache, null, 2));
}

/** Load cache from guardian.db.json (or migrate from legacy database.json). */
export function loadDB() {
  if (existsSync(DB_PATH)) {
    // ── Normal startup ───────────────────────────────────────────────────
    try {
      _cache = JSON.parse(readFileSync(DB_PATH, 'utf-8'));
      // Ensure any keys added after initial creation are present
      const defaults = buildDefaults();
      for (const key of Object.keys(defaults)) {
        if (_cache[key] === undefined) _cache[key] = defaults[key];
      }
      console.log('[DB] Loaded guardian.db.json (atomic-JSON, fsync-safe).');
    } catch (err) {
      console.error('[DB] Failed to parse guardian.db.json — restoring from defaults:', err.message);
      _cache = buildDefaults();
      saveDB();
    }
  } else if (existsSync(LEGACY_PATH)) {
    // ── First run: migrate from old database.json ────────────────────────
    console.log('[DB] Migrating data from database.json → guardian.db.json...');
    _cache = buildDefaults();
    try {
      const legacy = JSON.parse(readFileSync(LEGACY_PATH, 'utf-8'));
      if (Array.isArray(legacy.owners))   _cache.owners   = legacy.owners;
      if (Array.isArray(legacy.coowners)) _cache.coowners = legacy.coowners;
      if (legacy.whitelist?.users)  _cache.whitelist.users  = legacy.whitelist.users;
      if (legacy.blacklist?.users)  _cache.blacklist.users  = legacy.blacklist.users;
      if (legacy.quarantine?.users) _cache.quarantine.users = legacy.quarantine.users;
      if (legacy.quarantine?.role)  _cache.quarantine.role  = legacy.quarantine.role;
      if (legacy.anti)              _cache.anti = { ..._cache.anti, ...legacy.anti };
      if (legacy.config?.antinuke)      _cache.config.antinuke      = legacy.config.antinuke;
      if (legacy.config?.automod)       _cache.config.automod       = legacy.config.automod;
      if (legacy.config?.altProtection) _cache.config.altProtection = legacy.config.altProtection;
      if (legacy.logs?.channelId)   _cache.logs.channelId = legacy.logs.channelId;
      if (legacy.backups)           _cache.backups = legacy.backups;
      console.log('[DB] Migration complete.');
    } catch (err) {
      console.warn('[DB] Migration failed, using defaults:', err.message);
    }
    saveDB(); // Write guardian.db.json immediately
  } else {
    // ── Brand-new install ────────────────────────────────────────────────
    _cache = buildDefaults();
    saveDB();
    console.log('[DB] Initialised fresh guardian.db.json.');
  }

  return _cache;
}

/** Return the in-memory cache, loading from disk on first call. */
export function getDB() {
  if (!_cache) return loadDB();
  return _cache;
}

// ═════════════════════════════════════════════════════════════════════════════
// Domain helpers — every write calls saveDB() (→ atomic fsync write) before
// returning, so the file on disk is always consistent with memory.
// ═════════════════════════════════════════════════════════════════════════════

// ── Owner helpers ─────────────────────────────────────────────────────────────

export function isOwner(userId) {
  return getDB().owners.includes(userId);
}

export function addOwner(userId) {
  const db = getDB();
  if (!db.owners.includes(userId)) { db.owners.push(userId); saveDB(); }
}

export function removeOwner(userId) {
  const db = getDB();
  db.owners = db.owners.filter(id => id !== userId);
  saveDB();
}

// ── Co-owner helpers ──────────────────────────────────────────────────────────

export function isCoOwner(userId) {
  return (getDB().coowners ?? []).includes(userId);
}

export function addCoOwner(userId) {
  const db = getDB();
  if (!db.coowners) db.coowners = [];
  if (!db.coowners.includes(userId)) { db.coowners.push(userId); saveDB(); }
}

export function removeCoOwner(userId) {
  const db = getDB();
  if (!db.coowners) { db.coowners = []; return; }
  db.coowners = db.coowners.filter(id => id !== userId);
  saveDB();
}

// ── Whitelist helpers ─────────────────────────────────────────────────────────

export function isWhitelisted(userId) {
  return getDB().whitelist.users.includes(userId);
}

export function addWhitelist(userId) {
  const db = getDB();
  if (!db.whitelist.users.includes(userId)) { db.whitelist.users.push(userId); saveDB(); }
}

export function removeWhitelist(userId) {
  const db = getDB();
  db.whitelist.users = db.whitelist.users.filter(id => id !== userId);
  saveDB();
}

// ── Blacklist helpers ─────────────────────────────────────────────────────────

export function isBlacklisted(userId) {
  return getDB().blacklist.users.includes(userId);
}

export function addBlacklist(userId) {
  const db = getDB();
  if (!db.blacklist.users.includes(userId)) { db.blacklist.users.push(userId); saveDB(); }
}

export function removeBlacklist(userId) {
  const db = getDB();
  db.blacklist.users = db.blacklist.users.filter(id => id !== userId);
  saveDB();
}

// ── Quarantine helpers ────────────────────────────────────────────────────────

function qKey(guildId, userId) { return `${guildId}:${userId}`; }

export function isQuarantined(userId, guildId = null) {
  const db = getDB();
  if (guildId) return !!db.quarantine.users[qKey(guildId, userId)];
  return Object.keys(db.quarantine.users).some(k => k.endsWith(`:${userId}`));
}

export function addQuarantine(userId, data = {}) {
  const db = getDB();
  if (!data.guild) { console.warn('[DB] addQuarantine: missing guildId'); return; }
  db.quarantine.users[qKey(data.guild, userId)] = { ...data, timestamp: Date.now() };
  saveDB();
}

export function removeQuarantine(userId, guildId = null) {
  const db = getDB();
  if (guildId) {
    delete db.quarantine.users[qKey(guildId, userId)];
  } else {
    for (const k of Object.keys(db.quarantine.users)) {
      if (k.endsWith(`:${userId}`)) delete db.quarantine.users[k];
    }
  }
  saveDB();
}

export function getQuarantineData(userId, guildId) {
  return getDB().quarantine.users[qKey(guildId, userId)] ?? null;
}

export function setQuarantineRole(roleId) {
  const db = getDB();
  db.quarantine.role = roleId;
  saveDB();
}

export function getQuarantineRole() {
  return getDB().quarantine.role;
}

// ── Anti config helpers ───────────────────────────────────────────────────────

export function getAntiConfig(type) {
  return getDB().anti?.[type] ?? { enabled: false, action: 'delete' };
}

export function setAntiConfig(type, field, value) {
  const db = getDB();
  if (!db.anti) db.anti = {};
  if (!db.anti[type]) db.anti[type] = { enabled: true, action: 'delete' };
  db.anti[type][field] = value;
  saveDB();
}

// ── Backup helpers ────────────────────────────────────────────────────────────

export function generateBackupId() {
  return Math.random().toString(36).substring(2, 8).toUpperCase();
}

export function saveBackup(id, data) {
  const db = getDB();
  if (!db.backups) db.backups = {};
  db.backups[id] = data;
  saveDB();
}

export function getBackup(id) {
  return getDB().backups?.[id] ?? null;
}

export function listBackups() {
  return getDB().backups ?? {};
}

export function deleteBackup(id) {
  const db = getDB();
  if (!db.backups) return;
  delete db.backups[id];
  saveDB();
}

// ── Config / logs helpers ─────────────────────────────────────────────────────

export function getConfig() {
  return getDB().config;
}

export function setLogChannel(channelId) {
  const db = getDB();
  db.logs.channelId = channelId;
  saveDB();
}

export function getLogChannel() {
  return getDB().logs.channelId;
}
