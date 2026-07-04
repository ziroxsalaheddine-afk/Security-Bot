import { ChannelType, OverwriteType, PermissionsBitField } from 'discord.js';
import {
  generateBackupId,
  saveBackup,
  getBackup,
  listBackups,
} from '../utils/database.js';
import {
  backupEmbed,
  backupLoadWarningEmbed,
  successEmbed,
  errorEmbed,
  infoEmbed,
} from '../utils/embeds.js';

// ── Rate-limit safe delay ─────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ── Serialize a role for backup ───────────────────────────────────────────────
function serializeRole(role) {
  return {
    name: role.name,
    color: role.color,
    hoist: role.hoist,
    mentionable: role.mentionable,
    permissions: role.permissions.bitfield.toString(),
    position: role.position,
  };
}

// ── Serialize permission overwrites ───────────────────────────────────────────
function serializeOverwrites(channel, guild) {
  return channel.permissionOverwrites.cache.map(ow => ({
    id: ow.id,
    type: ow.type, // 0 = role, 1 = member
    allow: ow.allow.bitfield.toString(),
    deny: ow.deny.bitfield.toString(),
    // Store name so we can re-resolve after restore
    name: ow.type === OverwriteType.Role
      ? guild.roles.cache.get(ow.id)?.name ?? null
      : null,
  }));
}

// ── Serialize a channel ───────────────────────────────────────────────────────
function serializeChannel(channel, guild) {
  const base = {
    name: channel.name,
    type: channel.type,
    position: channel.position,
    permissionOverwrites: serializeOverwrites(channel, guild),
  };
  if (channel.type === ChannelType.GuildText) {
    base.topic = channel.topic ?? null;
    base.nsfw = channel.nsfw ?? false;
    base.rateLimitPerUser = channel.rateLimitPerUser ?? 0;
  }
  if (channel.type === ChannelType.GuildVoice) {
    base.bitrate = channel.bitrate ?? 64000;
    base.userLimit = channel.userLimit ?? 0;
  }
  if (channel.parentId) {
    base.parentName = channel.parent?.name ?? null;
  }
  return base;
}

// ── Restore: apply permission overwrites by role name ────────────────────────
function buildOverwrites(savedOverwrites, roleMap) {
  return savedOverwrites
    .map(ow => {
      if (ow.type === OverwriteType.Role) {
        const role = roleMap.get(ow.name) ?? null;
        if (!role) return null;
        return {
          id: role.id,
          type: OverwriteType.Role,
          allow: BigInt(ow.allow),
          deny: BigInt(ow.deny),
        };
      }
      return null; // Skip member-specific overwrites during restore
    })
    .filter(Boolean);
}

// ── BACKUP CREATE ─────────────────────────────────────────────────────────────
async function handleCreate(message) {
  const guild = message.guild;
  const statusMsg = await message.reply({ embeds: [infoEmbed('Backup', 'Scanning server layout...')] });

  // Roles: exclude @everyone and bot-managed roles
  const roles = guild.roles.cache
    .filter(r => r.id !== guild.id && !r.managed)
    .sort((a, b) => a.position - b.position)
    .map(r => serializeRole(r));

  // Categories
  const categories = guild.channels.cache
    .filter(c => c.type === ChannelType.GuildCategory)
    .sort((a, b) => a.position - b.position)
    .map(c => ({
      name: c.name,
      position: c.position,
      permissionOverwrites: serializeOverwrites(c, guild),
    }));

  // Channels (text + voice, excluding categories)
  const channels = guild.channels.cache
    .filter(c =>
      c.type === ChannelType.GuildText ||
      c.type === ChannelType.GuildVoice ||
      c.type === ChannelType.GuildAnnouncement,
    )
    .sort((a, b) => a.position - b.position)
    .map(c => serializeChannel(c, guild));

  const id = generateBackupId();
  saveBackup(id, {
    guildId: guild.id,
    guildName: guild.name,
    createdAt: Date.now(),
    createdBy: message.author.id,
    roles,
    categories,
    channels,
  });

  await statusMsg.edit({
    embeds: [backupEmbed(id, guild.name, roles.length, categories.length, channels.length)],
  });
}

