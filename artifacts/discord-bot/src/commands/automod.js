import { PermissionFlagsBits } from 'discord.js';
import { getDB, saveDB } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

const RULES = ['antispam', 'antilink', 'antimassmention'];

export default {
  name: 'automod',
  description: 'Configure Automod rules (antispam, antilink, antimassmention)',
  usage: '+automod <on|off|status|rule <name> <on|off>>',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    const db = getDB();
    const cfg = db.config.automod;
    const sub = args[0]?.toLowerCase();

    if (sub === 'on') {
      cfg.enabled = true;
      saveDB();
      return message.reply({ embeds: [successEmbed('Automod Enabled', 'Automod protection is now ACTIVE.')] });
    }

    if (sub === 'off') {
      cfg.enabled = false;
      saveDB();
      return message.reply({ embeds: [successEmbed('Automod Disabled', 'Automod protection is now INACTIVE.')] });
    }

    if (sub === 'rule') {
      const ruleName = args[1]?.toLowerCase();
      const toggle = args[2]?.toLowerCase();

      if (!RULES.includes(ruleName)) {
        return message.reply({
          embeds: [errorEmbed('Invalid Rule', `Choose one: \`${RULES.join(' | ')}\``)],
        });
      }

      if (!['on', 'off'].includes(toggle)) {
        return message.reply({ embeds: [errorEmbed('Invalid Toggle', 'Use: `on` or `off`')] });
      }

      const cfgMap = {
        antispam: 'antiSpam',
        antilink: 'antiLink',
        antimassmention: 'antiMassMention',
      };
      cfg[cfgMap[ruleName]].enabled = toggle === 'on';
      saveDB();

      return message.reply({
        embeds: [successEmbed(
          'Rule Updated',
          `Rule \`${ruleName}\` is now \`${toggle.toUpperCase()}\`.`,
        )],
      });
    }

    if (sub === 'spamthreshold') {
      const limit = parseInt(args[1]);
      const interval = parseInt(args[2]);
      if (!isNaN(limit)) cfg.antiSpam.messageLimit = limit;
      if (!isNaN(interval)) cfg.antiSpam.interval = interval;
      saveDB();
      return message.reply({
        embeds: [successEmbed('Spam Threshold Updated', `Limit: \`${cfg.antiSpam.messageLimit} msgs / ${cfg.antiSpam.interval / 1000}s\``)],
      });
    }

    // Default: show full status
    const { antiSpam: sp, antiLink: al, antiMassMention: am } = cfg;
    return message.reply({
      embeds: [infoEmbed('Automod Status', '`Current automod configuration:`', [
        { name: '◈ AUTOMOD', value: cfg.enabled ? '`ACTIVE`' : '`INACTIVE`', inline: true },
        { name: '◈ ANTI-SPAM', value: sp.enabled ? '`ON`' : '`OFF`', inline: true },
        { name: '◈ ANTI-LINK', value: al.enabled ? '`ON`' : '`OFF`', inline: true },
        { name: '◈ ANTI-MENTION', value: am.enabled ? '`ON`' : '`OFF`', inline: true },
        { name: '◈ SPAM LIMIT', value: `\`${sp.messageLimit} msgs / ${sp.interval / 1000}s\``, inline: true },
        { name: '◈ MENTION LIMIT', value: `\`${am.mentionLimit} mentions\``, inline: true },
      ])],
    });
  },
};
