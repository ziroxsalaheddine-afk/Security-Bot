import { AuditLogEvent } from 'discord.js';
import { handleNukeEvent } from './nukeCore.js';

export default {
  name: 'channelDelete',
  async execute(client, channel) {
    await handleNukeEvent(client, channel.guild, AuditLogEvent.ChannelDelete, 'channelDelete');
  },
};
