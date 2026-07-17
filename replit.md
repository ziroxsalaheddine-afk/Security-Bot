# Guardian Bot

A feature-rich Discord bot (Guardian v2) with a companion TypeScript API server.

## Project structure

```
artifacts/
  discord-bot/   Python bot (discord.py + wavelink)
  api-server/    Node.js/TypeScript REST API (Express + esbuild)
lib/
  api-spec/      OpenAPI spec + orval config
  api-zod/       Zod schemas generated from the spec
  api-client-react/  React query hooks generated from the spec
```

## Running the project

Two workflows run in parallel:

| Workflow | Command | Notes |
|---|---|---|
| Discord Bot | `cd artifacts/discord-bot && python main.py` | Requires `DISCORD_TOKEN` secret |
| Start application | `pnpm --filter @workspace/api-server run dev` | Builds then starts on `PORT` (default 433) |

## Required secrets

| Key | Where to get it |
|---|---|
| `DISCORD_TOKEN` | https://discord.com/developers/applications → Bot → Token |

## Environment variables (pre-configured)

| Key | Value | Purpose |
|---|---|---|
| `PORT` | `433` | API server listen port |
| `LAVALINK_URI` | `http://lavalinkv4.serenetia.com:80` | Primary Lavalink music node |
| `LAVALINK_PASSWORD` | (set) | Primary Lavalink auth |

## Bot features

- **Moderation** — admin controls, auto-mod, event logging
- **Security** — anti-nuke, anti-raid, warden
- **Music** — Lavalink v4 (wavelink) with fallback public nodes
- **Utility** — aliases, server cloning, backups, voice, reactions, user search

## Installing dependencies

```bash
# Python (discord bot)
pip install -r artifacts/discord-bot/requirements.txt

# Node.js (API server + libs)
pnpm install
```

## User preferences

- Keep the project's existing structure and stack.
