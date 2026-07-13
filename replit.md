# Guardian Bot

A comprehensive Discord security bot with Anti-Nuke, Advanced Automod, and Raid/Alt-Account Protection. Prefix: `+`

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string
- Discord bot: `cd artifacts/discord-bot && python main.py` (workflow: "Discord Bot"). Python deps installed via uv (discord.py, aiohttp, flask, wavelink, PyNaCl). Requires `DISCORD_TOKEN` and `DISCORD_BOT_OWNER_ID` secrets — both are set.
- Both workflows verified running: API server responds on `/api/healthz`, Discord bot connects and shows online in its 3 guilds.

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

_Populate as you build — short repo map plus pointers to the source-of-truth file for DB schema, API contracts, theme files, etc._

## Architecture decisions

_Populate as you build — non-obvious choices a reader couldn't infer from the code (3-5 bullets)._

## Product

_Describe the high-level user-facing capabilities of this app once they exist._

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- `wavelink.Pool.connect()`'s internal websocket loop retries an unreachable Lavalink node forever without raising. Since it's awaited in the bot's `setup_hook`, an unreachable node used to hang the bot before it ever reached the Discord gateway (no error, no crash — just silent). `main.py` now wraps that call in `asyncio.wait_for(..., timeout=15)` so a dead node can't block startup; music stays disabled until a node is reachable, but the bot still comes online.
- The default `LAVALINK_URI`/`LAVALINK_PASSWORD` shared env vars point to a public node that is currently unreachable from this environment — expect Lavalink connection warnings in the Discord Bot log; they're harmless given the fix above, but if music commands are needed, swap in a reachable node from https://lavalink-list.darrennathanael.com.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
