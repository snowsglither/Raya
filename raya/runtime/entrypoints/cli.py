"""Point d'entrée CLI — `python -m raya.runtime.entrypoints.cli` (consigne Phase 0 §5, §7)."""

from __future__ import annotations

from raya.interfaces.cli.repl import run
from raya.runtime.bootstrap import bootstrap


def main() -> None:
    handles = bootstrap()
    try:
        run(handles.harness, handles.bus, event_store=handles.event_store)
    finally:
        handles.shutdown()


if __name__ == "__main__":
    main()
