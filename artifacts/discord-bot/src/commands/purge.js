import { PermissionFlagsBits } from 'discord.js';
import { successEmbed, errorEmbed } from '../utils/embeds.js';

export default {
  name: 'purge',
  description: 'Bulk-delete messages (max 100)',
  usage: '+purge <amount> [@user]',

  async execute(client, message, args) {
    if (!message.member.permissions.has(PermissionFlagsBits.ManageMessages)) {
      return message.reply({ embeds: [errorEmbed('Access Denied', 'You need Manage Messages permission.')] });
    }

    const amount = parseInt(args[0]);
    if (isNaN(amount) || amount < 1 || amount > 100) {
      return message.reply({ embeds: [errorEmbed('Invalid Amount', 'Provide a number between 1 and 100.')] });
    }

    const targetUser = message.mentions.users.first();

    await message.delete().catch(() => {});

    let messages = await message.channel.messages.fetch({ limit: 100 });

    // Filter by target user if specified
    if (targetUser) {
      messages = messages.filter(m => m.author.id === targetUser.id);
    }

    // Discord only allows bulk-delete for messages < 14 days old
    const twoWeeksAgo = Date.now() - 14 * 24 * 60 * 60 * 1000;
    messages = messages.filter(m => m.createdTimestamp > twoWeeksAgo);
    messages = [...messages.values()].slice(0, amount);

    if (messages.length === 0) {
      return message.channel
        .send({ embeds: [errorEmbed('No Messages', 'No eligible messages found to delete.')] })
        .then(m => setTimeout(() => m.delete().catch(() => {}), 4000));
    }

    try {
      await message.channel.bulkDelete(messages, true);
      const reply = await message.channel.send({
        embeds: [successEmbed('Purge Complete', `Deleted \`${messages.length}\` message(s).`, [
          { name: '◈ TARGET', value: targetUser ? `<@${targetUser.id}>` : '`All Users`', inline: true },
          { name: '◈ BY', value: `<@${message.author.id}>`, inline: true },
        ])],
      });
      setTimeout(() => reply.delete().catch(() => {}), 5000);
    } catch (err) {
      message.channel
        .send({ embeds: [errorEmbed('Purge Failed', err.message)] })
        .then(m => setTimeout(() => m.delete().catch(() => {}), 5000));
    }
  },
};
