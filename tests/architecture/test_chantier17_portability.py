"""Chantier 17 (Portabilité Desktop <-> Laptop) — audit statique + garde-fou
de non-régression. L'inspection (lecture seule) a trouvé le code déjà
portable (racine dérivée de `__file__`, aucun chemin/nom d'utilisateur en
dur trouvé) — ces tests PROUVENT cet état et empêchent une régression
future, plutôt que de corriger un défaut qui n'existe pas."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

from raya.runtime.config import load_config  # noqa: E402

# Motifs machine-spécifiques réels (jamais un faux positif comme le nom du
# projet "raya"/"RAYA" lui-même, ou le prénom de l'utilisateur mentionné
# comme EXEMPLE illustratif dans un docstring — seulement un vrai CHEMIN
# codé en dur, ex: "C:\Users\ruben" ou "/home/ruben").
_HARDCODED_PATTERNS = (
    re.compile(r"[Uu]sers[\\/]+ruben", re.IGNORECASE),
    re.compile(r"/home/ruben", re.IGNORECASE),
    re.compile(r"OneDrive"),
    re.compile(r"\bRayaV2\b"),  # le NOM DE DOSSIER en dur, jamais le nom du package "raya"
    re.compile(r"C:\\\\Users"),
    re.compile(r"C:/Users"),
)

# Deux faux positifs légitimes déjà identifiés à l'inspection (Chantier 17
# §5) : un repli sur un chemin Windows UNIVERSEL (jamais un utilisateur/
# projet spécifique), lu depuis la vraie variable d'environnement OS en
# priorité — pas un chemin machine-spécifique codé en dur.
_KNOWN_SAFE_FILES = {"devices/browser/session.py"}


def _py_files(base: Path):
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_hardcoded_username_or_project_path_in_raya_source():
    """Garde-fou de non-régression (audit Chantier 17 §5 : zéro résultat
    réel trouvé) — empêche qu'un futur ajout réintroduise un chemin
    machine-spécifique dans raya/ (jamais dans tests/scripts/docs, qui
    peuvent légitimement référencer l'environnement de développement)."""
    violations = []
    for path in _py_files(arch_lint.RAYA_ROOT):
        rel = path.relative_to(arch_lint.RAYA_ROOT).as_posix()
        if rel in _KNOWN_SAFE_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _HARDCODED_PATTERNS:
            if pattern.search(text):
                violations.append(f"{rel}: {pattern.pattern}")
    assert violations == [], f"chemin/nom machine-spécifique trouvé : {violations}"


def test_config_root_is_derived_from_file_location_not_hardcoded(monkeypatch):
    """`load_config()` ne doit jamais supposer que le dossier s'appelle
    'RayaV2' ou vit à un emplacement précis — dérivé de `__file__`.
    `RAYA_DATA_DIR`/`RAYA_DB_PATH` sont explicitement retirés ici : le
    fixture autouse de tests/conftest.py les définit vers un tmp_path
    d'isolation pour TOUS les tests (jamais écrire dans le vrai data/ du
    repo) — ce test veut spécifiquement vérifier le comportement PAR
    DÉFAUT, sans override."""
    monkeypatch.delenv("RAYA_DATA_DIR", raising=False)
    monkeypatch.delenv("RAYA_DB_PATH", raising=False)
    cfg = load_config()
    assert cfg.root == Path(__file__).resolve().parents[2]
    # Le projet doit être déplaçable : rien n'oblige le nom de dossier.
    assert cfg.data_dir == cfg.root / "data"
    assert cfg.db_path == cfg.data_dir / "raya_v2.sqlite3"


def test_config_root_respects_an_explicit_override(monkeypatch):
    """Preuve directe de portabilité : pointer `load_config(root=...)` vers
    n'importe quel autre chemin fonctionne, sans toucher au code."""
    monkeypatch.delenv("RAYA_DATA_DIR", raising=False)
    monkeypatch.delenv("RAYA_DB_PATH", raising=False)
    fake_root = Path("D:/ailleurs/RayaCopie") if sys.platform == "win32" else Path("/ailleurs/RayaCopie")
    cfg = load_config(root=fake_root)
    assert cfg.root == fake_root
    assert cfg.data_dir == fake_root / "data"


# --- .env.example cohérent avec les variables réellement lues (B4/test 19) ---

_ENV_VAR_RE = re.compile(r'\bRAYA_[A-Z_]+\b')


def _env_vars_read_by_config() -> set[str]:
    source = (arch_lint.RAYA_ROOT / "runtime" / "config.py").read_text(encoding="utf-8")
    return set(_ENV_VAR_RE.findall(source)) - {"RAYA_ROOT"}  # RAYA_ROOT n'est pas une vraie variable d'env


def _env_vars_documented_in_example() -> set[str]:
    example = (arch_lint.RAYA_ROOT.parent / ".env.example").read_text(encoding="utf-8")
    return set(_ENV_VAR_RE.findall(example))


def test_env_example_documents_every_variable_actually_read_by_config():
    read_vars = _env_vars_read_by_config()
    documented = _env_vars_documented_in_example()
    missing = read_vars - documented
    assert missing == set(), f"variables lues par config.py mais absentes de .env.example : {missing}"


def test_env_example_has_no_dead_documentation_for_config_variables():
    """L'inverse : chaque variable RAYA_* documentée qui correspond à un nom
    plausible de config.py doit vraiment y être lue — évite une doc qui
    ment sur ce que le code consomme réellement."""
    read_vars = _env_vars_read_by_config()
    documented = _env_vars_documented_in_example()
    # RAYA_CDP_PORT et RAYA_ENABLE_PHONE_DEVICE/RAYA_TELEGRAM_*/RAYA_TIMEZONE
    # sont lus ailleurs que config.py (browser/session.py, contracts/clock.py)
    # — jamais un faux "mort" pour autant.
    known_elsewhere = {
        "RAYA_CDP_PORT", "RAYA_ENABLE_PHONE_DEVICE", "RAYA_TIMEZONE",
        "RAYA_TELEGRAM_ENABLED", "RAYA_TELEGRAM_BOT_TOKEN",
        "RAYA_TELEGRAM_ALLOWED_USER_IDS", "RAYA_TELEGRAM_POLL_TIMEOUT_S",
    }
    dead = documented - read_vars - known_elsewhere
    assert dead == set(), f"variables documentées mais jamais lues nulle part : {dead}"


def test_no_secret_value_committed_in_env_example():
    """.env.example ne doit contenir que des NOMS de variables, jamais une
    vraie valeur secrète (clé API, token)."""
    example = (arch_lint.RAYA_ROOT.parent / ".env.example").read_text(encoding="utf-8")
    assert "OLLAMA_API_KEY=" in example
    # Une vraie clé Ollama Cloud commence typiquement par un préfixe fixe ;
    # ici on vérifie simplement qu'aucune valeur n'est assignée après le '='
    # sur la ligne OLLAMA_API_KEY (juste le nom, jamais une vraie clé).
    for line in example.splitlines():
        if line.strip().lstrip("#").strip().startswith("OLLAMA_API_KEY="):
            value = line.split("=", 1)[1].strip()
            assert value == "" or value.startswith("#"), "une valeur semble committée pour OLLAMA_API_KEY"
