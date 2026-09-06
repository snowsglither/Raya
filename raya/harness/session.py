"""Gestion des HarnessState (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.5, §8).

Phase 0 : en mémoire uniquement. `HarnessState` reste checkpointable
(le passage à une vraie persistance ne change pas cette API — Phase 1).
"""

from __future__ import annotations

import threading

from raya.contracts import Channel, HarnessState, HarnessStatus


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, HarnessState] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, correlation_id: str, channel: Channel) -> HarnessState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = HarnessState(
                    session_id=session_id,
                    correlation_id=correlation_id,
                    channel=channel,
                    history_ref=session_id,
                    status=HarnessStatus.IDLE,
                )
                self._sessions[session_id] = state
            return state

    def get(self, session_id: str) -> HarnessState | None:
        with self._lock:
            return self._sessions.get(session_id)
