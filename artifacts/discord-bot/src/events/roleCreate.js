import { AuditLogEvent } from 'discord.js';
import { handleNukeEvent } from './nukeCore.js';

export default {
  name: 'roleCreate',
  async execute(client, role) {
    await handleNukeEvent(client, role.guild, AuditLogEvent.RoleCreate, 'roleCreate');
  },
};
