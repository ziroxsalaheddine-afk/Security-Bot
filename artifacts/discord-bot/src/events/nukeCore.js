import { getDB, isWhitelisted, addQuarantine, getQuarantineRole } from '../utils/database.js';
import { nukeAlertEmbed } from '../utils/embeds.js';

const NUKE_THRESHOLD = 3;
const NUKE_INTERVAL = 10_000; // 10 seconds

async function sendLog(guild, embed) {
  const db = getDB();
  const channelId = db.logs?.channelId;
  if (!channelId) return;
  const ch = guild.channels.cache.get(channelId);
  if (ch?.isTextBased()) ch.send({ embeds: [embed] }).catch(() => {});
}

/**
 * Fetch the executor of the most recent audit log entry of the given type,
 * but only if it targets a specific resource ID and is within the time window.
 * Returns null if attribution cannot be confidently established.
 */
async function fetchAuditExecutor(guild, auditLogEvent, targetId = null) {
  try {
    // Brief delay for audit log propagation
    await new Promise(r => setTimeout(r, 1500));
    const logs = await guild.fetchAuditLogs({ limit: 5, type: auditLogEvent });
    const now = Date.now();

    for (const entry of logs.entries.values()) {
      // Must be within 15 seconds
      if (now - entry.createdTimestamp > 15_000) break;
      // If we have a target ID, only match that specific entry
      if (targetId && entry.targetId && entry.targetId !== targetId) continue;
      return entry.executor;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Strip all roles from a member and apply the quarantine role.
 * Returns true only if ALL mutations succeeded.
 */
async function quarantineUser(guild, userId, reason) {
  const member = await guild.members.fetch(userId).catch(() => null);
  if (!member) return false;

  const savedRoles = member.roles.cache
    .filter(r => r.id !== guild.id)
    .map(r => r.id);

  // 1. Strip roles
  try {
    await member.roles.set([], reason);
  } catch (err) {
    console.error('[NUKE] Could not strip roles:', err.message);
    return false; // Abort — do not save quarantine state if strip failed
  }

  // 2. Apply quarantine role (best-effort — don't abort if this fails)
  const quarantineRoleId = getQuarantineRole();
  if (quarantineRoleId) {
    await member.roles.add(quarantineRoleId, 'Anti-Nuke: Quarantine role applied').catch(() => {});
  }

  // 3. Only persist quarantine state after successful enforcement
  addQuarantine(userId, {
    reason,
    guild: guild.id,
    savedRoles,
    quarantined: true,
  });

  console.log(`[ANTI-NUKE] Quarantined ${member.user.tag} in ${guild.name} — ${reason}`);
  return true;
}

export async function handleNukeEvent(client, guild, auditLogEvent, eventName, targetId = null) {
  if (!guild) return;

  const db = getDB();
  if (!db.config.antinuke?.enabled) return;

  const cfg = db.config.antinuke;
  const threshold = cfg.threshold ?? NUKE_THRESHOLD;
  const interval = cfg.interval ?? NUKE_INTERVAL;

  const executor = await fetchAuditExecutor(guild, auditLogEvent, targetId);
  if (!executor) return;
  if (executor.bot) return;
  if (isWhitelisted(executor.id)) return;
  if (db.owners.includes(executor.id)) return;

  // Guild-scoped tracker key prevents cross-guild false positives
  const trackerKey = `${guild.id}:${executor.id}`;
  const now = Date.now();
  let tracker = client.nukeTracker.get(trackerKey);

  if (!tracker || now - tracker.lastReset > interval) {
    tracker = { count: 1, actions: [eventName], lastReset: now };
  } else {
    tracker.count++;
    tracker.actions.push(eventName);
  }

  client.nukeTracker.set(trackerKey, tracker);

  console.log(
    `[ANTI-NUKE] ${executor.tag} in ${guild.name} — action #${tracker.count}: ${eventName}`,
  );

  if (tracker.count >= threshold) {
    client.nukeTracker.delete(trackerKey);

    const embed = nukeAlertEmbed(executor.id, tracker.count, eventName);
    // Send log independently — don't let it block enforcement
    sendLog(guild, embed).catch(() => {});

    const action = cfg.action ?? 'quarantine';

    if (action === 'ban') {
      await guild.bans
        .create(executor.id, { reason: 'Anti-Nuke: Exceeded destructive action threshold' })
        .catch(err => console.error('[NUKE] Ban failed:', err.message));
    } else if (action === 'kick') {
      const member = await guild.members.fetch(executor.id).catch(() => null);
      if (member) {
        await member
          .kick('Anti-Nuke: Exceeded destructive action threshold')
          .catch(err => console.error('[NUKE] Kick failed:', err.message));
      }
    } else {
      // Default: quarantine
      await quarantineUser(
        guild,
        executor.id,
        `Anti-Nuke: ${tracker.count} destructive actions in ${interval / 1000}s`,
      );
    }
  }
}
