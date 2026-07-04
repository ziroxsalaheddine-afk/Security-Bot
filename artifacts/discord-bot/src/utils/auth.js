import { getDB } from './database.js';

/**
 * Returns true if the userId is a bot owner OR co-owner.
 * Co-owners have access to all commands.
 */
export function isAuthorized(userId) {
  const db = getDB();
  return db.owners.includes(userId) || db.coowners.includes(userId);
}

/**
 * Returns true ONLY for bot owners (not co-owners).
 * Used for privileged actions like adding/removing co-owners.
 */
export function isOwnerOnly(userId) {
  const db = getDB();
  return db.owners.includes(userId);
}
