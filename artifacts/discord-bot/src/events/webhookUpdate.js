import { AuditLogEvent } from 'discord.js';
import { getDB, isWhitelisted } from '../utils/database.js';
import { automodEmbed } from '../utils/embeds.js';

async function sendLog(guild, embed) {
  try {
    const db = getDB();
    const ch = guild.channels.cache.get(db.logs?.channelId);
    if (ch?.isTextBased()) ch.send({ embeds: [embed] }).catch(() => {});
  } catch { /* non-critical */ }
}

export default {
  name: 'webhookUpdate',

  async execute(client, channel) {
    const db = getDB();
    const webhookCfg = db.anti?.webhook;
    if (!webhookCfg?.enabled) return;

    const guild = channel.guild;
    if (!guild) return;

    // Brief delay so the audit log has time to record the creation
    await new Promise(r => setTimeout(r, 1500));

    // Step 1: Fetch current webhooks in the channel
    let currentWebhooks;
    try {
      currentWebhooks = await channel.fetchWebhooks();
    } catch {
      return; // Can't read webhooks — nothing to act on
    }

    if (currentWebhooks.size === 0) return; // No webhooks to investigate

    // Step 2: Fetch recent WebhookCreate audit entries
    let auditEntries;
    try {
      const logs = await guild.fetchAuditLogs({ limit: 5, type: AuditLogEvent.WebhookCreate });
      auditEntries = logs.entries;
    } catch (err) {
      console.error('[ANTI-WEBHOOK] Audit log fetch failed:', err.message);
      return;
    }

    const now = Date.now();

    // Step 3: For each existing webhook in this channel, find a matching
    // audit entry by webhook ID (targetId). This prevents cross-channel
    // or unrelated attribution errors.
    for (const webhook of currentWebhooks.values()) {
      // Only inspect webhooks created in the last 15 seconds
      if (now - webhook.createdTimestamp > 15_000) continue;

      // Find the audit log entry whose targetId matches this specific webhook
      const entry = auditEntries.find(
        e =>
          e.targetId === webhook.id &&
          now - e.createdTimestamp <= 15_000,
      );
      if (!entry?.executor) continue;

      const executor = entry.executor;
      if (executor.bot) continue;

      // Skip owners, co-owners, and whitelisted users
      if (db.owners.includes(executor.id)) continue;
      if ((db.coowners ?? []).includes(executor.id)) continue;
      if (isWhitelisted(executor.id)) continue;

      console.log(
        `[ANTI-WEBHOOK] Unauthorized webhook "${webhook.name}" created by ${executor.tag} in #${channel.name}`,
      );

      // Step 4: Delete the specific unauthorized webhook
      await webhook.delete('Anti-Webhook: Unauthorized webhook creation').catch(err =>
        console.error('[ANTI-WEBHOOK] Could not delete webhook:', err.message),
      );

      const embed = automodEmbed(
        'ANTI-WEBHOOK',
        executor.id,
        `Webhook "${webhook.name}" created in #${channel.name} — deleted`,
      );
      sendLog(guild, embed);

      // Step 5: Apply configured action to the executor
      const member = await guild.members.fetch(executor.id).catch(() => null);
      if (!member) continue;

      const action = webhookCfg.action ?? 'delete';
      if (action === 'timeout') {
        await member.timeout(300_000, 'Anti-Webhook: Unauthorized webhook creation').catch(() => {});
      } else if (action === 'kick') {
        await member.kick('Anti-Webhook: Unauthorized webhook creation').catch(() => {});
      } else if (action === 'ban') {
        await member.ban({ reason: 'Anti-Webhook: Unauthorized webhook creation' }).catch(() => {});
      }
      // "delete" = webhook deleted above, no further user action
    }
  },
};
