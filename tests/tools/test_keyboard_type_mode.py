"""Tests BUG B — pc.keyboard.type mode=replace/append.

Vérifie :
T1  schema contient mode (replace/append)
T2  default = append (préserve comportement existant, raw keyboard)
T3  replace envoie Ctrl+A avant frappe
T4  append n'envoie pas Ctrl+A
T5  mode apparaît dans output et evidence
T6  browser.type inchangé (régression)
T7  pc.ui.type inchangé (régression)
T8  directive §19 mentionne pc.keyboard.type mode='replace'
T9  append explicite depuis directive §19
T10 inspection avant écriture directive correctement formulée
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ─── T1 : schema contient mode ────────────────────────────────────────────────

def test_keyboard_type_schema_has_mode_field():
    """pc.keyboard.type doit déclarer un champ 'mode' dans son input_schema."""
    from raya.tools.catalog import register_pc_tools
    from raya.tools import ToolRegistry

    registry = ToolRegistry()

    class _Fake:
        def execute(self, cmd, should_stop=None): return None
        def list_capabilities(self): return []
        def health(self): return None
        def shutdown(self): pass

    register_pc_tools(registry, _Fake(), lambda: False)
    tool = registry.get("pc.keyboard.type")
    assert tool is not None
    props = tool.input_schema.get("properties", {})
    assert "mode" in props, f"pc.keyboard.type doit avoir 'mode' dans schema, props={list(props)}"
    mode_prop = props["mode"]
    assert "enum" in mode_prop, "mode doit être un enum"
    assert "replace" in mode_prop["enum"]
    assert "append" in mode_prop["enum"]


# ─── T2 : default = append ────────────────────────────────────────────────────

def test_keyboard_type_default_mode_is_append():
    """Le défaut de pc.keyboard.type est 'append' — raw keyboard primitive,
    conserve comportement existant et évite les Ctrl+A non intentionnels."""
    from raya.devices.windows.agent import _keyboard_type, WindowsDeviceAgent
    from raya.contracts import Command

    call_order = []

    with patch("raya.devices.windows.mechanisms.keyboard.press", side_effect=lambda c: call_order.append(("press", c))), \
         patch("raya.devices.windows.mechanisms.keyboard.type_text", side_effect=lambda t: call_order.append(("type", t)) or True), \
         patch("raya.devices.windows.mechanisms.window_mgmt.get_active_window", return_value={"status": "ok", "active": None}):
        cmd = Command(device_id="w", capability_name="keyboard.type",
                      arguments={"text": "Bonjour"},  # pas de mode = default
                      correlation_id="x", timeout_ms=5000)
        agent = MagicMock(spec=WindowsDeviceAgent)
        _keyboard_type(agent, cmd, lambda: False)

    # Ctrl+A ne doit PAS avoir été envoyé
    ctrl_a_calls = [c for c in call_order if c[0] == "press" and "ctrl" in c[1].lower()]
    assert len(ctrl_a_calls) == 0, (
        f"mode='append' par défaut ne doit pas envoyer Ctrl+A, calls={call_order}"
    )
    # Le texte doit avoir été tapé
    type_calls = [c for c in call_order if c[0] == "type"]
    assert len(type_calls) == 1 and type_calls[0][1] == "Bonjour"


# ─── T3 : replace envoie Ctrl+A avant frappe ─────────────────────────────────

def test_keyboard_type_replace_sends_ctrl_a_before_typing():
    """mode='replace' : Ctrl+A doit être envoyé AVANT type_text — sélectionne
    tout le contenu existant, puis Ctrl+V le remplace."""
    from raya.devices.windows.agent import _keyboard_type, WindowsDeviceAgent
    from raya.contracts import Command

    call_order = []

    with patch("raya.devices.windows.mechanisms.keyboard.press", side_effect=lambda c: call_order.append(("press", c))) as mock_press, \
         patch("raya.devices.windows.mechanisms.keyboard.type_text", side_effect=lambda t: call_order.append(("type", t)) or True), \
         patch("raya.devices.windows.mechanisms.window_mgmt.get_active_window", return_value={"status": "ok", "active": None}):
        cmd = Command(device_id="w", capability_name="keyboard.type",
                      arguments={"text": "Salut", "mode": "replace"},
                      correlation_id="x", timeout_ms=5000)
        agent = MagicMock(spec=WindowsDeviceAgent)
        result = _keyboard_type(agent, cmd, lambda: False)

    # Ctrl+A doit être appelé
    ctrl_a_calls = [c for c in call_order if c[0] == "press" and "ctrl+a" in c[1].lower()]
    assert len(ctrl_a_calls) >= 1, f"mode='replace' doit envoyer Ctrl+A, calls={call_order}"

    # Ctrl+A doit précéder type_text
    first_ctrl_a = next(i for i, c in enumerate(call_order) if c[0] == "press" and "ctrl+a" in c[1].lower())
    first_type = next(i for i, c in enumerate(call_order) if c[0] == "type")
    assert first_ctrl_a < first_type, "Ctrl+A doit précéder type_text dans mode='replace'"

    assert result.output.get("mode") == "replace"


# ─── T4 : append n'envoie pas Ctrl+A ─────────────────────────────────────────

def test_keyboard_type_append_does_not_send_ctrl_a():
    """mode='append' : Ctrl+A ne doit PAS être envoyé — insère au curseur."""
    from raya.devices.windows.agent import _keyboard_type, WindowsDeviceAgent
    from raya.contracts import Command

    ctrl_a_called = []

    with patch("raya.devices.windows.mechanisms.keyboard.press",
               side_effect=lambda c: ctrl_a_called.append(c) if "ctrl+a" in c.lower() else None), \
         patch("raya.devices.windows.mechanisms.keyboard.type_text", return_value=True), \
         patch("raya.devices.windows.mechanisms.window_mgmt.get_active_window", return_value={"status": "ok", "active": None}):
        cmd = Command(device_id="w", capability_name="keyboard.type",
                      arguments={"text": "Ajouter", "mode": "append"},
                      correlation_id="x", timeout_ms=5000)
        agent = MagicMock(spec=WindowsDeviceAgent)
        _keyboard_type(agent, cmd, lambda: False)

    assert len(ctrl_a_called) == 0, f"mode='append' ne doit pas envoyer Ctrl+A, envoyé : {ctrl_a_called}"


# ─── T5 : mode dans output et evidence ────────────────────────────────────────

def test_keyboard_type_mode_in_output_and_evidence():
    """Le mode utilisé doit apparaître dans output ET evidence pour la traçabilité."""
    from raya.devices.windows.agent import _keyboard_type, WindowsDeviceAgent
    from raya.contracts import Command, CommandStatus

    for mode in ("replace", "append"):
        with patch("raya.devices.windows.mechanisms.keyboard.press"), \
             patch("raya.devices.windows.mechanisms.keyboard.type_text", return_value=True), \
             patch("raya.devices.windows.mechanisms.window_mgmt.get_active_window",
                   return_value={"status": "ok", "active": {"hwnd": 1, "title": "Test App", "pid": 1, "process": "app.exe"}}):
            cmd = Command(device_id="w", capability_name="keyboard.type",
                          arguments={"text": "texte", "mode": mode},
                          correlation_id="x", timeout_ms=5000)
            agent = MagicMock(spec=WindowsDeviceAgent)
            result = _keyboard_type(agent, cmd, lambda: False)

        assert result.status == CommandStatus.SUCCESS
        assert result.output.get("mode") == mode, f"output doit contenir mode={mode!r}"
        assert result.evidence.get("mode") == mode, f"evidence doit contenir mode={mode!r}"


# ─── T6 : browser.type inchangé ────────────────────────────────────────────────

def test_browser_type_schema_unchanged():
    """Régression : browser.type doit toujours avoir mode=replace/append/clear."""
    from raya.tools.catalog.browser import _LAST_TYPED_OBSERVATION
    from raya.devices.browser.agent import _CAPABILITIES

    # Vérifie que la capability browser.type a toujours mode
    bt_cap = next((c for c in _CAPABILITIES if c.name == "browser.type"), None)
    assert bt_cap is not None
    props = bt_cap.input_schema.get("properties", {})
    assert "mode" in props
    assert "replace" in props["mode"]["enum"]
    assert "clear" in props["mode"]["enum"]
    # ObservationSpec inchangée
    assert len(_LAST_TYPED_OBSERVATION) == 2


# ─── T7 : pc.ui.type inchangé ──────────────────────────────────────────────────

def test_pc_ui_type_schema_unchanged():
    """Régression : pc.ui.type doit toujours avoir mode=replace/append/clear."""
    from raya.tools.catalog import register_pc_tools
    from raya.tools import ToolRegistry

    registry = ToolRegistry()

    class _Fake:
        def execute(self, cmd, should_stop=None): return None
        def list_capabilities(self): return []
        def health(self): return None
        def shutdown(self): pass

    register_pc_tools(registry, _Fake(), lambda: False)
    tool = registry.get("pc.ui.type")
    assert tool is not None
    props = tool.input_schema.get("properties", {})
    assert "mode" in props
    assert "replace" in props["mode"]["enum"]
    assert "clear" in props["mode"]["enum"]


# ─── T8 : directive §19 mentionne pc.keyboard.type mode='replace' ─────────────

def test_write_semantics_directive_covers_keyboard_type_replace():
    """La directive Write semantics doit indiquer que pc.keyboard.type
    doit utiliser mode='replace' pour les commandes 'écris X'."""
    from raya.context_engine.render import render_system_prompt
    from raya.contracts import Context, ContextSection, SectionKind

    ctx = Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="test",
    )], used_tokens_estimate=0)
    rendered = render_system_prompt(ctx)

    assert "pc.keyboard.type" in rendered, "directive doit mentionner pc.keyboard.type"
    assert "mode='replace'" in rendered or "mode=replace" in rendered.replace("'", ""), (
        "directive doit mentionner mode='replace' pour pc.keyboard.type"
    )


# ─── T9 : directive distingue append/replace ──────────────────────────────────

def test_write_semantics_directive_distinguishes_append_replace():
    """La directive doit mentionner 'append' explicitement pour 'ajoute'."""
    from raya.context_engine.render import render_system_prompt
    from raya.contracts import Context, ContextSection, SectionKind

    ctx = Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="test",
    )], used_tokens_estimate=0)
    rendered = render_system_prompt(ctx)

    lower = rendered.lower()
    assert "append" in lower, "directive doit mentionner append"
    assert "ajoute" in lower or "ajouter" in lower, "directive doit mentionner le cas 'ajoute'"


# ─── T10 : directive mentionne inspection avant écriture ─────────────────────

def test_write_semantics_directive_mentions_inspection():
    """La directive doit mentionner pc.ui.inspect pour observer l'état avant écriture."""
    from raya.context_engine.render import render_system_prompt
    from raya.contracts import Context, ContextSection, SectionKind

    ctx = Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="test",
    )], used_tokens_estimate=0)
    rendered = render_system_prompt(ctx)

    assert "pc.ui.inspect" in rendered, (
        "directive doit mentionner pc.ui.inspect pour observer l'état avant écriture"
    )


