import { PermissionFlagsBits } from 'discord.js';
import { PREFIX } from '../config.js';
import { getDB, isWhitelisted } from '../utils/database.js';
import { automodEmbed, errorEmbed } from '../utils/embeds.js';

// ── URL / invite pattern ──────────────────────────────────────────────────────
const URL_REGEX = /https?:\/\/[^\s]+/i;
const INVITE_REGEX = /discord(?:\.gg|\.com\/invite|app\.com\/invite)\/[a-zA-Z0-9-]+/i;

// ── Helpers ───────────────────────────────────────────────────────────────────

function isAdmin(member) {
  return (
    member.permissions.has(PermissionFlagsBits.Administrator) ||
    member.permissions.has(PermissionFlagsBits.ManageGuild)
  );
}

async function sendLog(guild, embed) {
  try {
    const db = getDB();
    const channelId = db.logs?.channelId;
    if (!channelId) return;
    const ch = guild.channels.cache.get(channelId);
    if (ch?.isTextBased()) await ch.send({ embeds: [embed] });
  } catch { /* non-critical — don't let log failure abort enforcement */ }
}

async function timeoutMember(member, durationMs, reason) {
  if (member.isCommunicationDisabled()) return false;
  try {
    await member.timeout(durationMs, reason);
    return true;
  } catch {
    return false;
  }
}

// ── Anti-spam tracker ─────────────────────────────────────────────────────────
async function handleAntiSpam(client, message, cfg) {
  const { messageLimit, interval, timeoutDuration } = cfg;
  const userId = message.author.id;
  const guildId = message.guild.id;
  // Guild-scoped key prevents cross-guild false positives
  const trackerKey = `${guildId}:${userId}`;
  const now = Date.now();

  let tracker = client.spamTracker.get(trackerKey);
  if (!tracker || now - tracker.firstMessage > interval) {
    tracker = { count: 1, firstMessage: now };
  } else {
    tracker.count++;
  }
  client.spamTracker.set(trackerKey, tracker);

  if (tracker.count >= messageLimit) {
    client.spamTracker.delete(trackerKey);

    // ── Enforce FIRST (fail-safe), then notify ────────────────────────────
    const timedOut = await timeoutMember(
      message.member,
      timeoutDuration,
      'Anti-Spam: Exceeded message rate limit',
    );

    if (!timedOut) {
      // Fall back to kick if timeout fails (e.g. missing permission or already timed out)
      await message.member.kick('Anti-Spam: Rate limit exceeded, timeout unavailable').catch(() => {});
    }

    // Notify and log after enforcement (non-blocking)
    const embed = automodEmbed(
      'ANTI-SPAM',
      userId,
      `${tracker.count} messages in <${interval / 1000}s`,
    );
    message.channel.send({ embeds: [embed] }).catch(() => {});
    sendLog(message.guild, embed);

    return true;
  }
  return false;
}

// ── Anti-link filter ──────────────────────────────────────────────────────────
async function handleAntiLink(message, cfg) {
  const content = message.content;
  const hasLink = URL_REGEX.test(content) || INVITE_REGEX.test(content);
  if (!hasLink) return false;

  // Delete first — enforcement is primary
  await message.delete().catch(() => {});

  const embed = automodEmbed('ANTI-LINK', message.author.id, content);
  message.channel
    .send({ embeds: [embed] })
    .then(m => setTimeout(() => m.delete().catch(() => {}), 5000))
    .catch(() => {});
  sendLog(message.guild, embed);

  return true;
}

// ── Anti-mass-mention filter ──────────────────────────────────────────────────
async function handleAntiMassMention(message, cfg) {
  const { mentionLimit, timeoutDuration = 300_000 } = cfg;
  const mentionCount =
    message.mentions.users.size +
    message.mentions.roles.size +
    (message.mentions.everyone ? 1 : 0);

  if (mentionCount < mentionLimit) return false;

  // Delete and enforce FIRST
  await message.delete().catch(() => {});

  await timeoutMember(
    message.member,
    timeoutDuration,
    `Anti-Mass-Mention: ${mentionCount} mentions`,
  );

  const embed = automodEmbed(
    'ANTI-MASS-MENTION',
    message.author.id,
    `${mentionCount} mentions detected`,
  );
  message.channel.send({ embeds: [embed] }).catch(() => {});
  sendLog(message.guild, embed);

  return true;
}

// ── Main event ────────────────────────────────────────────────────────────────
export default {
  name: 'messageCreate',

  async execute(client, message) {
    if (message.author.bot || !message.guild) return;

    const db = getDB();
    const { prefix } = db.config;
    const userId = message.author.id;

    // ── Command routing ───────────────────────────────────────────────────────
    if (message.content.startsWith(prefix)) {
      const args = message.content.slice(prefix.length).trim().split(/\s+/);
      const commandName = args.shift().toLowerCase();
      const command = client.commands.get(commandName);

      if (!command) return;

      try {
        await command.execute(client, message, args);
      } catch (err) {
        console.error(`[CMD ERROR] ${commandName}:`, err);
        message.reply({ embeds: [errorEmbed('Error', err.message)] }).catch(() => {});
      }
      return;
    }

    // ── Automod ───────────────────────────────────────────────────────────────
    const automod = db.config.automod;
    if (!automod.enabled) return;

    // Skip whitelisted users and admins (also skip bot owners)
    if (isWhitelisted(userId) || isAdmin(message.member) || db.owners.includes(userId)) return;

    // Anti-spam
    if (automod.antiSpam?.enabled) {
      const triggered = await handleAntiSpam(client, message, automod.antiSpam);
      if (triggered) return;
    }

    // Anti-link
    if (automod.antiLink?.enabled) {
      const triggered = await handleAntiLink(message, automod.antiLink);
      if (triggered) return;
    }

    // Anti-mass-mention
    if (automod.antiMassMention?.enabled) {
      await handleAntiMassMention(message, automod.antiMassMention);
    }
  },
};
