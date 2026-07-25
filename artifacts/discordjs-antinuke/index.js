"use strict";

/**
 * Discord.js v14 Anti-Nuke Bot — Role Deletion Protection
 *
 * Environment variables:
 *   ANTINUKE_TOKEN  — Bot token (required). Create a separate bot account at
 *                     https://discord.com/developers/applications and set this
 *                     as a Replit Secret named ANTINUKE_TOKEN.
 */

const { Client, GatewayIntentBits } = require("discord.js");
const path = require("path");
const fs = require("fs");

const TOKEN = process.env.ANTINUKE_TOKEN;
if (!TOKEN) {
  console.error(
    "[AntiNuke] ANTINUKE_TOKEN is not set.\n" +
      "Create a bot at https://discord.com/developers/applications, copy its token,\n" +
      'and add it as a Replit Secret named "ANTINUKE_TOKEN".'
  );
  process.exit(1);
}

// ── Database ──────────────────────────────────────────────────────────────────
const db = require("./src/database");

// ── Client ────────────────────────────────────────────────────────────────────
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,       // guild/role events
    GatewayIntentBits.GuildMembers, // member cache + guildMemberUpdate
  ],
});

// Attach db to client so every event handler can reach it without re-importing
client.db = db;

// ── Event loader ─────────────────────────────────────────────────────────────
const eventsPath = path.join(__dirname, "src", "events");
const eventFiles = fs
  .readdirSync(eventsPath)
  .filter((f) => f.endsWith(".js"))
  .sort(); // deterministic load order

for (const file of eventFiles) {
  const event = require(path.join(eventsPath, file));
  const register = event.once
    ? client.once.bind(client)
    : client.on.bind(client);
  register(event.name, (...args) => event.execute(client, ...args));
  console.log(`[AntiNuke] Registered event: ${event.name} (${file})`);
}

// ── Login ─────────────────────────────────────────────────────────────────────
client.login(TOKEN).catch((err) => {
  console.error("[AntiNuke] Login failed:", err.message);
  process.exit(1);
});
