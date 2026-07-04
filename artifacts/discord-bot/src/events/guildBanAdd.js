import { AuditLogEvent } from 'discord.js';
import { handleNukeEvent } from './nukeCore.js';

export default {
  name: 'guildBanAdd',
  async execute(client, ban) {
    await handleNukeEvent(client, ban.guild, AuditLogEvent.MemberBanAdd, 'guildBanAdd');
  },
};
