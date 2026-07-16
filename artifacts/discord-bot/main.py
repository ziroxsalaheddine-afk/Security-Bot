import os
import pkgutil
import asyncio
import logging

import discord
import wavelink
from discord.ext import commands

from utils import db
from utils import alias_db
from utils import gatekeeper
from keep_alive import keep_alive

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("guardian")

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.moderation      = True
intents.guilds          = True
intents.voice_states    = True     # Required for all voice/music operations

# Lavalink v4 node(s) used for music. If LAVALINK_URI / LAVALINK_PASSWORD env
# vars are set they become the "MAIN" node; the entries below are registered
# as additional nodes and wavelink load-balances across all reachable ones.
# secure=false → plain HTTP (port 3000); switch to https:// + 443 if the
# host ever enables TLS.
FALLBACK_LAVALINK_NODES: list[tuple[str, str]] = [
    # Jirayu (SSL)
    ("https://lavalink.jirayu.net:443", "youshallnotpass"),
    # AneFaiz / Millohost (SSL)
    ("https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
]


def _discover_cogs() -> list[str]:
    """Scan the cogs/ package and return the dotted import path of every
    module inside it, so new cogs are picked up automatically without ever
    having to edit this file again. Files starting with "_" (e.g.
    __init__.py) are skipped.
    """
    import cogs as cogs_package

    discovered = []
    for module in pkgutil.iter_modules(cogs_package.__path__):
        if module.name.startswith("_"):
            continue
        discovered.append(f"cogs.{module.name}")
    return sorted(discovered)


def _get_prefix(bot: "Guardian", message: discord.Message) -> str:
    return db.get_prefix()


class Guardian(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=_get_prefix,
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self):
        alias_db.init()
        self.add_check(gatekeeper.check_or_raise)

        for cog in _discover_cogs():
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as e:
                log.error("Failed to load cog %s: %s", cog, e)

        # ── Connect Lavalink v4 node(s) (wavelink 3.x) in the background ─────
        # Each node gets its own independent task.
        #
        # Why: Pool.connect blocks the calling coroutine until the WebSocket
        # handshake succeeds (with internal exponential-backoff retries).
        # For an unreachable node that retry loop runs forever, so if we
        # connect all nodes in one Pool.connect call the first unreachable
        # node prevents every subsequent node from being attempted at all.
        # By spawning a separate task per node, reachable FALLBACK nodes
        # come online immediately even when MAIN is temporarily down.
        #
        # Do NOT wrap individual connects in asyncio.wait_for — cancelling
        # Pool.connect mid-flight leaves the node's WebSocket running but
        # Pool.__nodes empty, causing "No nodes assigned" errors at runtime.
        self.loop.create_task(self._connect_lavalink())

    async def _connect_lavalink(self):
        # Ensure Discord is fully connected before touching wavelink internals.
        await self.wait_until_ready()

        lava_uri  = os.environ.get("LAVALINK_URI")
        lava_pass = os.environ.get("LAVALINK_PASSWORD")

        nodes: list[tuple[str, wavelink.Node]] = []

        if lava_uri and lava_pass:
            print(f"[Lavalink] MAIN node → {lava_uri}")
            log.info("Lavalink MAIN node configured: %s", lava_uri)
            nodes.append(("MAIN", wavelink.Node(
                identifier="MAIN",
                uri=lava_uri,
                password=lava_pass,
            )))
        else:
            print("[Lavalink] WARNING: LAVALINK_URI / LAVALINK_PASSWORD not set — no MAIN node.")
            log.warning("LAVALINK_URI or LAVALINK_PASSWORD env var missing — no MAIN node.")

        for i, (uri, password) in enumerate(FALLBACK_LAVALINK_NODES, start=1):
            label = f"FALLBACK-{i}"
            print(f"[Lavalink] {label} node → {uri}")
            nodes.append((label, wavelink.Node(
                identifier=label,
                uri=uri,
                password=password,
            )))

        if not nodes:
            print("[Lavalink] No nodes configured — music commands disabled.")
            log.warning("No Lavalink nodes configured — music commands disabled.")
            return

        print(f"[Lavalink] Spawning {len(nodes)} independent connection task(s)…")
        for label, node in nodes:
            asyncio.create_task(self._connect_single_node(label, node))

    async def _connect_single_node(self, label: str, node: wavelink.Node):
        """Connect a single Lavalink node in its own task.

        Pool.connect blocks (with internal retries) until the WebSocket
        handshake succeeds, so running each node here in isolation means
        a temporarily unreachable node cannot block any other node.
        """
        print(f"[Lavalink] {label}: connecting…")
        try:
            await wavelink.Pool.connect(nodes=[node], client=self, cache_capacity=100)
            # on_wavelink_node_ready fires separately once the WS handshake
            # completes; Pool.connect returning means the node is registered.
            print(f"[Lavalink] {label}: registered in Pool — waiting for WS ready event.")
            log.info("Lavalink %s registered in Pool.", label)
        except wavelink.AuthorizationFailedException as exc:
            print(f"[Lavalink] {label} ❌ auth failed — check password. ({exc})")
            log.error("Lavalink %s auth failed: %s", label, exc)
        except wavelink.NodeException as exc:
            print(f"[Lavalink] {label} ❌ node error — is Lavalink v4 running? ({exc})")
            log.error("Lavalink %s node error: %s", label, exc)
        except Exception as exc:
            print(f"[Lavalink] {label} ❌ {type(exc).__name__}: {exc}")
            log.error("Lavalink %s unexpected error: %s", label, exc)

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        print(
            f"[Lavalink] ✅ Node connected successfully! "
            f"identifier='{payload.node.identifier}'  "
            f"session='{payload.session_id}'  resumed={payload.resumed}"
        )
        log.info(
            "Lavalink node '%s' ready (session: %s, resumed: %s)",
            payload.node.identifier, payload.session_id, payload.resumed,
        )

    async def on_ready(self):
        print()
        print("  ╔══════════════════════════════════════╗")
        print("  ║     G U A R D I A N   B O T   v2      ║")
        print("  ║     Python Security System            ║")
        print("  ╚══════════════════════════════════════╝")
        print()
        print(f"  [ONLINE] {self.user} (ID: {self.user.id})")
        print(f"  [GUILDS] Serving {len(self.guilds)} guild(s)")
        print()

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Rewrite `+<alias>` into its target command before dispatch, so
        # server & personal aliases behave exactly like the real command.
        resolved = alias_db.resolve(
            db.get_prefix(),
            message.content,
            message.author.id,
            message.guild.id if message.guild else None,
        )
        if resolved is not None:
            message.content = resolved

        await self.process_commands(message)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        # Any check failure (gatekeeper denial, guild_only, missing perms from
        # another decorator, etc.) surfaces the same "You Cannot Use This Bot!"
        # embed — the bot must never go silent when a user lacks permission.
        if isinstance(error, commands.CheckFailure):
            try:
                await ctx.send(embed=gatekeeper.denial_embed(self))
            except discord.HTTPException:
                pass
            return
        if isinstance(error, commands.CommandOnCooldown):
            # Single source of truth for the cooldown embed — every heavy
            # command's @commands.cooldown(...) funnels here instead of each
            # cog rolling its own message.
            seconds = max(1, round(error.retry_after))
            try:
                await ctx.send(
                    embed=discord.Embed(
                        title="Cooldown Notice",
                        description=f"• __Patience please... just **{seconds}** seconds left, Huh__ ⁘",
                        color=0xC8B6FF,
                    ),
                    delete_after=min(seconds, 10),
                )
            except discord.HTTPException:
                pass
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            try:
                await ctx.send(
                    embed=discord.Embed(
                        description=f"❌ Missing argument: `{error.param.name}`",
                        color=0xC0392B,
                    ),
                    delete_after=5,
                )
            except discord.HTTPException:
                pass
        else:
            log.error("Command error in %s: %s", ctx.command, error)


def _get_token() -> str:
    """Read the bot token from the environment. Supports both TOKEN (common
    on external hosting platforms like Railway/Render/Heroku) and
    DISCORD_TOKEN (used by this project's Replit secrets), so the exact same
    file works unmodified in either environment.
    """
    return os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN") or ""


bot = Guardian()

if __name__ == "__main__":
    token = _get_token()
    if not token:
        log.critical("No bot token found. Set TOKEN or DISCORD_TOKEN in your environment.")
        raise SystemExit(1)

    db.get()
    keep_alive()
    bot.run(token, log_handler=None)
