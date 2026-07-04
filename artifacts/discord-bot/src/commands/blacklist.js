import { PermissionFlagsBits } from 'discord.js';
import { getDB, isBlacklisted, addBlacklist, removeBlacklist } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'blacklist',
  description: 'Manage the ban-on-join blacklist',
  usage: '+blacklist <add|remove|list> [@user]',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    const sub = args[0]?.toLowerCase();

    if (sub === 'list') {
      const db = getDB();
      const users = db.blacklist.users;
      const display = users.length
        ? users.map(id => `<@${id}>`).join('\n')
        : '`No users blacklisted.`';
      return message.reply({
        embeds: [infoEmbed('Blacklist', display, [
          { name: '◈ TOTAL', value: `\`${users.length} user(s)\`` },
        ])],
      });
    }

    const target = message.mentions.users.first();
    if (!target) {
      return message.reply({
        embeds: [errorEmbed('Invalid Usage', 'Usage: +blacklist <add|remove|list> [@user]')],
      });
    }

    if (sub === 'add') {
      if (isBlacklisted(target.id)) {
        return message.reply({ embeds: [errorEmbed('Already Listed', `<@${target.id}> is already blacklisted.`)] });
      }
      addBlacklist(target.id);
      return message.reply({
        embeds: [successEmbed('Blacklist Updated', `<@${target.id}> added to blacklist. They will be banned on join.`, [
          { name: '◈ STATUS', value: '`BAN-ON-JOIN ENABLED`' },
        ])],
      });
    }

    if (sub === 'remove') {
      if (!isBlacklisted(target.id)) {
        return message.reply({ embeds: [errorEmbed('Not Listed', `<@${target.id}> is not blacklisted.`)] });
      }
      removeBlacklist(target.id);
      return message.reply({
        embeds: [successEmbed('Blacklist Updated', `<@${target.id}> removed from blacklist.`, [
          { name: '◈ STATUS', value: '`BAN-ON-JOIN DISABLED`' },
        ])],
      });
    }

    return message.reply({
      embeds: [errorEmbed('Invalid Subcommand', 'Use: +blacklist <add|remove|list> [@user]')],
    });
  },
};
