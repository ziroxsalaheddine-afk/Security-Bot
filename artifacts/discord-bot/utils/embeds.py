import discord
from datetime import datetime, timezone

DARK = discord.Color(0x0D0D0D)
SUCCESS = discord.Color(0x1A5C2A)
DANGER = discord.Color(0xC0392B)
SILVER = discord.Color(0xC0C0C0)
INFO = discord.Color(0x2C3E50)
GOLD = discord.Color(0xF1C40F)


def _base(title: str = "", description: str = "", color: discord.Color = DARK) -> discord.Embed:
    e = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text="Guardian Security System")
    return e


def success(title: str, description: str = "") -> discord.Embed:
    return _base(f"✅  {title}", description, SUCCESS)


def danger(title: str, description: str = "") -> discord.Embed:
    return _base(f"🛡️  {title}", description, DANGER)


def info(title: str, description: str = "") -> discord.Embed:
    return _base(f"ℹ️  {title}", description, INFO)


def stats(
    title: str,
    elapsed_s: float,
    fields: list | None = None,
) -> discord.Embed:
    ms = elapsed_s * 1000
    e = _base(title, color=SILVER)
    e.add_field(
        name="⏱️  Execution Time",
        value=f"`{ms:.2f}ms`  ({elapsed_s:.4f}s)",
        inline=False,
    )
    if fields:
        for name, value, inline in fields:
            e.add_field(name=name, value=value, inline=inline)
    return e
