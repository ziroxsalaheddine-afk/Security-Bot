import { readFileSync, writeFileSync, existsSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = join(__dirname, '../../database.json');

let _cache = null;

export function loadDB() {
  if (!existsSync(DB_PATH)) {
    throw new Error(`database.json not found at ${DB_PATH}`);
  }
  const raw = readFileSync(DB_PATH, 'utf-8');
  _cache = JSON.parse(raw);
  return _cache;
}

export function getDB() {
  if (!_cache) return loadDB();
  return _cache;
}

export function saveDB() {
  if (!_cache) return;
  writeFileSync(DB_PATH, JSON.stringify(_cache, null, 2), 'utf-8');
}

// ── Whitelist helpers ─────────────────────────────────────────────────────────

export function isWhitelisted(userId) {
  const db = getDB();
  return db.whitelist.users.includes(userId);
}

export function addWhitelist(userId) {
  const db = getDB();
  if (!db.whitelist.users.includes(userId)) {
    db.whitelist.users.push(userId);
    saveDB();
  }
}

export function removeWhitelist(userId) {
  const db = getDB();
  db.whitelist.users = db.whitelist.users.filter(id => id !== userId);
  saveDB();
}

// ── Blacklist helpers ─────────────────────────────────────────────────────────

export function isBlacklisted(userId) {
  const db = getDB();
  return db.blacklist.users.includes(userId);
}

export function addBlacklist(userId) {
  const db = getDB();
  if (!db.blacklist.users.includes(userId)) {
    db.blacklist.users.push(userId);
    saveDB();
  }
}

export function removeBlacklist(userId) {
  const db = getDB();
  db.blacklist.users = db.blacklist.users.filter(id => id !== userId);
  saveDB();
}

// ── Owner helpers ─────────────────────────────────────────────────────────────

export function isOwner(userId) {
  const db = getDB();
  return db.owners.includes(userId);
}

export function addOwner(userId) {
  const db = getDB();
  if (!db.owners.includes(userId)) {
    db.owners.push(userId);
    saveDB();
  }
}

export function removeOwner(userId) {
  const db = getDB();
  db.owners = db.owners.filter(id => id !== userId);
  saveDB();
}

// ── Quarantine helpers ────────────────────────────────────────────────────────
// Quarantine is keyed by `${guildId}:${userId}` to prevent cross-guild collision.

function quarantineKey(guildId, userId) {
  return `${guildId}:${userId}`;
}

export function isQuarantined(userId, guildId = null) {
  const db = getDB();
  if (guildId) {
    return !!db.quarantine.users[quarantineKey(guildId, userId)];
  }
  // Legacy global check (for commands that don't pass guildId)
  return Object.keys(db.quarantine.users).some(k => k.endsWith(`:${userId}`));
}

export function addQuarantine(userId, data = {}) {
  const db = getDB();
  const guildId = data.guild;
  if (!guildId) {
    console.warn('[DB] addQuarantine called without guildId — skipping persist.');
    return;
  }
  const key = quarantineKey(guildId, userId);
  db.quarantine.users[key] = { ...data, timestamp: Date.now() };
  saveDB();
}

export function removeQuarantine(userId, guildId = null) {
  const db = getDB();
  if (guildId) {
    delete db.quarantine.users[quarantineKey(guildId, userId)];
  } else {
    // Remove all quarantine entries for this user across guilds
    for (const key of Object.keys(db.quarantine.users)) {
      if (key.endsWith(`:${userId}`)) delete db.quarantine.users[key];
    }
  }
  saveDB();
}

export function getQuarantineData(userId, guildId) {
  const db = getDB();
  return db.quarantine.users[quarantineKey(guildId, userId)] ?? null;
}

export function setQuarantineRole(roleId) {
  const db = getDB();
  db.quarantine.role = roleId;
  saveDB();
}

export function getQuarantineRole() {
  const db = getDB();
  return db.quarantine.role;
}

// ── Config helpers ────────────────────────────────────────────────────────────

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