// ── BACKUP LOAD ───────────────────────────────────────────────────────────────
async function handleLoad(message, backupId) {
  const backup = getBackup(backupId);
  if (!backup) {
    return message.reply({ embeds: [errorEmbed('Backup Not Found', `No backup found with ID \`${backupId}\`.`)] });
  }

  const guild = message.guild;

  // Preflight: verify the bot has the permissions needed for a full restore
  const me = guild.members.me;
  if (!me.permissions.has('ManageRoles') || !me.permissions.has('ManageChannels')) {
    return message.reply({
      embeds: [errorEmbed(
        'Missing Permissions',
        'Bot requires Manage Roles and Manage Channels permissions to restore a backup.',
      )],
    });
  }

  // Show confirmation warning
  const warningMsg = await message.reply({ embeds: [backupLoadWarningEmbed(backupId, backup)] });

  // Wait for "CONFIRM"
  let confirmed = false;
  try {
    const collected = await message.channel.awaitMessages({
      filter: m => m.author.id === message.author.id,
      max: 1,
      time: 30_000,
      errors: ['time'],
    });
    const response = collected.first();
    confirmed = response?.content?.trim() === 'CONFIRM';
    response?.delete().catch(() => {});
  } catch {
    confirmed = false;
  }

  await warningMsg.delete().catch(() => {});

  if (!confirmed) {
    const cancel = await message.channel.send({
      embeds: [infoEmbed('Backup Load Cancelled', 'Operation aborted. No changes were made.')],
    });
    setTimeout(() => cancel.delete().catch(() => {}), 5000);
    return;
  }

  // ── START RESTORATION ─────────────────────────────────────────────────────
  console.log(`[BACKUP] Starting restore of ${backupId} in ${guild.name}`);
  let statusMsg;
  try {
    statusMsg = await message.channel.send({
      embeds: [infoEmbed('Restoring Backup', `\`\`\`\nBackup ID: ${backupId}\nPhase: Deleting channels...\n\`\`\``)],
    });
  } catch { /* channel may be deleted */ }

  // Phase 1: Delete all channels (rate-limited)
  const allChannels = [...guild.channels.cache.values()];
  for (const ch of allChannels) {
    await ch.delete('Backup restore: wiping server layout').catch(() => {});
    await sleep(500);
  }

  await sleep(1000);

  // Phase 2: Delete non-essential roles (skip @everyone and bot-managed)
  const allRoles = [...guild.roles.cache.values()]
    .filter(r => r.id !== guild.id && !r.managed)
    .sort((a, b) => b.position - a.position); // Delete top-down

  for (const role of allRoles) {
    await role.delete('Backup restore: wiping roles').catch(() => {});
    await sleep(500);
  }

  await sleep(1000);

  // Phase 3: Recreate roles (bottom-up)
  const roleMap = new Map(); // name → Role object
  for (const savedRole of backup.roles) {
    try {
      const role = await guild.roles.create({
        name: savedRole.name,
        color: savedRole.color,
        hoist: savedRole.hoist,
        mentionable: savedRole.mentionable,
        permissions: BigInt(savedRole.permissions),
        reason: `Backup restore: ${backupId}`,
      });
      roleMap.set(savedRole.name, role);
      await sleep(600);
    } catch (err) {
      console.error(`[BACKUP] Failed to create role "${savedRole.name}":`, err.message);
    }
  }

  await sleep(1000);

  // Phase 4: Recreate categories
  const categoryMap = new Map(); // name → Channel object
  for (const cat of backup.categories) {
    try {
      const overwrites = buildOverwrites(cat.permissionOverwrites, roleMap);
      const newCat = await guild.channels.create({
        name: cat.name,
        type: ChannelType.GuildCategory,
        permissionOverwrites: overwrites,
        reason: `Backup restore: ${backupId}`,
      });
      categoryMap.set(cat.name, newCat);
      await sleep(600);
    } catch (err) {
      console.error(`[BACKUP] Failed to create category "${cat.name}":`, err.message);
    }
  }

  await sleep(1000);

  // Phase 5: Recreate channels
  let restoredChannel = null;
  for (const savedCh of backup.channels) {
    try {
      const parent = savedCh.parentName ? categoryMap.get(savedCh.parentName) : null;
      const overwrites = buildOverwrites(savedCh.permissionOverwrites, roleMap);

      const options = {
        name: savedCh.name,
        type: savedCh.type,
        parent: parent?.id ?? null,
        permissionOverwrites: overwrites,
        reason: `Backup restore: ${backupId}`,
      };

      if (savedCh.type === ChannelType.GuildText || savedCh.type === ChannelType.GuildAnnouncement) {
        if (savedCh.topic) options.topic = savedCh.topic;
        options.nsfw = savedCh.nsfw ?? false;
        options.rateLimitPerUser = savedCh.rateLimitPerUser ?? 0;
      }
      if (savedCh.type === ChannelType.GuildVoice) {
        options.bitrate = savedCh.bitrate ?? 64000;
        options.userLimit = savedCh.userLimit ?? 0;
      }

      const newCh = await guild.channels.create(options);
      if (!restoredChannel && newCh.isTextBased()) {
        restoredChannel = newCh;
      }
      await sleep(600);
    } catch (err) {
      console.error(`[BACKUP] Failed to create channel "${savedCh.name}":`, err.message);
    }
  }

  console.log(`[BACKUP] Restore of ${backupId} complete in ${guild.name}`);

  // Send completion message to the first available text channel
  if (restoredChannel) {
    await restoredChannel.send({
      embeds: [
        successEmbed('Backup Restored', `Server layout has been restored from backup \`${backupId}\`.`, [
          { name: '◈ BACKUP ID', value: `\`${backupId}\``, inline: true },
          { name: '◈ ORIGIN', value: `\`${backup.guildName}\``, inline: true },
          { name: '◈ ROLES', value: `\`${roleMap.size}/${backup.roles.length} restored\``, inline: true },
          { name: '◈ CATEGORIES', value: `\`${categoryMap.size}/${backup.categories.length} restored\``, inline: true },
          { name: '◈ RESTORED BY', value: `<@${message.author.id}>` },
        ]),
      ],
    }).catch(() => {});
  }
}

