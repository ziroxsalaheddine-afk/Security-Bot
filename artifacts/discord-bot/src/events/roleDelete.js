import { AuditLogEvent } from 'discord.js';
import { handleNukeEvent } from './nukeCore.js';

export default {
  name: 'roleDelete',
  async execute(client, role) {
    await handleNukeEvent(client, role.guild, AuditLogEvent.RoleDelete, 'roleDelete');
  },
};
