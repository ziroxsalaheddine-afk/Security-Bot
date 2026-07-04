import { getDB, isBlacklisted, addQuarantine, getQuarantineRole } from '../utils/database.js';
import { altAlertEmbed, punishEmbed, automodEmbed } from '../utils/embeds.js';

const MS_PER_DAY = 86_400_000;

async function sendLog(guild, embed) {
  try {
    const db = getDB();
    const ch = guild.channels.cache.get(db.logs?.channelId);
    if (ch?.isTextBased()) ch.send({ embeds: [embed] }).catch(() => {});
  } catch { /* non-critical */ }
}

export default {
  name: 'guildMemberAdd',

  async execute(client, member) {
    const db = getDB();

    // ── Anti-Bot: kick/ban bots that join without authorization ──────────────
    if (member.user.bot) {
      const botCfg = db.anti?.bot;
      if (botCfg?.enabled) {
        const embed = automodEmbed('ANTI-BOT', member.id, `Bot joined: ${member.user.tag}`);
        sendLog(member.guild, embed);

        if (botCfg.action === 'ban') {
          await member.ban({ reason: 'Anti-Bot: Unauthorized bot join.' }).catch(err =>
            console.error('[ANTI-BOT] Ban failed:', err.message));
        } else {
          // Default: kick
          await member.kick('Anti-Bot: Unauthorized bot join.').catch(err =>
            console.error('[ANTI-BOT] Kick failed:', err.message));
        }
      }
      return;
    }

    // ── Blacklist check: immediately ban ─────────────────────────────────────
    if (isBlacklisted(member.id)) {
      try {
        await member.ban({ reason: 'User is on the server blacklist.' });
        const embed = punishEmbed('BAN', member.id, 'User is on the server blacklist.');
        await sendLog(member.guild, embed);
      } catch (err) {
        console.error('[BLACKLIST] Could not ban:', err.message);
      }
      return;
    }

    // ── Alt-Account / Raid Protection ────────────────────────────────────────
    const altCfg = db.config.altProtection;
    if (!altCfg?.enabled) return;

    const accountAgeMs = Date.now() - member.user.createdTimestamp;
    const minAgeMs = altCfg.minAccountAge * MS_PER_DAY;
    if (accountAgeMs >= minAgeMs) return;

    const ageHours = accountAgeMs / 3_600_000;
    const embed = altAlertEmbed(member, ageHours);
    await sendLog(member.guild, embed);

    if (altCfg.action === 'quarantine') {
      const quarantineRoleId = getQuarantineRole();
      if (!quarantineRoleId) {
        await member.kick('Alt-Account Protection: No quarantine role configured.').catch(() => {});
        return;
      }
      try {
        await member.roles.set([quarantineRoleId], 'Alt-Account Protection: Account too new.');
        addQuarantine(member.id, { reason: 'Alt-account protection', guild: member.guild.id });
      } catch (err) {
        console.error('[ALT-PROTECT] Could not quarantine:', err.message);
      }
    } else if (altCfg.action === 'ban') {
      await member.ban({ reason: 'Alt-Account Protection: Account too new.' }).catch(err =>
        console.error('[ALT-PROTECT] Could not ban:', err.message));
    } else {
      // Default: kick
      await member.kick(
        `Alt-Account Protection: Account is ${(ageHours / 24).toFixed(1)} days old.`,
      ).catch(err => console.error('[ALT-PROTECT] Could not kick:', err.message));
    }
  },
};
