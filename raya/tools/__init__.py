"""Tools (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.10, §8).

catalog/ (déclarations par domaine, Phase 3) est vide en Phase 0 — le
mécanisme registry/discovery/validation/execution est, lui, réel et testé.
"""

from .discovery import discover
from .execution import execute
from .registry import ToolRegistry
from .validation import ValidationError, validate_call

__all__ = ["ToolRegistry", "ValidationError", "discover", "execute", "validate_call"]
