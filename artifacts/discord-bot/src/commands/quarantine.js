import { PermissionFlagsBits } from 'discord.js';
import {
  addQuarantine,
  isQuarantined,
  getQuarantineRole,
  setQuarantineRole,
} from '../utils/database.js';
import { successEmbed, errorEmbed, warningEmbed } from '../utils/embeds.js';

export default {
  name: 'quarantine',
  description: 'Quarantine a user (strip roles, apply quarantine role)',
  usage: '+quarantine <@user> [reason] | +quarantine setrole @role',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    // Sub-command: +quarantine setrole <@role>
    if (args[0] === 'setrole') {
      const role = message.mentions.roles.first();
      if (!role) {
        return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Mention a role: +quarantine setrole @role')] });
      }
      setQuarantineRole(role.id);
      return message.reply({
        embeds: [successEmbed('Quarantine Role Set', `Quarantine role is now ${role.name}`, [
          { name: '◈ ROLE', value: `<@&${role.id}>` },
        ])],
      });
    }

    const target = message.mentions.members?.first();
    if (!target) {
      return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Mention a user: +quarantine @user [reason]')] });
    }

    if (isQuarantined(target.id, message.guild.id)) {
      return message.reply({ embeds: [warningEmbed('Already Quarantined', `<@${target.id}> is already quarantined.`)] });
    }

    const quarantineRoleId = getQuarantineRole();
    if (!quarantineRoleId) {
      return message.reply({
        embeds: [errorEmbed('No Quarantine Role', 'Set one first: +quarantine setrole @role')],
      });
    }

    const reason = args.slice(1).join(' ') || 'No reason provided';
    const savedRoles = target.roles.cache
      .filter(r => r.id !== message.guild.id)
      .map(r => r.id);

    // Enforce role strip first — only persist state on success
    try {
      await target.roles.set([quarantineRoleId], `Quarantined by ${message.author.tag}: ${reason}`);
    } catch (err) {
      return message.reply({ embeds: [errorEmbed('Enforcement Failed', `Could not modify roles: ${err.message}`)] });
    }

    // Only save to DB after successful role mutation
    addQuarantine(target.id, {
      reason,
      guild: message.guild.id,
      savedRoles,
      executor: message.author.id,
    });

    return message.reply({
      embeds: [successEmbed('User Quarantined', `<@${target.id}> has been quarantined.`, [
        { name: '◈ REASON', value: `\`${reason}\``, inline: true },
        { name: '◈ BY', value: `<@${message.author.id}>`, inline: true },
        { name: '◈ SAVED ROLES', value: `\`${savedRoles.length} roles stored\`` },
      ])],
    });
  },
};
