from importlib.metadata import version, PackageNotFoundError

from . import cot

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"
