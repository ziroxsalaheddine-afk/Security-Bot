import { helpEmbed } from '../utils/embeds.js';

export default {
  name: 'help',
  description: 'Show all available commands',
  usage: '+help',

  async execute(client, message, args) {
    const commands = [...client.commands.values()].map(c => ({
      name: `+${c.name}`,
      description: c.description ?? 'No description.',
    }));

    await message.reply({ embeds: [helpEmbed(commands)] });
  },
};
