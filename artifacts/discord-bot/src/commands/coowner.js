import { getDB, isOwner, isCoOwner, addCoOwner, removeCoOwner } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'coowner',
  description: 'Manage co-owners (Application Owner only)',
  usage: '+coowner <add|remove|list> [@user]',

  async execute(client, message, args) {
    // ONLY the application owner (db.owners) can manage co-owners.
    // Co-owners themselves cannot grant co-owner status to others.
    if (!isOwner(message.author.id)) {
      return message.reply({
        embeds: [errorEmbed('Access Denied', 'Only the Application Owner can manage co-owners.')],
      });
    }

    const sub = args[0]?.toLowerCase();
    const db = getDB();

    // ── List ──────────────────────────────────────────────────────────────────
    if (sub === 'list') {
      const coowners = db.coowners ?? [];
      const display = coowners.length
        ? coowners.map((id, i) => `  ${i + 1}. <@${id}> [ ${id} ]`).join('\n')
        : '  No co-owners configured.';

      return message.reply({
        embeds: [
          infoEmbed('Co-Owner Registry', `\`\`\`\n${display}\n\`\`\``, [
            { name: '◈ TOTAL', value: `\`${coowners.length} co-owner(s)\``, inline: true },
            { name: '◈ ACCESS', value: '`All admin/security commands`', inline: true },
          ]),
        ],
      });
    }

    // ── Add ───────────────────────────────────────────────────────────────────
    if (sub === 'add') {
      const target = message.mentions.users.first();
      if (!target) {
        return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Usage: +coowner add @user')] });
      }
      if (isOwner(target.id)) {
        return message.reply({ embeds: [errorEmbed('Already Owner', `<@${target.id}> is the Application Owner — no co-owner rank needed.`)] });
      }
      if (isCoOwner(target.id)) {
        return message.reply({ embeds: [errorEmbed('Already Co-Owner', `<@${target.id}> is already a co-owner.`)] });
      }

      addCoOwner(target.id);
      return message.reply({
        embeds: [
          successEmbed('Co-Owner Added', `<@${target.id}> has been promoted to Co-Owner.`, [
            { name: '◈ USER', value: `<@${target.id}>`, inline: true },
            { name: '◈ ID', value: `\`${target.id}\``, inline: true },
            { name: '◈ PRIVILEGES', value: '`All security & admin commands`', inline: true },
            { name: '◈ BYPASS', value: '`ANTI-NUKE • AUTOMOD • ALT-PROTECT • ALL COMMANDS`' },
          ]),
        ],
      });
    }

    // ── Remove ────────────────────────────────────────────────────────────────
    if (sub === 'remove') {
      const target = message.mentions.users.first();
      if (!target) {
        return message.reply({ embeds: [errorEmbed('Invalid Usage', 'Usage: +coowner remove @user')] });
      }
      if (!isCoOwner(target.id)) {
        return message.reply({ embeds: [errorEmbed('Not a Co-Owner', `<@${target.id}> is not on the co-owner list.`)] });
      }

      removeCoOwner(target.id);
      return message.reply({
        embeds: [
          successEmbed('Co-Owner Removed', `<@${target.id}> has been demoted from Co-Owner.`, [
            { name: '◈ USER', value: `<@${target.id}>`, inline: true },
            { name: '◈ STATUS', value: '`PRIVILEGES REVOKED`', inline: true },
          ]),
        ],
      });
    }

    return message.reply({
      embeds: [errorEmbed('Invalid Subcommand', 'Usage: +coowner <add|remove|list> [@user]')],
    });
  },
};
