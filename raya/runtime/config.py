"""Configuration (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.1).

Chargement .env minimal, stdlib uniquement (pas de nouvelle dépendance pour
Phase 0 — pyproject.toml reste dependency-free en dehors de pytest pour dev).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from raya.contracts import DEFAULT_TIMEZONE, ModelCapability

# Pool de modèles PAR DÉFAUT — DONNÉE de configuration, pas une règle métier
# gravée dans le code (consigne Phase 3 §6 : "DeepSeek = toujours cerveau"
# est interdit comme règle irréversible). Modifiable sans toucher au code via
# RAYA_MODEL_POOL. Sous-ensemble raisonnable du pool mentionné par la
# consigne — le Router (raya/models/router.py) reste inchangé, capable
# d'accueillir n'importe quel autre modèle/capability sans modification.
_DEFAULT_MODEL_POOL = "deepseek-v4-flash:cloud:reasoning|planning|fast_response,kimi-k2.7-code:cloud:coding,gemma4:cloud:vision|classification|summarization"


def parse_model_pool(spec: str) -> list[tuple[str, list[ModelCapability]]]:
    """Format : "model_id:cap1|cap2,model_id2:cap3". Capabilities inconnues
    ignorées avec un avertissement plutôt qu'un crash (config utilisateur)."""
    pool: list[tuple[str, list[ModelCapability]]] = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        model_id, _, caps_raw = entry.rpartition(":")
        capabilities: list[ModelCapability] = []
        for cap_name in caps_raw.split("|"):
            try:
                capabilities.append(ModelCapability(cap_name.strip()))
            except ValueError:
                continue
        if model_id and capabilities:
            pool.append((model_id, capabilities))
    return pool


def _parse_int_list(spec: str) -> tuple[int, ...]:
    """Format : "123456789,987654321". Entrées non-numériques ignorées avec
    un avertissement plutôt qu'un crash (config utilisateur, même discipline
    que `parse_model_pool`)."""
    ids: list[int] = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            ids.append(int(entry))
        except ValueError:
            continue
    return tuple(ids)


def _strip_inline_comment(value: str) -> str:
    """`KEY=valeur   # commentaire` — un commentaire en fin de ligne n'est
    retiré que hors guillemets (une valeur explicitement quotée peut
    légitimement contenir un `#`, ex: un mot de passe)."""
    if value[:1] in ('"', "'"):
        return value
    for marker in (" #", "\t#"):
        idx = value.find(marker)
        if idx != -1:
            value = value[:idx]
    return value


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = _strip_inline_comment(value.strip())
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class RuntimeConfig:
    root: Path
    data_dir: Path
    db_path: Path
    ollama_api_key: str | None = None
    ollama_host: str = "https://ollama.com"
    ollama_local_host: str = "http://localhost:11434"
    enable_ollama_local: bool = False
    model_pool: list[tuple[str, list[ModelCapability]]] = field(default_factory=list)
    local_model_pool: list[tuple[str, list[ModelCapability]]] = field(default_factory=list)
    # AJUSTÉ Phase 11 (consigne §5, Root Causes) : 4 suffisait pour prouver le
    # pipeline (Phase 3) mais coupait artificiellement tôt un scénario de
    # Computer Use réel à plusieurs étapes (ex: naviguer -> observer -> choisir
    # un profil -> observer -> choisir un contenu -> observer -> lire = 7
    # tool calls rien que pour Netflix). 12 laisse une marge raisonnable
    # (~2x le besoin observé) sans devenir un budget "illimité" arbitraire —
    # la protection contre les boucles/répétitions reste `LoopDetector`
    # (ESCALATE après 2 échecs identiques), inchangé, jamais désactivé par ce
    # réglage. Un travail réellement long reste du ressort de tasks.create
    # (Phase 10, non borné par cette valeur — voir harness/loop.py
    # ::_run_long_horizon_step, un tick = un appel modèle, jamais cette boucle).
    max_tool_iterations: int = 12
    tool_workspace_dir: Path = field(default_factory=lambda: Path("."))
    context_budget_tokens: int = 4096
    world_state_default_ttl_s: int | None = None
    memory_search_limit: int = 20
    max_concurrent_tasks: int = 2
    enable_windows_device: bool = True
    enable_browser_device: bool = True
    # Chantier 13 (Phone Integration MVP) : Device Agent pilotant Microsoft
    # Phone Link via UI Automation — se dégrade honnêtement (absent du
    # DeviceRegistry) si Phone Link n'est pas installé/accessible, jamais un
    # crash. Activé par défaut comme windows/browser (même politique).
    enable_phone_device: bool = True
    device_screenshot_dir: Path = field(default_factory=lambda: Path("."))
    # Phase 6 : voix opt-in pour le Cockpit web — reste désactivée par défaut
    # (facteur.py documente déjà pourquoi : matériel audio réel + modèles
    # Whisper/Kokoro lourds, jamais chargés sans demande explicite).
    enable_voice: bool = False
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    # Phase 7 : capteur léger actif par défaut (RAYA_V2_TECHNICAL_ARCHITECTURE.md
    # §11.1 — "capteurs légers autorisés en continu", contrairement à la voix
    # qui charge des modèles lourds) ; se dégrade honnêtement (aucun event
    # publié) si pywin32/la plateforme ne le permet pas, jamais un crash.
    enable_perception: bool = True
    perception_poll_interval_s: float = 3.0
    # Phase 9 : Telegram — interface opt-in (RAYA_ENABLE_TELEGRAM), désactivée
    # par défaut. Token JAMAIS loggé/committé — lu depuis l'environnement
    # uniquement, jamais une valeur par défaut en dur (consigne §4). Liste
    # d'IDs Telegram numériques autorisés — vide par défaut, FAIL CLOSED
    # (personne n'est autorisé tant qu'elle n'est pas explicitement remplie,
    # consigne §8 : un utilisateur non autorisé ne doit jamais contrôler le PC).
    enable_telegram: bool = False
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: tuple[int, ...] = field(default_factory=tuple)
    telegram_poll_timeout_s: int = 25
    # Chantier 12 §A (Temporal) : fuseau utilisateur pour system.time.now et
    # la résolution `tasks.create(run_at=...)` — Europe/Brussels par défaut,
    # jamais deviné/recalculé ailleurs dans le code.
    timezone: str = DEFAULT_TIMEZONE
    env: dict[str, str] = field(default_factory=dict)


