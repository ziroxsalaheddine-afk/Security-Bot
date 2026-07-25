"use strict";

/**
 * ready — fires once after the bot connects to Discord.
 *
 * Populates the role cache for every guild the bot is in so that
 * roleDelete can immediately find member lists even if the bot just restarted.
 */

module.exports = {
  name: "ready",
  once: true,

  async execute(client) {
    console.log(
      `[AntiNuke] Logged in as ${client.user.tag} — serving ${client.guilds.cache.size} guild(s)`
    );
    console.log("[AntiNuke] Populating role cache on startup...");

    let totalMembers = 0;
    let totalGuilds = 0;

    for (const [, guild] of client.guilds.cache) {
      try {
        // Fetch ALL guild members into the cache (requires GuildMembers intent +
        // "Server Members Intent" enabled in the developer portal)
        const members = await guild.members.fetch();

        for (const [, member] of members) {
          // Exclude @everyone (= guild.id) and Discord-managed roles (e.g. bot integrations)
          const roleIds = member.roles.cache
            .filter((r) => r.id !== guild.id && !r.managed)
            .map((r) => r.id);

          client.db.syncMember(guild.id, member.id, roleIds);
        }

        totalMembers += members.size;
        totalGuilds++;
        console.log(
          `[AntiNuke]   Cached ${members.size} member(s) in "${guild.name}"`
        );
      } catch (err) {
        console.error(
          `[AntiNuke] Failed to cache guild "${guild.name}": ${err.message}`
        );
      }
    }

    console.log(
      `[AntiNuke] Startup cache complete — ${totalMembers} member(s) across ${totalGuilds} guild(s).`
    );
  },
};
