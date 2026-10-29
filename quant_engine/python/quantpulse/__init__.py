"""QuantPulse - silnik obliczeń w C++20 dla laboratorium HossaLab."""
from ._core import RollingWindow, rolling_mean, rolling_std, rolling_zscore  # noqa: F401

try:
    from ._core import __version__  # noqa: F401
except ImportError:
    __version__ = "0.0.0"
