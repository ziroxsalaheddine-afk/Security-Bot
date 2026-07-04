import { PermissionFlagsBits } from 'discord.js';
import { setLogChannel, getLogChannel } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

export default {
  name: 'setlog',
  description: 'Set the channel where security logs are sent',
  usage: '+setlog <#channel>',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Server permission.')] });
    }

    const channel = message.mentions.channels.first();
    if (!channel) {
      const current = getLogChannel();
      return message.reply({
        embeds: [infoEmbed('Log Channel', current ? `Logs → <#${current}>` : '`Not configured.`')],
      });
    }

    if (!channel.isTextBased()) {
      return message.reply({ embeds: [errorEmbed('Invalid Channel', 'Please mention a text channel.')] });
    }

    setLogChannel(channel.id);
    return message.reply({
      embeds: [successEmbed('Log Channel Set', `Security logs will be sent to <#${channel.id}>.`, [
        { name: '◈ CHANNEL', value: `<#${channel.id}>` },
      ])],
    });
  },
};
