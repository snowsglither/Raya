"""Tool Registry (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.10, §8.1)."""

from __future__ import annotations

from typing import Callable

from raya.contracts import Tool, ToolCall, ToolResult

ToolHandler = Callable[[ToolCall], ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, tool: Tool, handler: ToolHandler | None = None) -> None:
        self._tools[tool.name] = tool
        if handler is not None:
            self._handlers[tool.name] = handler

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def handler_for(self, name: str) -> ToolHandler | None:
        return self._handlers.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def all_capability_tags(self) -> list[str]:
        """Union des capability_tags de tous les outils enregistrés — permet
        au Harness de faire de la vraie découverte (tools.discovery, jamais
        tools.execution) sans coder de correspondance texte→outil en dur
        (consigne Phase 3 §22)."""
        tags: set[str] = set()
        for tool in self._tools.values():
            tags.update(tool.capability_tags)
        return sorted(tags)