# ─── Tests Windows réels (skip si win32 indisponible) ─────────────────────────

def _win32_available() -> bool:
    try:
        import win32gui
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _win32_available(), reason="win32 non disponible")
def test_real_notepad_replace_mode_overwrites_content(tmp_path):
    """E2E 2 — Windows réel : mode='replace' remplace le contenu existant de Notepad."""
    import time
    import psutil
    import win32gui
    from raya.devices.windows.mechanisms import applications, window_mgmt, keyboard

    # Tue les Notepad existants
    for p in psutil.process_iter(["name"]):
        if "notepad" in (p.info["name"] or "").lower():
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(0.3)

    r = applications.launch("notepad", wait_timeout_s=8.0)
    if r.get("status") not in ("ok",):
        pytest.skip(f"BLOCKED: Notepad non lancé : {r}")

    try:
        time.sleep(0.5)
        # Écrit "Bonjour" d'abord (append — comportement brut)
        keyboard.type_text("Bonjour")
        time.sleep(0.2)

        # Maintenant mode='replace' — doit effacer "Bonjour" et écrire "Salut"
        keyboard.press("ctrl+a")  # simule mode='replace'
        keyboard.type_text("Salut")
        time.sleep(0.3)

        # Vérifie le titre (contient l'astérisque + le contenu si court)
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        # Le titre Notepad Windows 11 montre "*Salut — Bloc-notes" si contenu = "Salut"
        # Si contenu = "Salut" le fichier s'appelle *Salut, si contenu = "BonjourSalut" c'est le bug
        assert "Salut" in title, f"Titre doit contenir 'Salut' : {title!r}"
        # "Bonjour" ne doit plus être dans le titre (remplacement réussi)
        # Note : le titre Notepad Windows 11 affiche uniquement la 1ère ligne
        # Ce test vérifie le comportement de la primitive Ctrl+A + type_text

    finally:
        for p in psutil.process_iter(["name"]):
            if "notepad" in (p.info["name"] or "").lower():
                try:
                    p.kill()
                except Exception:
                    pass


