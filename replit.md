# Guardian Bot — Monorepo

A polyglot monorepo with two independent services: a Discord security/music bot (Python) and a supporting REST API server (Node.js/TypeScript).

## Services

### Discord Bot (`artifacts/discord-bot/`)
- **Stack:** Python 3.11, discord.py 2.7.1, Wavelink 3.5.2 (Lavalink v4), Flask (keep-alive)
- **Run:** `cd artifacts/discord-bot && python main.py`
- **Workflow:** `Discord Bot`
- **Token:** `DISCORD_TOKEN` secret (supports `TOKEN` as alias)
- Music runs through public community Lavalink nodes (fallback list in `main.py`). Override with `LAVALINK_URI` / `LAVALINK_PASSWORD` env vars.

### API Server (`artifacts/api-server/`)
- **Stack:** Node.js 20, Express 5, TypeScript, Drizzle ORM, PostgreSQL 16, pino logging
- **Run:** `pnpm --filter @workspace/api-server run dev` (builds then starts on port 5000)
- **Workflow:** `Start application`
- **Database:** Replit's built-in PostgreSQL — `DATABASE_URL` is set automatically

## Shared Libraries (`lib/`)
- `@workspace/db` — Drizzle ORM client + schema
- `@workspace/api-zod` — Zod schemas for API validation
- `@workspace/api-spec` — API spec definitions

## Running the Project
Both services start together via the **Project** workflow (run button). Or start them individually with their respective workflows above.

## Dependencies
- **JS:** `pnpm install` from repo root (pnpm workspaces)
- **Python:** `pip install -r artifacts/discord-bot/requirements.txt`

## Environment Variables
| Key | Required | Description |
|-----|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Discord bot token |
| `DATABASE_URL` | ✅ | Set automatically by Replit |
| `LAVALINK_URI` | optional | Custom Lavalink node URI |
| `LAVALINK_PASSWORD` | optional | Custom Lavalink node password |
| `PORT` | auto | Set to `5000` in `.replit` |

## User Preferences
<!-- Agent: add remembered preferences here -->
