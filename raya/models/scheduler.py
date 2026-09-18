"""ModelScheduler — contrôle de concurrence pour les appels modèle (Chantier 2).

Semaphore global (default=3, Ollama Pro) + observabilité active_count.
Séparé du TaskScheduler (raya/harness/scheduler.py) qui gère les tâches,
pas les slots modèle — deux préoccupations distinctes.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

from raya.contracts import ErrorInfo, FinishReason, ModelResponse
from raya.observability import log

if TYPE_CHECKING:
    from .registry import ModelRegistry


class ModelScheduler:
    def __init__(
        self,
        global_max: int = 3,
        *,
        stop_fn: Callable[[], bool] | None = None,
        registry: ModelRegistry | None = None,
    ) -> None:
        self._global_semaphore = threading.Semaphore(global_max)
        self._global_max = global_max
        self._active = 0
        self._lock = threading.Lock()
        self._stop_fn = stop_fn
        self._registry = registry

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    @property
    def global_max(self) -> int:
        return self._global_max

    def run_request(
        self,
        model_id: str,
        fn: Callable[[], ModelResponse],
        req_id: str,
    ) -> ModelResponse:
        if self._stop_fn and self._stop_fn():
            return ModelResponse(
                request_id=req_id,
                provider_used=f"scheduler:{model_id}",
                content=[],
                finish_reason=FinishReason.ERROR,
                error=ErrorInfo(
                    code="SCHEDULER_STOP",
                    message="STOP actif — invocation modèle annulée avant départ",
                    retryable=False,
                ),
            )

        self._global_semaphore.acquire()
        with self._lock:
            self._active += 1

        failure = False
        try:
            result = fn()
            failure = result.finish_reason == FinishReason.ERROR
            return result
        except Exception as exc:
            failure = True
            log("error", "ModelScheduler: exception pendant appel modèle", model=model_id, exc=str(exc))
            raise
        finally:
            if self._registry is not None:
                self._registry.update_health(model_id, failure=failure)
            self._global_semaphore.release()
            with self._lock:
                self._active -= 1
