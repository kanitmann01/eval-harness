"""Display filters shared by every server-rendered console template."""

from __future__ import annotations

from datetime import datetime

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
    """Format a USD amount with six decimals, or a dash."""
    return "-" if value is None else f"${value:.6f}"


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
            "timestamp": format_time,
        }
    )
