"""Crush color theme for Rich console output.

Based on the CLI Style Guide with the Crush/Charmtone palette.
"""

from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel

# Crush theme color palette
CRUSH_THEME = Theme({
    # Status colors
    "success": "#12C78F",      # Guac
    "error": "#EB4268",        # Sriracha
    "warning": "#E8FE96",      # Zest
    "info": "#00A4FF",         # Malibu
    
    # UI colors
    "primary": "#6B50FF",      # Charple
    "secondary": "#FF60FF",    # Dolly
    "tertiary": "#68FFD6",     # Bok
    "accent": "#E8FE96",       # Zest
    
    # Text colors
    "muted": "#858392",        # Squid
    "subtle": "#605F6B",       # Oyster
    "highlight": "#F1EFEF",    # Salt
    
    # Content colors
    "coral": "#FF577D",        # Coral
    "julep": "#00FFB2",        # Julep
    "cumin": "#BF976F",        # Cumin
})

# Global console instance with Crush theme
console = Console(theme=CRUSH_THEME)


def success(message: str) -> None:
    """Print a success message with green checkmark."""
    console.print(f"[success]✅[/success] {message}")


def error(message: str) -> None:
    """Print an error message with red X."""
    console.print(f"[error]❌[/error] {message}")


def warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[warning]⚠️[/warning] {message}")


def info(message: str) -> None:
    """Print an info message."""
    console.print(f"[info]💡[/info] {message}")


def muted(message: str) -> None:
    """Print muted/secondary text."""
    console.print(f"[muted]{message}[/muted]")


def print_header(title: str, emoji: str = "") -> None:
    """Print a section header with horizontal rule."""
    console.print()
    prefix = f"{emoji} " if emoji else ""
    console.rule(f"[primary]{prefix}{title}[/primary]", style="primary")
    console.print()


def print_banner() -> None:
    """Print the application banner."""
    console.print(Panel.fit(
        "🖥️ [primary]RT82 Display[/primary] › Upload GIFs to your keyboard",
        border_style="primary"
    ))


def step_progress(current: int, total: int) -> str:
    """Generate visual progress indicator with filled/empty dots."""
    filled = "●" * current
    empty = "○" * (total - current)
    return f"[success]{filled}[/success][muted]{empty}[/muted]"


def format_bytes(size: int) -> str:
    """Format byte size in human readable format."""
    for unit in ['B', 'KB', 'MB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
