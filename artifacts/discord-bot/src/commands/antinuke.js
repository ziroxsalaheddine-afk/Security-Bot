import { PermissionFlagsBits } from 'discord.js';
import { getDB, saveDB } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'antinuke',
  description: 'Configure Anti-Nuke system settings',
  usage: '+antinuke <on|off|status|threshold <n>|interval <ms>|action <quarantine|kick|ban>>',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.Administrator)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Administrator permission.')] });
    }

    const db = getDB();
    const cfg = db.config.antinuke;
    const sub = args[0]?.toLowerCase();

    if (sub === 'on') {
      cfg.enabled = true;
      saveDB();
      return message.reply({ embeds: [successEmbed('Anti-Nuke Enabled', 'Anti-Nuke protection is now ACTIVE.')] });
    }

    if (sub === 'off') {
      cfg.enabled = false;
      saveDB();
      return message.reply({ embeds: [successEmbed('Anti-Nuke Disabled', 'Anti-Nuke protection is now INACTIVE.')] });
    }

    if (sub === 'threshold') {
      const val = parseInt(args[1]);
      if (isNaN(val) || val < 1) {
        return message.reply({ embeds: [errorEmbed('Invalid Value', 'Threshold must be a number ≥ 1.')] });
      }
      cfg.threshold = val;
      saveDB();
      return message.reply({
        embeds: [successEmbed('Threshold Updated', `Anti-Nuke will trigger after \`${val}\` destructive actions.`)],
      });
    }

    if (sub === 'interval') {
      const val = parseInt(args[1]);
      if (isNaN(val) || val < 1000) {
        return message.reply({ embeds: [errorEmbed('Invalid Value', 'Interval must be in milliseconds (≥ 1000).')] });
      }
      cfg.interval = val;
      saveDB();
      return message.reply({
        embeds: [successEmbed('Interval Updated', `Time window set to \`${val}ms\` (${val / 1000}s).`)],
      });
    }

    if (sub === 'action') {
      const valid = ['quarantine', 'kick', 'ban'];
      const val = args[1]?.toLowerCase();
      if (!valid.includes(val)) {
        return message.reply({ embeds: [errorEmbed('Invalid Action', `Choose one: \`${valid.join(' | ')}\``)] });
      }
      cfg.action = val;
      saveDB();
      return message.reply({
        embeds: [successEmbed('Action Updated', `Nuke punishment set to \`${val.toUpperCase()}\`.`)],
      });
    }

    // Default: show status
    return message.reply({
      embeds: [infoEmbed('Anti-Nuke Status', '`Current configuration:`', [
        { name: '◈ STATUS', value: cfg.enabled ? '`ACTIVE`' : '`INACTIVE`', inline: true },
        { name: '◈ THRESHOLD', value: `\`${cfg.threshold} actions\``, inline: true },
        { name: '◈ INTERVAL', value: `\`${cfg.interval / 1000}s\``, inline: true },
        { name: '◈ ACTION', value: `\`${cfg.action?.toUpperCase() ?? 'QUARANTINE'}\``, inline: true },
      ])],
    });
  },
};
