import { Client, GatewayIntentBits, Partials } from 'discord.js';
import { loadCommands } from './handlers/commandHandler.js';
import { loadEvents } from './handlers/eventHandler.js';
import { loadDB } from './utils/database.js';

// ── Boot banner ───────────────────────────────────────────────────────────────
console.log('');
console.log('  ╔══════════════════════════════════════╗');
console.log('  ║     G U A R D I A N   B O T          ║');
console.log('  ║     Discord Security System v1.0     ║');
console.log('  ╚══════════════════════════════════════╝');
console.log('');

// ── Validate token ────────────────────────────────────────────────────────────
if (!process.env.DISCORD_TOKEN) {
  console.error('[FATAL] DISCORD_TOKEN is not set. Exiting.');
  process.exit(1);
}

// ── Load database ─────────────────────────────────────────────────────────────
try {
  loadDB();
  console.log('[DB] database.json loaded successfully.');
} catch (err) {
  console.error('[FATAL] Failed to load database:', err.message);
  process.exit(1);
}

// ── Discord client ────────────────────────────────────────────────────────────
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildBans,
    GatewayIntentBits.GuildModeration,
    GatewayIntentBits.GuildWebhooks,
  ],
  partials: [Partials.Message, Partials.Channel, Partials.GuildMember],
});

// ── In-memory state (shared via client) ──────────────────────────────────────
// Anti-nuke: Map<userId, { count, actions[], lastReset }>
client.nukeTracker = new Map();

// Anti-spam: Map<userId, { count, firstMessage }>
client.spamTracker = new Map();

// Load handlers
await loadCommands(client);
await loadEvents(client);

// ── Login ─────────────────────────────────────────────────────────────────────
client.login(process.env.DISCORD_TOKEN).catch(err => {
  console.error('[FATAL] Login failed:', err.message);
  process.exit(1);
});