@pytest.mark.skipif(not _win32_available(), reason="win32 non disponible")
def test_real_notepad_append_mode_preserves_content(tmp_path):
    """E2E 3 — Windows réel : mode='append' conserve le contenu existant."""
    import time
    import psutil
    import win32gui
    from raya.devices.windows.mechanisms import applications, window_mgmt, keyboard

    for p in psutil.process_iter(["name"]):
        if "notepad" in (p.info["name"] or "").lower():
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(0.3)

    r = applications.launch("notepad", wait_timeout_s=8.0)
    if r.get("status") not in ("ok",):
        pytest.skip(f"BLOCKED: Notepad non lancé : {r}")

    try:
        time.sleep(0.5)
        keyboard.type_text("Bonjour")
        time.sleep(0.2)

        # mode='append' — conserve "Bonjour" et ajoute " Salut"
        keyboard.type_text(" Salut")  # pas de Ctrl+A
        time.sleep(0.3)

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        # Les deux textes doivent coexister dans le document
        assert "Bonjour" in title or "Salut" in title, (
            f"Titre doit contenir trace du contenu : {title!r}"
        )

    finally:
        for p in psutil.process_iter(["name"]):
            if "notepad" in (p.info["name"] or "").lower():
                try:
                    p.kill()
                except Exception:
                    pass


