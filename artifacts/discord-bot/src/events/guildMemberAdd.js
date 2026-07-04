import { getDB, isBlacklisted, addQuarantine, getQuarantineRole } from '../utils/database.js';
import { altAlertEmbed, punishEmbed } from '../utils/embeds.js';

const MS_PER_DAY = 86_400_000;

async function sendLog(guild, embed) {
  const db = getDB();
  const channelId = db.logs?.channelId;
  if (!channelId) return;
  const ch = guild.channels.cache.get(channelId);
  if (ch?.isTextBased()) ch.send({ embeds: [embed] }).catch(() => {});
}

export default {
  name: 'guildMemberAdd',

  async execute(client, member) {
    const db = getDB();

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

    if (altCfg.action === 'kick') {
      try {
        await member.kick(
          `Alt-Account Protection: Account is only ${(ageHours / 24).toFixed(1)} days old.`,
        );
        console.log(`[ALT-PROTECT] Kicked ${member.user.tag} — account age: ${ageHours.toFixed(1)}h`);
      } catch (err) {
        console.error('[ALT-PROTECT] Could not kick:', err.message);
      }
    } else if (altCfg.action === 'quarantine') {
      const quarantineRoleId = getQuarantineRole();
      if (!quarantineRoleId) {
        // Fallback to kick if no quarantine role set
        await member.kick('Alt-Account Protection: No quarantine role configured.').catch(() => {});
        return;
      }
      try {
        await member.roles.set([quarantineRoleId], 'Alt-Account Protection: Account too new.');
        addQuarantine(member.id, { reason: 'Alt-account protection', guild: member.guild.id });
        console.log(`[ALT-PROTECT] Quarantined ${member.user.tag}`);
      } catch (err) {
        console.error('[ALT-PROTECT] Could not quarantine:', err.message);
      }
    } else if (altCfg.action === 'ban') {
      try {
        await member.ban({ reason: 'Alt-Account Protection: Account too new.' });
      } catch (err) {
        console.error('[ALT-PROTECT] Could not ban:', err.message);
      }
    }
  },
};