def load_config(root: Path | None = None) -> RuntimeConfig:
    root = root or Path(__file__).resolve().parents[2]
    file_values = _load_dotenv(root / ".env")
    merged = {**file_values, **os.environ}
    data_dir = Path(merged.get("RAYA_DATA_DIR", str(root / "data")))
    db_path = Path(merged.get("RAYA_DB_PATH", str(data_dir / "raya_v2.sqlite3")))
    workspace_dir = Path(merged.get("RAYA_TOOL_WORKSPACE_DIR", str(data_dir / "workspace")))
    return RuntimeConfig(
        root=root,
        data_dir=data_dir,
        db_path=db_path,
        ollama_api_key=merged.get("OLLAMA_API_KEY") or None,
        ollama_host=merged.get("RAYA_OLLAMA_HOST", "https://ollama.com"),
        ollama_local_host=merged.get("RAYA_OLLAMA_LOCAL_HOST", "http://localhost:11434"),
        enable_ollama_local=merged.get("RAYA_ENABLE_OLLAMA_LOCAL", "false").lower() in ("1", "true", "yes"),
        model_pool=parse_model_pool(merged.get("RAYA_MODEL_POOL", _DEFAULT_MODEL_POOL)),
        local_model_pool=parse_model_pool(merged.get("RAYA_LOCAL_MODEL_POOL", "")),
        max_tool_iterations=int(merged.get("RAYA_MAX_TOOL_ITERATIONS", "12")),
        tool_workspace_dir=workspace_dir,
        context_budget_tokens=int(merged.get("RAYA_CONTEXT_BUDGET_TOKENS", "4096")),
        world_state_default_ttl_s=(
            int(merged["RAYA_WORLD_STATE_TTL_S"]) if merged.get("RAYA_WORLD_STATE_TTL_S") else None
        ),
        memory_search_limit=int(merged.get("RAYA_MEMORY_SEARCH_LIMIT", "20")),
        max_concurrent_tasks=int(merged.get("RAYA_MAX_CONCURRENT_TASKS", "2")),
        enable_windows_device=merged.get("RAYA_ENABLE_WINDOWS_DEVICE", "true").lower() in ("1", "true", "yes"),
        enable_browser_device=merged.get("RAYA_ENABLE_BROWSER_DEVICE", "true").lower() in ("1", "true", "yes"),
        enable_phone_device=merged.get("RAYA_ENABLE_PHONE_DEVICE", "true").lower() in ("1", "true", "yes"),
        device_screenshot_dir=Path(merged.get("RAYA_DEVICE_SCREENSHOT_DIR", str(data_dir / "screenshots"))),
        enable_voice=merged.get("RAYA_ENABLE_VOICE", "false").lower() in ("1", "true", "yes"),
        web_host=merged.get("RAYA_WEB_HOST", "127.0.0.1"),
        web_port=int(merged.get("RAYA_WEB_PORT", "8765")),
        enable_perception=merged.get("RAYA_ENABLE_PERCEPTION", "true").lower() in ("1", "true", "yes"),
        perception_poll_interval_s=float(merged.get("RAYA_PERCEPTION_POLL_INTERVAL_S", "3.0")),
        enable_telegram=merged.get("RAYA_TELEGRAM_ENABLED", "false").lower() in ("1", "true", "yes"),
        telegram_bot_token=merged.get("RAYA_TELEGRAM_BOT_TOKEN") or None,
        telegram_allowed_user_ids=_parse_int_list(merged.get("RAYA_TELEGRAM_ALLOWED_USER_IDS", "")),
        telegram_poll_timeout_s=int(merged.get("RAYA_TELEGRAM_POLL_TIMEOUT_S", "25")),
        timezone=merged.get("RAYA_TIMEZONE", DEFAULT_TIMEZONE),
        env=merged,
    )
