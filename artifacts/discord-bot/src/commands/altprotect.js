import { PermissionFlagsBits } from 'discord.js';
import { getDB, saveDB } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'altprotect',
  description: 'Configure Alt-Account / Raid Protection settings',
  usage: '+altprotect <on|off|status|minage <days>|action <kick|quarantine|ban>>',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    const db = getDB();
    const cfg = db.config.altProtection;
    const sub = args[0]?.toLowerCase();

    if (sub === 'on') {
      cfg.enabled = true;
      saveDB();
      return message.reply({ embeds: [successEmbed('Alt Protection Enabled', 'Alt/Raid protection is now ACTIVE.')] });
    }

    if (sub === 'off') {
      cfg.enabled = false;
      saveDB();
      return message.reply({ embeds: [successEmbed('Alt Protection Disabled', 'Alt/Raid protection is now INACTIVE.')] });
    }

    if (sub === 'minage') {
      const days = parseInt(args[1]);
      if (isNaN(days) || days < 0) {
        return message.reply({ embeds: [errorEmbed('Invalid Value', 'Min age must be a number ≥ 0 days.')] });
      }
      cfg.minAccountAge = days;
      saveDB();
      return message.reply({
        embeds: [successEmbed('Min Age Updated', `Accounts younger than \`${days} days\` will be flagged.`)],
      });
    }

    if (sub === 'action') {
      const valid = ['kick', 'quarantine', 'ban'];
      const val = args[1]?.toLowerCase();
      if (!valid.includes(val)) {
        return message.reply({ embeds: [errorEmbed('Invalid Action', `Choose one: \`${valid.join(' | ')}\``)] });
      }
      cfg.action = val;
      saveDB();
      return message.reply({
        embeds: [successEmbed('Action Updated', `Alt-account action set to \`${val.toUpperCase()}\`.`)],
      });
    }

    // Default: status
    return message.reply({
      embeds: [infoEmbed('Alt Protection Status', '`Current configuration:`', [
        { name: '◈ STATUS', value: cfg.enabled ? '`ACTIVE`' : '`INACTIVE`', inline: true },
        { name: '◈ MIN ACCOUNT AGE', value: `\`${cfg.minAccountAge} days\``, inline: true },
        { name: '◈ ACTION', value: `\`${cfg.action?.toUpperCase() ?? 'KICK'}\``, inline: true },
      ])],
    });
  },
};
