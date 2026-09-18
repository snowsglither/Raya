"""Automated code path verification for the Real Multimodal Audit.
Run: python tests/audit/run_automated_checks.py
"""
from __future__ import annotations
import sys
import os
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

results = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    results.append((name, status, detail))
    suffix = f": {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")


print("=== AUTOMATED CODE PATH TESTS (20 tests) ===\n")

# ── T1-T3: ObservationSpec on browser tools ──────────────────────────────────
from raya.tools.catalog.browser import register_browser_tools
from raya.tools import ToolRegistry

class _FakeAgent:
    def execute(self, c, should_stop=None): pass

reg = ToolRegistry()
register_browser_tools(reg, _FakeAgent(), lambda: False)

read_page = reg.get("browser.read_page")
check("T1: browser.read_page.observation is empty (DOM not promoted to WorldState)",
      read_page is not None and len(read_page.observation) == 0)

navigate = reg.get("browser.navigate")
check("T2: browser.navigate has current_url ObservationSpec",
      navigate is not None and len(navigate.observation) > 0 and navigate.observation[0].key == "current_url")

screenshot = reg.get("browser.screenshot")
check("T3: browser.screenshot.observation is empty (no WorldState promotion)",
      screenshot is not None and len(screenshot.observation) == 0)

# ── T4-T6: ObservationSpec on vision tools ────────────────────────────────────
from raya.tools.catalog.visual import register_visual_tools
from raya.contracts import ModelCapability, VisualObservation, Confidence, new_id


def _fake_observe(path, prompt, find_target, corr_id, prefer_local, viewport, source):
    return VisualObservation(
        observation_id=new_id("obs"), source="perception:screen",
        description="fake", confidence=Confidence.INFERRED,
    )


def _fake_cap(): return {"path": "/tmp/test.png", "width": 100, "height": 100}
def _fake_cap_browser(): return {"path": "/tmp/browser.png"}


reg2 = ToolRegistry()
register_visual_tools(reg2, _fake_observe, _fake_cap, _fake_cap_browser)

obs_screen = reg2.get("vision.observe_screen")
check("T4: vision.observe_screen has screen_state ObservationSpec (WorldState domain=visual)",
      obs_screen is not None and len(obs_screen.observation) > 0 and obs_screen.observation[0].key == "screen_state")

obs_browser = reg2.get("vision.observe_browser")
check("T5: vision.observe_browser has browser_visual_state ObservationSpec",
      obs_browser is not None and len(obs_browser.observation) > 0 and obs_browser.observation[0].key == "browser_visual_state")

obs_image = reg2.get("vision.observe_image")
check("T6: vision.observe_image has last_image_observation ObservationSpec",
      obs_image is not None and len(obs_image.observation) > 0 and obs_image.observation[0].key == "last_image_observation")

# ── T7-T8: _parse_grounding coordinate validation ────────────────────────────
from raya.models.vision import _parse_grounding

result_pixel = _parse_grounding(
    'FOUND: bbox=[379,475,620,534] label="search" confidence=0.95', "obs1", None
)
check("T7: _parse_grounding rejects pixel coordinates (>1.0) — GAP found in grounding",
      result_pixel is None,
      f"result={result_pixel}")

result_norm = _parse_grounding(
    'FOUND: bbox=[0.1,0.2,0.5,0.7] label="button" confidence=0.8', "obs2", None
)
check("T8: _parse_grounding accepts normalized [0,1] coordinates",
      result_norm is not None and abs(result_norm.bbox.x_min - 0.1) < 0.001)

# ── T9: BoundingBox rejects out-of-range ────────────────────────────────────
from raya.contracts import BoundingBox
try:
    BoundingBox(x_min=-0.1, y_min=0, x_max=1, y_max=1)
    check("T9: BoundingBox rejects out-of-range values", False, "no exception raised")
except Exception as e:
    check("T9: BoundingBox rejects out-of-range values", True, f"{type(e).__name__}")

# ── T10: VisualObservation confidence enforced ───────────────────────────────
try:
    VisualObservation(
        observation_id=new_id("obs"), source="perception:screen",
        description="test", confidence=Confidence.KNOWN_FACT
    )
    check("T10: VisualObservation rejects non-INFERRED confidence", False, "no exception raised")
except Exception:
    check("T10: VisualObservation rejects non-INFERRED confidence", True)

# ── T11-T12: Privacy contracts ───────────────────────────────────────────────
from raya.contracts import VisualArtifact

