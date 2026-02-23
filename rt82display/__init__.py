"""RT82 Display - Upload GIFs to Epomaker RT82 keyboard LCD screen."""

try:
    from ._version import __version__
except ModuleNotFoundError:
    __version__ = "dev"

from .cli import DeviceBusy

__all__ = ["__version__", "DeviceBusy"]
