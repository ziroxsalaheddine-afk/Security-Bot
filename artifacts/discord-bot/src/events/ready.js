export default {
  name: 'clientReady',
  once: true,

  async execute(client) {
    console.log('');
    console.log(`  [ONLINE] Logged in as ${client.user.tag}`);
    console.log(`  [GUILDS] Serving ${client.guilds.cache.size} guild(s)`);
    console.log('');

    client.user.setPresence({
      activities: [{ name: '+help | Watching over you', type: 3 }],
      status: 'dnd',
    });
  },
};
