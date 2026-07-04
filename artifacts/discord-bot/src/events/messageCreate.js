import { PermissionFlagsBits } from 'discord.js';
import { PREFIX } from '../config.js';
import { getDB, isWhitelisted } from '../utils/database.js';
import { isAuthorized } from '../utils/auth.js';
import { automodEmbed, errorEmbed, accessDeniedEmbed } from '../utils/embeds.js';

// ── Regex ─────────────────────────────────────────────────────────────────────
const INVITE_REGEX = /discord(?:\.gg|\.com\/invite|app\.com\/invite)\/[a-zA-Z0-9-]+/i;
const URL_REGEX = /https?:\/\/[^\s]+/i;

// ── Helpers ───────────────────────────────────────────────────────────────────

async function sendLog(guild, embed) {
  try {
    const db = getDB();
    const ch = guild.channels.cache.get(db.logs?.channelId);
    if (ch?.isTextBased()) await ch.send({ embeds: [embed] });
  } catch { /* non-critical */ }
}

async function applyAction(member, action, reason) {
  const MS_5MIN = 300_000;
  switch (action) {
    case 'timeout':
      if (!member.isCommunicationDisabled()) {
        await member.timeout(MS_5MIN, reason).catch(() => {});
      }
      break;
    case 'kick':
      await member.kick(reason).catch(() => {});
      break;
    case 'ban':
      await member.ban({ reason }).catch(() => {});
      break;
    case 'delete':
    default:
      break; // message already deleted before this is called
  }
}

// ── Anti-spam tracker ─────────────────────────────────────────────────────────
async function handleAntiSpam(client, message, cfg) {
  const { messageLimit, interval, timeoutDuration } = cfg;
  const trackerKey = `${message.guild.id}:${message.author.id}`;
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

    // Enforce first — notify after
    const mem = message.member;
    const timedOut = !mem.isCommunicationDisabled() &&
      await mem.timeout(timeoutDuration, 'Anti-Spam').catch(() => false);
    if (!timedOut) await mem.kick('Anti-Spam: timeout unavailable').catch(() => {});

    const embed = automodEmbed('ANTI-SPAM', message.author.id, `${tracker.count} msgs in <${interval / 1000}s`);
    message.channel.send({ embeds: [embed] }).catch(() => {});
    sendLog(message.guild, embed);
    return true;
  }
  return false;
}

// ── Anti-invite (Discord invite links) ────────────────────────────────────────
async function handleAntiInvite(message, cfg) {
  if (!INVITE_REGEX.test(message.content)) return false;
  await message.delete().catch(() => {});
  await applyAction(message.member, cfg.action, 'Anti-Invite: Discord invite link detected');
  const embed = automodEmbed('ANTI-INVITE', message.author.id, message.content);
  message.channel.send({ embeds: [embed] }).then(m => setTimeout(() => m.delete().catch(() => {}), 5000)).catch(() => {});
  sendLog(message.guild, embed);
  return true;
}

// ── Anti-link (all other http/https links) ────────────────────────────────────
async function handleAntiLink(message, cfg) {
  // Strip invite links first so they're handled by anti-invite
  const content = message.content.replace(INVITE_REGEX, '');
  if (!URL_REGEX.test(content)) return false;
  await message.delete().catch(() => {});
  await applyAction(message.member, cfg.action, 'Anti-Link: External URL detected');
  const embed = automodEmbed('ANTI-LINK', message.author.id, message.content);
  message.channel.send({ embeds: [embed] }).then(m => setTimeout(() => m.delete().catch(() => {}), 5000)).catch(() => {});
  sendLog(message.guild, embed);
  return true;
}

// ── Anti-mass-mention ─────────────────────────────────────────────────────────
async function handleAntiMassMention(message, cfg) {
  const mentionCount =
    message.mentions.users.size +
    message.mentions.roles.size +
    (message.mentions.everyone ? 1 : 0);
  if (mentionCount < cfg.mentionLimit) return false;

  await message.delete().catch(() => {});
  await message.member.timeout(cfg.timeoutDuration ?? 300_000, `Anti-Mass-Mention: ${mentionCount} mentions`).catch(() => {});

  const embed = automodEmbed('ANTI-MASS-MENTION', message.author.id, `${mentionCount} mentions`);
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
    const userId = message.author.id;
    const { prefix } = db.config;

    // ── Command routing ───────────────────────────────────────────────────────
    if (message.content.startsWith(prefix)) {
      const args = message.content.slice(prefix.length).trim().split(/\s+/);
      const commandName = args.shift().toLowerCase();
      const command = client.commands.get(commandName);
      if (!command) return;

      // ── STRICT AUTH GATE: owner/co-owner only ─────────────────────────────
      // Bootstrap exception: when no owners are configured yet, the Discord
      // guild owner may run `+owner add` once to register the first bot owner.
      const isBootstrap =
        commandName === 'owner' &&
        db.owners.length === 0 &&
        (db.coowners ?? []).length === 0 &&
        message.guild.ownerId === userId;

      if (!isAuthorized(userId) && !isBootstrap) {
        const reply = await message.reply({ embeds: [accessDeniedEmbed()] }).catch(() => null);
        if (reply) setTimeout(() => reply.delete().catch(() => {}), 5000);
        message.delete().catch(() => {});
        return;
      }

      try {
        await command.execute(client, message, args);
      } catch (err) {
        console.error(`[CMD ERROR] ${commandName}:`, err);
        message.reply({ embeds: [errorEmbed('Error', err.message)] }).catch(() => {});
      }
      return;
    }

    // ── Automod (skip owners, co-owners, and whitelisted users) ──────────────
    const anti = db.anti ?? {};
    const automod = db.config.automod;
    if (!automod.enabled) return;

    // Authorized users (owners/co-owners) and whitelisted users bypass automod
    if (isAuthorized(userId) || isWhitelisted(userId)) return;

    // Anti-spam
    if (automod.antiSpam?.enabled) {
      const triggered = await handleAntiSpam(client, message, automod.antiSpam);
      if (triggered) return;
    }

    // Anti-invite (check before anti-link so it gets correct action)
    if (anti.invite?.enabled) {
      const triggered = await handleAntiInvite(message, anti.invite);
      if (triggered) return;
    }

    // Anti-link
    if (anti.link?.enabled) {
      const triggered = await handleAntiLink(message, anti.link);
      if (triggered) return;
    }

    // Anti-mass-mention
    if (automod.antiMassMention?.enabled) {
      await handleAntiMassMention(message, automod.antiMassMention);
    }
  },
};
