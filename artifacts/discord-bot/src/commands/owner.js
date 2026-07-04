import { getDB, isOwner, addOwner, removeOwner } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'owner',
  description: 'Manage bot owners (bypass security systems — server owner only)',
  usage: '+owner <add|remove|list> [@user]',

  async execute(client, message, args) {
    // ONLY the Discord guild owner can manage bot owners.
    // Bot owners cannot grant other bot owners to prevent privilege escalation.
    const isGuildOwner = message.guild.ownerId === message.author.id;
    if (!isGuildOwner) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'Only the server owner can manage bot owners.')] });
    }

    const sub = args[0]?.toLowerCase();
    const db = getDB();

    if (sub === 'list') {
      const owners = db.owners;
      const display = owners.length
        ? owners.map(id => `<@${id}>`).join('\n')
        : '`No owners configured.`';
      return message.reply({
        embeds: [infoEmbed('Bot Owners', display, [
          { name: '◈ TOTAL', value: `\`${owners.length} owner(s)\`` },
          { name: '◈ NOTE', value: '`Owners bypass all automod and anti-nuke checks.`' },
        ])],
      });
    }

    if (sub === 'add') {
      const target = message.mentions.users.first();
      if (!target) {
        return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Usage: +owner add @user')] });
      }
      if (isOwner(target.id)) {
        return message.reply({ embeds: [errorEmbed('Already Owner', `<@${target.id}> is already a bot owner.`)] });
      }
      addOwner(target.id);
      return message.reply({
        embeds: [successEmbed('Owner Added', `<@${target.id}> is now a bot owner.`, [
          { name: '◈ BYPASS', value: '`ANTI-NUKE • AUTOMOD • ALT-PROTECT`' },
        ])],
      });
    }

    if (sub === 'remove') {
      const target = message.mentions.users.first();
      if (!target) {
        return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Usage: +owner remove @user')] });
      }
      if (!isOwner(target.id)) {
        return message.reply({ embeds: [errorEmbed('Not Owner', `<@${target.id}> is not a bot owner.`)] });
      }
      removeOwner(target.id);
      return message.reply({
        embeds: [successEmbed('Owner Removed', `<@${target.id}> is no longer a bot owner.`)],
      });
    }

    return message.reply({
      embeds: [errorEmbed('Invalid Subcommand', 'Use: +owner <add|remove|list> [@user]')],
    });
  },
};
