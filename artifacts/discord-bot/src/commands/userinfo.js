import { PermissionFlagsBits } from 'discord.js';
import { isWhitelisted, isBlacklisted, isQuarantined, isOwner } from '../utils/database.js';
import { infoEmbed } from '../utils/embeds.js';

const MS_PER_DAY = 86_400_000;

export default {
  name: 'userinfo',
  description: 'View security profile for a user',
  usage: '+userinfo [@user]',

  async execute(client, message, args) {
    const target = message.mentions.members?.first() ?? message.member;
    const user = target.user;

    const accountAgeMs = Date.now() - user.createdTimestamp;
    const ageDays = (accountAgeMs / MS_PER_DAY).toFixed(1);

    const flags = [];
    if (isOwner(user.id)) flags.push('`OWNER`');
    if (isWhitelisted(user.id)) flags.push('`WHITELISTED`');
    if (isBlacklisted(user.id)) flags.push('`BLACKLISTED`');
    if (isQuarantined(user.id)) flags.push('`QUARANTINED`');
    if (accountAgeMs < 7 * MS_PER_DAY) flags.push('`ALT-RISK`');

    return message.reply({
      embeds: [infoEmbed(`User Profile — ${user.tag}`, `\`\`\`Security analysis for ${user.tag}\`\`\``, [
        { name: '◈ USER', value: `<@${user.id}>`, inline: true },
        { name: '◈ ID', value: `\`${user.id}\``, inline: true },
        { name: '◈ ACCOUNT AGE', value: `\`${ageDays} days\``, inline: true },
        { name: '◈ JOINED', value: target.joinedAt ? `<t:${Math.floor(target.joinedTimestamp / 1000)}:R>` : '`Unknown`', inline: true },
        { name: '◈ CREATED', value: `<t:${Math.floor(user.createdTimestamp / 1000)}:R>`, inline: true },
        { name: '◈ FLAGS', value: flags.length ? flags.join(' ') : '`NONE`', inline: true },
        { name: '◈ ROLES', value: `\`${target.roles.cache.size - 1} roles\``, inline: true },
      ])],
    });
  },
};
