import { PermissionFlagsBits } from 'discord.js';
import { getDB, isWhitelisted, addWhitelist, removeWhitelist } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'whitelist',
  description: 'Manage the user whitelist (bypass automod & anti-nuke)',
  usage: '+whitelist <add|remove|list> [@user]',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    const sub = args[0]?.toLowerCase();

    if (sub === 'list') {
      const db = getDB();
      const users = db.whitelist.users;
      const display = users.length
        ? users.map(id => `<@${id}>`).join('\n')
        : '`No users whitelisted.`';
      return message.reply({
        embeds: [infoEmbed('Whitelist', display, [
          { name: '◈ TOTAL', value: `\`${users.length} user(s)\`` },
        ])],
      });
    }

    const target = message.mentions.users.first();
    if (!target) {
      return message.reply({
        embeds: [errorEmbed('Invalid Usage', 'Usage: +whitelist <add|remove|list> [@user]')],
      });
    }

    if (sub === 'add') {
      if (isWhitelisted(target.id)) {
        return message.reply({ embeds: [errorEmbed('Already Listed', `<@${target.id}> is already whitelisted.`)] });
      }
      addWhitelist(target.id);
      return message.reply({
        embeds: [successEmbed('Whitelist Updated', `<@${target.id}> has been whitelisted.`, [
          { name: '◈ STATUS', value: '`BYPASS ENABLED`' },
        ])],
      });
    }

    if (sub === 'remove') {
      if (!isWhitelisted(target.id)) {
        return message.reply({ embeds: [errorEmbed('Not Listed', `<@${target.id}> is not on the whitelist.`)] });
      }
      removeWhitelist(target.id);
      return message.reply({
        embeds: [successEmbed('Whitelist Updated', `<@${target.id}> has been removed from the whitelist.`, [
          { name: '◈ STATUS', value: '`BYPASS REVOKED`' },
        ])],
      });
    }

    return message.reply({
      embeds: [errorEmbed('Invalid Subcommand', 'Use: +whitelist <add|remove|list> [@user]')],
    });
  },
};
