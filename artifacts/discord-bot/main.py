import os
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
intents.members        = True
intents.message_content = True
intents.moderation     = True
intents.guilds         = True
intents.voice_states   = True      # Required for all voice/music operations

# Public Lavalink v4 (SSL) fallback nodes: (uri, password). Sourced from the
# community-maintained list at https://lavalink.darrennathanael.com/SSL —
# these are free, volunteer-hosted nodes, so they occasionally go down or
# rotate credentials. If music stops working and the logs show ALL nodes
# failing to connect, open that page, grab fresh Host/Port/Password values,
# and replace the entries below (keep the "https://host:port" format).
# wavelink.Pool connects to every node in this list and load-balances across
# whichever ones are actually reachable, so one dead node no longer takes
# music offline entirely.
FALLBACK_LAVALINK_NODES: list[tuple[str, str]] = [
    # Amane & AjieDev
    ("https://lavalinkv4.serenetia.com:443", "https://seretia.link/discord"),
    # Jirayu
    ("https://lavalink.jirayu.net:443", "youshallnotpass"),
    # AneFaiz / Millohost
    ("https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
]

COGS = [
    "cogs.antinuke",
    "cogs.clone",
    "cogs.antiraid",
    "cogs.automod",
    "cogs.admin",
    "cogs.help",
    "cogs.setup",
    "cogs.voice",
    "cogs.owner",
    "cogs.backup",
    "cogs.warden",
    "cogs.music",
    "cogs.dj",
    "cogs.alias",
    "cogs.reactions",
    "cogs.eventlog",
    "cogs.information",
]


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

        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as e:
                log.error("Failed to load cog %s: %s", cog, e)

        # ── Connect Lavalink v4 node(s) (wavelink 3.x) ────────────────────────
        # A single public node going down (e.g. lavalinkv4.serenetia.com
        # timing out) used to take music offline entirely. We now connect to
        # a *pool* of nodes — wavelink.Pool automatically routes new players
        # to whichever node is healthy, so one dead node no longer breaks
        # +play for everyone. LAVALINK_URI/LAVALINK_PASSWORD (env) is always
        # tried first if set, so self-hosted/private nodes still take
        # priority; the public fallbacks below only fill in the gaps.
        lava_uri  = os.environ.get("LAVALINK_URI")
        lava_pass = os.environ.get("LAVALINK_PASSWORD")

        nodes: list[wavelink.Node] = []
        if lava_uri and lava_pass:
            nodes.append(wavelink.Node(
                identifier="MAIN",
                uri=lava_uri,
                password=lava_pass,
            ))

        # Public Lavalink v4 nodes (free, community-hosted — see
        # https://lavalink-list.darrennathanael.com for a maintained list).
        # These rotate/die periodically; if music stops working, swap in
        # fresh hosts from that list. Each entry needs a `https://` or
        # `http://` scheme, a port, and its own password.
        for i, (uri, password) in enumerate(FALLBACK_LAVALINK_NODES, start=1):
            nodes.append(wavelink.Node(
                identifier=f"FALLBACK-{i}",
                uri=uri,
                password=password,
            ))

        if nodes:
            try:
                await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)
                log.info("Connecting to %d Lavalink node(s)…", len(nodes))
            except Exception as exc:
                log.error("Failed to initialise Lavalink pool: %s", exc)
        else:
            log.warning(
                "No Lavalink nodes configured — music commands disabled."
            )

    async def on_ready(self):
        print()
        print("  ╔══════════════════════════════════════╗")
        print("  ║     G U A R D I A N   B O T   v2    ║")
        print("  ║     Python Security System           ║")
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
            except discord.Forbidden:
                pass
            return
        if isinstance(error, commands.CommandOnCooldown):
            # Single source of truth for the cooldown embed — every heavy
            # command's @commands.cooldown(...) funnels here instead of each
            # cog rolling its own message.
            seconds = max(1, round(error.retry_after))
            await ctx.send(
                embed=discord.Embed(
                    title="Cooldown Notice",
                    description=f"Patience please... just **{seconds}** seconds left, hhhhh",
                    color=0x8E7CC3,
                ),
                delete_after=min(seconds, 10),
            )
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=discord.Embed(
                    description=f"❌ Missing argument: `{error.param.name}`",
                    color=0xC0392B,
                ),
                delete_after=5,
            )
        else:
            log.error("Command error in %s: %s", ctx.command, error)


bot = Guardian()

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN is not set. Exiting.")
        raise SystemExit(1)
    db.get()
    keep_alive()
    bot.run(token, log_handler=None)