// ── BACKUP LIST ───────────────────────────────────────────────────────────────
async function handleList(message) {
  const backups = listBackups();
  const ids = Object.keys(backups);

  if (!ids.length) {
    return message.reply({ embeds: [infoEmbed('Backups', 'No backups found in the database.')] });
  }

  const lines = ids.map(id => {
    const b = backups[id];
    const date = new Date(b.createdAt).toISOString().slice(0, 10);
    return `  ${id}  |  ${b.guildName.slice(0, 20).padEnd(20)}  |  ${date}  |  ${b.roles.length}R ${b.channels.length}C`;
  });

  return message.reply({
    embeds: [
      infoEmbed('Backup Registry', `\`\`\`\n  ID      | Server               | Date       | Size\n${'-'.repeat(62)}\n${lines.join('\n')}\n\`\`\``, [
        { name: '◈ TOTAL', value: `\`${ids.length} backup(s)\`` },
        { name: '◈ RESTORE', value: '`+backup load <ID>`' },
      ]),
    ],
  });
}

// ── Main command ──────────────────────────────────────────────────────────────
export default {
  name: 'backup',
  description: 'Server backup system — create, load, or list backups',
  usage: '+backup <create|load <ID>|list>',

  async execute(client, message, args) {
    const sub = args[0]?.toLowerCase();

    if (sub === 'create') return handleCreate(message);
    if (sub === 'load') {
      const id = args[1]?.toUpperCase();
      if (!id) return message.reply({ embeds: [errorEmbed('Missing ID', 'Usage: +backup load <ID>')] });
      return handleLoad(message, id);
    }
    if (sub === 'list') return handleList(message);

    return message.reply({
      embeds: [errorEmbed('Invalid Subcommand', 'Usage: +backup <create|load <ID>|list>')],
    });
  },
};
