import discord
from datetime import datetime, timezone

COL     = discord.Color(0x2B2D31)
FOOTER  = "Guardian Security System"


def _base(title: str = "", description: str = "", color: discord.Color = COL) -> discord.Embed:
    e = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text=FOOTER)
    return e


def success(title: str, description: str = "") -> discord.Embed:
    return _base(title, description)


def danger(title: str, description: str = "") -> discord.Embed:
    return _base(title, description)


def info(title: str, description: str = "") -> discord.Embed:
    return _base(title, description)


def stats(title: str, elapsed_s: float, fields: list | None = None) -> discord.Embed:
    ms   = elapsed_s * 1000
    desc = f"• __**Execution Time**__\n`{ms:.2f}ms`  ({elapsed_s:.4f}s)"
    if fields:
        for name, value, _ in fields:
            desc += f"\n\n• __**{name}**__\n{value}"
    return _base(title, desc)
