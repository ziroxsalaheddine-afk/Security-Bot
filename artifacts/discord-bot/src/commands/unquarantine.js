import { PermissionFlagsBits } from 'discord.js';
import { isQuarantined, getQuarantineData, removeQuarantine } from '../utils/database.js';
import { successEmbed, errorEmbed, warningEmbed } from '../utils/embeds.js';

export default {
  name: 'unquarantine',
  description: 'Release a user from quarantine and restore their roles',
  usage: '+unquarantine <@user>',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    const target = message.mentions.members?.first();
    if (!target) {
      return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Mention a user: +unquarantine @user')] });
    }

    const guildId = message.guild.id;

    if (!isQuarantined(target.id, guildId)) {
      return message.reply({ embeds: [warningEmbed('Not Quarantined', `<@${target.id}> is not quarantined in this server.`)] });
    }

    const data = getQuarantineData(target.id, guildId);
    const savedRoles = data?.savedRoles ?? [];

    // Only restore roles that still exist in the guild
    const validRoles = savedRoles.filter(id => message.guild.roles.cache.has(id));

    try {
      await target.roles.set(validRoles, `Released from quarantine by ${message.author.tag}`);
    } catch (err) {
      return message.reply({ embeds: [errorEmbed('Enforcement Failed', `Could not restore roles: ${err.message}`)] });
    }

    // Only remove from DB after successful role restoration
    removeQuarantine(target.id, guildId);

    return message.reply({
      embeds: [successEmbed('User Released', `<@${target.id}> has been released from quarantine.`, [
        { name: '◈ ROLES RESTORED', value: `\`${validRoles.length}/${savedRoles.length} roles\``, inline: true },
        { name: '◈ BY', value: `<@${message.author.id}>`, inline: true },
      ])],
    });
  },
};
