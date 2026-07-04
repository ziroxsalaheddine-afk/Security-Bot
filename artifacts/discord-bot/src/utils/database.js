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

// ── Co-owner helpers ──────────────────────────────────────────────────────────

export function isCoOwner(userId) {
  const db = getDB();
  return db.coowners.includes(userId);
}

export function addCoOwner(userId) {
  const db = getDB();
  if (!db.coowners) db.coowners = [];
  if (!db.coowners.includes(userId)) {
    db.coowners.push(userId);
    saveDB();
  }
}

export function removeCoOwner(userId) {
  const db = getDB();
  if (!db.coowners) db.coowners = [];
  db.coowners = db.coowners.filter(id => id !== userId);
  saveDB();
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

// ── Quarantine helpers ────────────────────────────────────────────────────────
// Keyed by `${guildId}:${userId}` to prevent cross-guild collision.

function quarantineKey(guildId, userId) {
  return `${guildId}:${userId}`;
}

export function isQuarantined(userId, guildId = null) {
  const db = getDB();
  if (guildId) {
    return !!db.quarantine.users[quarantineKey(guildId, userId)];
  }
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

// ── Anti config helpers ───────────────────────────────────────────────────────

export function getAntiConfig(type) {
  const db = getDB();
  if (!db.anti) db.anti = {};
  return db.anti[type] ?? { enabled: false, action: 'delete' };
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
  const db = getDB();
  return db.backups?.[id] ?? null;
}

export function listBackups() {
  const db = getDB();
  return db.backups ?? {};
}

export function deleteBackup(id) {
  const db = getDB();
  if (!db.backups) return;
  delete db.backups[id];
  saveDB();
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
