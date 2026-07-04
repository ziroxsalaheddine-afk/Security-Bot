import { EmbedBuilder } from 'discord.js';
import { COLORS } from '../config.js';

// ── Separator used across all embeds ─────────────────────────────────────────
const SEP = '▓▒░';

function timestamp() {
  return `<t:${Math.floor(Date.now() / 1000)}:F>`;
}

// ── Base embed factory ────────────────────────────────────────────────────────
function base(color = COLORS.PRIMARY) {
  return new EmbedBuilder()
    .setColor(color)
    .setFooter({ text: '[ SYSTEM :: GUARDIAN BOT ]' })
    .setTimestamp();
}

// ── Public embed builders ─────────────────────────────────────────────────────

export function successEmbed(title, description, fields = []) {
  return base(COLORS.SUCCESS)
    .setTitle(`${SEP} ${title.toUpperCase()} ${SEP}`)
    .setDescription(`\`\`\`ansi\n\u001b[0;32m${description}\u001b[0m\`\`\``)
    .addFields(fields);
}

export function errorEmbed(title, description) {
  return base(COLORS.DANGER)
    .setTitle(`${SEP} ${title.toUpperCase()} ${SEP}`)
    .setDescription(`\`\`\`ansi\n\u001b[0;31m${description}\u001b[0m\`\`\``);
}

export function infoEmbed(title, description, fields = []) {
  return base(COLORS.INFO)
    .setTitle(`${SEP} ${title.toUpperCase()} ${SEP}`)
    .setDescription(description)
    .addFields(fields);
}

export function warningEmbed(title, description, fields = []) {
  return base(COLORS.WARNING)
    .setTitle(`⚠ ${SEP} ${title.toUpperCase()} ${SEP}`)
    .setDescription(`\`\`\`ansi\n\u001b[0;33m${description}\u001b[0m\`\`\``)
    .addFields(fields);
}

export function punishEmbed(action, target, reason, executor = 'SYSTEM') {
  return base(COLORS.DANGER)
    .setTitle(`${SEP} ENFORCEMENT ACTION ${SEP}`)
    .addFields(
      { name: '◈ ACTION', value: `\`${action.toUpperCase()}\``, inline: true },
      { name: '◈ TARGET', value: `<@${target}>`, inline: true },
      { name: '◈ EXECUTOR', value: `\`${executor}\``, inline: true },
      { name: '◈ REASON', value: `\`\`\`${reason}\`\`\`` },
      { name: '◈ TIMESTAMP', value: timestamp() },
    );
}

export function nukeAlertEmbed(userId, actionCount, actionType) {
  return base(COLORS.DANGER)
    .setTitle(`${SEP} ANTI-NUKE TRIGGERED ${SEP}`)
    .setDescription('```ansi\n\u001b[0;31m[CRITICAL] Malicious activity detected. Quarantine initiated.\u001b[0m```')
    .addFields(
      { name: '◈ SUSPECT', value: `<@${userId}>`, inline: true },
      { name: '◈ ACTIONS', value: `\`${actionCount} in <10s\``, inline: true },
      { name: '◈ LAST ACTION', value: `\`${actionType}\``, inline: true },
      { name: '◈ STATUS', value: '```ROLES STRIPPED → QUARANTINED```' },
    );
}

export function automodEmbed(rule, userId, content) {
  return base(COLORS.WARNING)
    .setTitle(`${SEP} AUTOMOD ${SEP}`)
    .addFields(
      { name: '◈ RULE', value: `\`${rule}\``, inline: true },
      { name: '◈ USER', value: `<@${userId}>`, inline: true },
      { name: '◈ CONTENT', value: `\`\`\`${String(content).slice(0, 200)}\`\`\`` },
    );
}

export function altAlertEmbed(member, ageHours) {
  const ageDays = (ageHours / 24).toFixed(1);
  return base(COLORS.DANGER)
    .setTitle(`${SEP} ALT-ACCOUNT DETECTED ${SEP}`)
    .setDescription('```ansi\n\u001b[0;31m[RAID PROTECTION] New account below age threshold.\u001b[0m```')
    .addFields(
      { name: '◈ USER', value: `<@${member.id}>`, inline: true },
      { name: '◈ ACCOUNT AGE', value: `\`${ageDays} days\``, inline: true },
      { name: '◈ ACTION', value: '`KICKED`', inline: true },
    );
}

export function helpEmbed(commands) {
  const lines = commands.map(c => `\`${c.name.padEnd(18)}\` — ${c.description}`).join('\n');
  return base(COLORS.INFO)
    .setTitle(`${SEP} GUARDIAN BOT — COMMAND REFERENCE ${SEP}`)
    .setDescription(`\`\`\`\n${lines}\n\`\`\``)
    .setFooter({ text: '[ PREFIX: + ] [ SYSTEM :: GUARDIAN BOT ]' });
}
