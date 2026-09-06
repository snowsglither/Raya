"""Runtime — composition root (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.1)."""

from .bootstrap import RuntimeHandles, bootstrap
from .config import RuntimeConfig, load_config

__all__ = ["RuntimeConfig", "RuntimeHandles", "bootstrap", "load_config"]