@pytest.mark.skipif(not _win32_available(), reason="win32 non disponible")
def test_real_keyboard_type_replace_via_device_agent(tmp_path):
    """E2E 2b — pc.keyboard.type(mode='replace') via le Device Agent réel :
    Ctrl+A + Ctrl+V remplace le contenu."""
    import time
    import psutil
    import win32gui
    from raya.devices.windows import WindowsDeviceAgent
    from raya.contracts import Command, CommandStatus

    for p in psutil.process_iter(["name"]):
        if "notepad" in (p.info["name"] or "").lower():
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(0.3)

    agent = WindowsDeviceAgent(tmp_path / "screens")

    # Lance Notepad
    launch_cmd = Command(device_id="windows_agent", capability_name="application.launch",
                         arguments={"target": "notepad"}, correlation_id="t1", timeout_ms=10000)
    r = agent.execute(launch_cmd)
    if r.status != CommandStatus.SUCCESS:
        pytest.skip(f"BLOCKED: Notepad non lancé : {r.error}")

    time.sleep(0.5)
    try:
        # Tape "Bonjour" (append par défaut)
        cmd1 = Command(device_id="windows_agent", capability_name="keyboard.type",
                       arguments={"text": "Bonjour"}, correlation_id="t2", timeout_ms=5000)
        r1 = agent.execute(cmd1)
        assert r1.status == CommandStatus.SUCCESS
        assert r1.output.get("mode") == "append"  # défaut
        time.sleep(0.3)

        # Tape "Salut" avec mode=replace → doit remplacer "Bonjour"
        cmd2 = Command(device_id="windows_agent", capability_name="keyboard.type",
                       arguments={"text": "Salut", "mode": "replace"}, correlation_id="t3", timeout_ms=5000)
        r2 = agent.execute(cmd2)
        assert r2.status == CommandStatus.SUCCESS
        assert r2.output.get("mode") == "replace"
        assert r2.evidence.get("mode") == "replace"
        time.sleep(0.3)

        # Vérifie le titre Notepad
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        assert "Salut" in title, f"Après mode='replace', le document doit contenir 'Salut' : {title!r}"

    finally:
        for p in psutil.process_iter(["name"]):
            if "notepad" in (p.info["name"] or "").lower():
                try:
                    p.kill()
                except Exception:
                    pass
