"""Display filters shared by every server-rendered console template."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from starlette.templating import Jinja2Templates


def format_percent(value: float | None) -> str:
    """Format a proportion as a one-decimal percentage, or a dash."""
    return "-" if value is None else f"{value * 100:.1f}%"


def format_interval(low: float | None, high: float | None) -> str:
    """Format a Wilson interval as two percentages, or a dash."""
    if low is None or high is None:
        return "-"
    return f"{low * 100:.1f}% to {high * 100:.1f}%"


def format_duration(seconds: float | None) -> str:
    """Format a duration in seconds, or a dash when it is unknown."""
    return "-" if seconds is None else f"{seconds:.1f}s"


def format_usd(value: float | None) -> str:
    """Format a USD amount readably, keeping tiny non-zero costs visible.

    Amounts of a cent or more use ordinary currency rounding. Smaller amounts
    keep two significant digits, so a real cost never collapses to $0.00.
    """
    return "-" if value is None else _readable_usd(value)


def _readable_usd(value: float) -> str:
    """Format a present USD amount with an honest number of digits."""
    if value == 0:
        return "$0.00"
    magnitude = abs(value)
    if magnitude >= 0.01:
        return f"${value:,.2f}"
    decimals = 1 - math.floor(math.log10(magnitude))
    return f"${value:.{decimals}f}"


def format_usd_exact(value: float | None) -> str:
    """Format the stored USD value with up to eight decimals for a tooltip."""
    return "-" if value is None else ("$0.00" if value == 0 else f"${value:.8f}")


def format_short_path(value: str | Path) -> str:
    """Show a path relative to the working directory, or its final name."""
    path = Path(value)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name


def format_time(value: datetime | None) -> str:
    """Format a stored timestamp to seconds, or a dash."""
    return "-" if value is None else value.strftime("%Y-%m-%d %H:%M:%S")


def install_filters(templates: Jinja2Templates) -> None:
    """Register the display filters the console templates use."""
    templates.env.filters.update(
        {
            "pct": format_percent,
            "interval": format_interval,
            "duration": format_duration,
            "usd": format_usd,
            "usdexact": format_usd_exact,
            "shortpath": format_short_path,
            "timestamp": format_time,
        }
    )
