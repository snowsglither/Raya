"""Priorité C — Runtime : bootstrap, shutdown, composition."""

from __future__ import annotations

from raya.runtime.bootstrap import bootstrap


def test_bootstrap_wires_all_handles():
    handles = bootstrap()
    try:
        assert handles.bus is not None
        assert handles.safety is not None
        assert handles.world_state is not None
        assert handles.memory is not None
        assert handles.tasks is not None
        assert handles.models is not None
        assert handles.harness is not None
    finally:
        handles.shutdown()


def test_bootstrap_publishes_runtime_started_event():
    handles = bootstrap()
    try:
        handles.bus.wait_idle(timeout_s=0.5)
        events = handles.tracer.all()
        assert any(e.type == "runtime.started" for e in events)
    finally:
        handles.shutdown()


def test_shutdown_publishes_runtime_stopping():
    handles = bootstrap()
    handles.shutdown()
    events = handles.tracer.all()
    assert any(e.type == "runtime.stopping" for e in events)
