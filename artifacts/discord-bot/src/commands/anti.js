import { getDB, getAntiConfig, setAntiConfig, saveDB } from '../utils/database.js';
import { successEmbed, errorEmbed, infoEmbed } from '../utils/embeds.js';

const VALID_TYPES = ['link', 'invite', 'webhook', 'bot'];
const VALID_ACTIONS = ['delete', 'timeout', 'kick', 'ban'];

// Some types don't support timeout (bots/webhooks can't be timed out in meaningful ways)
const TYPE_VALID_ACTIONS = {
  link: ['delete', 'timeout', 'kick', 'ban'],
  invite: ['delete', 'timeout', 'kick', 'ban'],
  webhook: ['delete', 'timeout', 'kick', 'ban'],
  bot: ['kick', 'ban'],
};

function antiStatusEmbed(db) {
  const anti = db.anti ?? {};
  const rows = VALID_TYPES.map(type => {
    const cfg = anti[type] ?? { enabled: false, action: 'delete' };
    const status = cfg.enabled ? '\u001b[0;32mON \u001b[0m' : '\u001b[0;31mOFF\u001b[0m';
    return `  ${type.padEnd(10)} [ ${status} ]  action: ${cfg.action ?? 'delete'}`;
  }).join('\n');

  return infoEmbed('Anti Config', `\`\`\`ansi\n${rows}\n\`\`\``, [
    { name: '◈ USAGE', value: '`+anti <type> <on|off|action>`', inline: true },
    { name: '◈ TYPES', value: `\`${VALID_TYPES.join(' | ')}\``, inline: true },
    { name: '◈ ACTIONS', value: `\`${VALID_ACTIONS.join(' | ')}\`` },
  ]);
}

export default {
  name: 'anti',
  description: 'Configure anti-system actions (link, invite, webhook, bot)',
  usage: '+anti <type> <on|off|action>  — e.g. +anti link ban  |  +anti bot kick',

  async execute(client, message, args) {
    const db = getDB();
    const type = args[0]?.toLowerCase();
    const param = args[1]?.toLowerCase();

    // ── Status overview ───────────────────────────────────────────────────────
    if (!type || !VALID_TYPES.includes(type)) {
      return message.reply({ embeds: [antiStatusEmbed(db)] });
    }

    // ── Toggle on/off ─────────────────────────────────────────────────────────
    if (param === 'on' || param === 'off') {
      setAntiConfig(type, 'enabled', param === 'on');
      return message.reply({
        embeds: [
          successEmbed(
            `Anti-${type} ${param.toUpperCase()}`,
            `Anti-${type} protection is now ${param === 'on' ? 'ACTIVE' : 'INACTIVE'}.`,
            [{ name: '◈ MODULE', value: `\`anti-${type}\``, inline: true },
             { name: '◈ STATUS', value: `\`${param.toUpperCase()}\``, inline: true }],
          ),
        ],
      });
    }

    // ── Set action ────────────────────────────────────────────────────────────
    if (param && VALID_ACTIONS.includes(param)) {
      const allowed = TYPE_VALID_ACTIONS[type];
      if (!allowed.includes(param)) {
        return message.reply({
          embeds: [
            errorEmbed(
              'Invalid Action',
              `Anti-${type} supports: \`${allowed.join(' | ')}\`\n` +
              `\`timeout\` and \`delete\` are not applicable to \`${type}\`.`,
            ),
          ],
        });
      }

      setAntiConfig(type, 'action', param);
      // Enable the module if setting an action on it
      setAntiConfig(type, 'enabled', true);

      return message.reply({
        embeds: [
          successEmbed(`Anti-${type} Updated`, `Action set to \`${param.toUpperCase()}\`.`, [
            { name: '◈ MODULE', value: `\`anti-${type}\``, inline: true },
            { name: '◈ ACTION', value: `\`${param.toUpperCase()}\``, inline: true },
            { name: '◈ STATUS', value: '`ACTIVE`', inline: true },
            {
              name: '◈ EFFECT',
              value: actionDescription(type, param),
            },
          ]),
        ],
      });
    }

    return message.reply({ embeds: [antiStatusEmbed(db)] });
  },
};

function actionDescription(type, action) {
  const descriptions = {
    link: {
      delete: '`Message deleted — no further punishment`',
      timeout: '`Message deleted + user timed out for 5 minutes`',
      kick: '`Message deleted + user kicked from server`',
      ban: '`Message deleted + user permanently banned`',
    },
    invite: {
      delete: '`Invite message deleted — no further punishment`',
      timeout: '`Invite deleted + user timed out for 5 minutes`',
      kick: '`Invite deleted + user kicked from server`',
      ban: '`Invite deleted + user permanently banned`',
    },
    webhook: {
      delete: '`Unauthorized webhook deleted`',
      timeout: '`Webhook deleted + executor timed out for 5 minutes`',
      kick: '`Webhook deleted + executor kicked from server`',
      ban: '`Webhook deleted + executor permanently banned`',
    },
    bot: {
      kick: '`Bot kicked immediately on join`',
      ban: '`Bot permanently banned on join`',
    },
  };
  return descriptions[type]?.[action] ?? `\`${action.toUpperCase()}\``;
}
