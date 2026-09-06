"""Détection du navigateur par défaut Windows (passe 'Targeted Fix', Sujet
3) — mécanisme purement informationnel, jamais câblé à une décision de
comportement (voir raya/devices/windows/mechanisms/browser_detect.py)."""

from __future__ import annotations

from raya.devices.windows.mechanisms.browser_detect import _KNOWN_PROG_IDS, detect_default_browser


def test_real_detection_never_crashes_and_returns_a_plausible_value_or_none():
    """Test RÉEL (registre Windows de cette machine, jamais mocké) — ne
    doit jamais lever d'exception, quel que soit le navigateur par défaut
    réellement configuré."""
    result = detect_default_browser()
    assert result is None or isinstance(result, str)


def test_known_prog_ids_map_to_generic_names(monkeypatch):
    import winreg

    class _FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open_key(hive, path):
        return _FakeKey()

    def fake_query_value(key, name):
        return ("ChromeHTML", 1)

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query_value)
    assert detect_default_browser() == "chrome"


def test_unknown_prog_id_is_returned_as_is_never_a_guess(monkeypatch):
    """Jamais une supposition parmi les navigateurs connus — un ProgId
    inconnu reste tel quel, honnêtement non traduit."""
    import winreg

    class _FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda hive, path: _FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: ("SomeObscureBrowserHTML", 1))
    assert detect_default_browser() == "SomeObscureBrowserHTML"


def test_registry_key_absent_returns_none_never_raises(monkeypatch):
    import winreg

    def raise_not_found(hive, path):
        raise FileNotFoundError()

    monkeypatch.setattr(winreg, "OpenKey", raise_not_found)
    assert detect_default_browser() is None


def test_automation_browser_choice_remains_unchanged_edge_dedicated_profile_stays_the_safe_fallback():
    """Non-régression architecturale explicite (consigne Sujet 3) : cette
    passe n'implémente PAS de bascule de comportement — le Browser Device
    Agent continue d'utiliser EXCLUSIVEMENT le profil Edge dédié isolé,
    quel que soit le navigateur par défaut détecté. `_KNOWN_PROG_IDS` reste
    une table de LECTURE pure, jamais une logique de choix."""
    import raya.devices.browser.session as browser_session

    assert "msedge.exe" in __import__("inspect").getsource(browser_session._locate_edge)
    assert "chrome.exe" not in __import__("inspect").getsource(browser_session._locate_edge)
    # La table de mapping reste purement informationnelle (dict), jamais une
    # fonction de décision retournant un exécutable à lancer.
    assert isinstance(_KNOWN_PROG_IDS, dict)