obs_priv = VisualObservation(
    observation_id=new_id("obs"), source="perception:screen",
    description="test", confidence=Confidence.INFERRED,
    artifact_ref=VisualArtifact(path="/secret/path.png", width=100, height=100),
)
world_val = obs_priv.to_world_state_value()
check("T11: to_world_state_value() excludes artifact_ref",
      "artifact_ref" not in world_val and "/secret/path.png" not in str(world_val))

try:
    VisualObservation(
        observation_id=new_id("obs"), source="tool:screen",
        description="test", confidence=Confidence.INFERRED
    )
    check("T12: VisualObservation rejects source not starting with perception:", False)
except Exception:
    check("T12: VisualObservation rejects source not starting with perception:", True)

# ── T13: WorldState retrieve_relevant(()) returns all domains ─────────────────
from raya.world_state import WorldStateStore
from raya.persistence.sqlite_backend import SqliteBackend as SQLiteBackend
from raya.contracts import WorldStateFact

db = SQLiteBackend(os.path.join(tempfile.mkdtemp(), "test.db"))
ws = WorldStateStore(db)
ws.apply_update(WorldStateFact(domain="visual", key="screen_state",
                               value={"desc": "test"}, source="tool:vision",
                               confidence=Confidence.INFERRED))
ws.apply_update(WorldStateFact(domain="browser", key="current_url",
                               value="https://example.com", source="tool:browser",
                               confidence=Confidence.KNOWN_FACT))
all_facts = ws.retrieve_relevant(())
check("T13: retrieve_relevant(()) returns all domains (visual + browser simultaneously)",
      len(all_facts) >= 2, f"got {len(all_facts)} facts")

# ── T14: vision tag is SAFE ──────────────────────────────────────────────────
from raya.safety.risk import _RISK_BY_TAG
from raya.contracts import PermissionLevel
check("T14: vision tag is PermissionLevel.SAFE in risk table",
      _RISK_BY_TAG.get("vision") == PermissionLevel.SAFE,
      f"actual={_RISK_BY_TAG.get('vision')}")

# ── T15-T16: vision.* tools discoverable via all_capability_tags ─────────────
from raya.tools import discover

all_tags = reg2.all_capability_tags()
discovered = discover(reg2, list(all_tags))
tool_names = {t.name for t in discovered}
check("T15: vision.observe_screen discoverable via all_capability_tags",
      "vision.observe_screen" in tool_names)
check("T16: vision.find_in_browser discoverable via all_capability_tags",
      "vision.find_in_browser" in tool_names)

# ── T17-T18: _obs_to_output and _obs_to_evidence privacy ─────────────────────
from raya.tools.catalog.visual import _obs_to_output, _obs_to_evidence

obs_p = VisualObservation(
    observation_id="obs-priv", source="perception:screen",
    description="secret content", confidence=Confidence.INFERRED,
    artifact_ref=VisualArtifact(path="/sensitive/data.png", width=800, height=600),
)
out_dict = _obs_to_output(obs_p)
check("T17: _obs_to_output does not expose local file path",
      "/sensitive/data.png" not in str(out_dict))

evidence = _obs_to_evidence(obs_p)
ws_val = evidence.get("visual_observation", {})
check("T18: _obs_to_evidence excludes artifact_ref from WorldState value",
      "artifact_ref" not in ws_val and "/sensitive/data.png" not in str(ws_val))

# ── T19: render_system_prompt intentionally skips TOOL_SCHEMAS ───────────────
from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind

ctx = Context(session_id="test", budget_tokens=4096, sections=[
    ContextSection(
        kind=SectionKind.TOOL_SCHEMAS,
        content={"tools": [{"name": "hidden_tool_xyz"}]},
        provenance="tools:discovery",
        rank_score=0.9,
    )
])
prompt = render_system_prompt(ctx)
check("T19: render_system_prompt intentionally skips TOOL_SCHEMAS section",
      "hidden_tool_xyz" not in prompt)

# ── T20: handle_request assemble() omits world_state_domains (loads all) ──────
import inspect
from raya.harness.loop import Harness

src = inspect.getsource(Harness.handle_request)
# The assemble() call in handle_request should NOT contain world_state_domains
# (it uses the default `()` which means ALL facts are retrieved)
after_assemble = src.split("context = assemble(")[1].split(")")[0]
check("T20: handle_request assemble() omits world_state_domains — all WorldState facts in context",
      "world_state_domains" not in after_assemble)

# ── Summary ───────────────────────────────────────────────────────────────────
print()
pass_count = sum(1 for _, s, _ in results if s == "PASS")
fail_count = sum(1 for _, s, _ in results if s == "FAIL")
print(f"RESULTS: {pass_count}/{len(results)} PASS, {fail_count} FAIL")
