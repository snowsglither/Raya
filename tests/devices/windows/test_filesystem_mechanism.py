"""devices/windows/mechanisms/filesystem.py (Chantier 12 §C) — découverte
CIBLÉE (bornée en profondeur/résultats, dossiers volumineux ignorés), jamais
un scan massif de tout le disque. Teste le mécanisme pur, sans passer par le
Tool/Device (déjà couvert par tests/tools/test_pc_catalog.py)."""

from __future__ import annotations

from raya.devices.windows.mechanisms import filesystem


def test_find_folder_locates_exact_case_insensitive_match(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "Projets"
    target.mkdir()
    result = filesystem.find_folder("projets")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["matches"][0] == str(target)


def test_find_folder_returns_empty_when_nothing_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "Documents").mkdir()
    result = filesystem.find_folder("un-dossier-qui-nexiste-pas-du-tout")
    assert result["status"] == "ok"
    assert result["matches"] == []
    assert result["count"] == 0


def test_find_folder_reports_multiple_matches_never_guesses_which(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "Archive").mkdir()
    (tmp_path / "b" / "Archive").mkdir()
    result = filesystem.find_folder("archive")
    assert result["count"] == 2


def test_find_folder_never_descends_into_skip_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", staticmethod(lambda: tmp_path))
    hidden = tmp_path / "node_modules" / "Projets"
    hidden.mkdir(parents=True)
    result = filesystem.find_folder("projets")
    assert result["matches"] == []  # jamais descendu dans node_modules


def test_find_folder_bounded_depth_stops_descending(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", staticmethod(lambda: tmp_path))
    deep = tmp_path
    for i in range(6):
        deep = deep / f"level{i}"
        deep.mkdir()
    target = deep / "TropProfond"
    target.mkdir()
    result = filesystem.find_folder("tropprofond", max_depth=3)
    assert result["matches"] == []


def test_find_folder_rejects_empty_name():
    result = filesystem.find_folder("   ")
    assert result["status"] == "error"


def test_open_path_reports_missing_path_honestly(tmp_path):
    result = filesystem.open_path(str(tmp_path / "inexistant"))
    assert result["status"] == "error"


def test_open_path_succeeds_for_a_real_existing_directory(tmp_path, monkeypatch):
    """N'invoque JAMAIS le vrai `os.startfile` dans un test automatisé (ouvrirait
    une vraie fenêtre Explorer) — vérifie uniquement la logique de résolution/
    résultat ; le lancement OS réel est validé manuellement (voir rapport final)."""
    called = {}
    monkeypatch.setattr(filesystem.os, "startfile", lambda path: called.setdefault("path", path))
    result = filesystem.open_path(str(tmp_path))
    assert result["status"] == "ok"
    assert result["path"] == str(tmp_path)
    assert called["path"] == str(tmp_path)
