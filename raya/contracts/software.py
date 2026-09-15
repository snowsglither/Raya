"""Software Environment contracts — LaunchMethod, InstalledApp,
SoftwareEnvironmentSnapshot, DiscoveredCapability.

These are pure data contracts. No business logic, no preferences,
no application-specific knowledge."""

from __future__ import annotations
from dataclasses import dataclass, field
from raya.contracts._base import utc_now_iso
from raya.contracts.world_state import Confidence


@dataclass
class LaunchMethod:
    """A single, technically observable way to launch an application.

    This is NOT a preference decision. It represents what is technically available.
    The model/Cognition chooses among available methods."""
    type: str           # "exe", "url_protocol", "uwp", "lnk", "cli"
    value: str          # actual path / URL / AppID / command
    launcher: str       # "direct", "steam", "epic", "gog", "ubisoft", "uwp", "shell", "unknown"
    verified: bool      # os.path.exists() confirmed for exe/lnk, or launcher detected for url_protocol
    launcher_available: bool   # launcher itself is installed (registry/PATH check)
    launcher_running: bool     # launcher process is currently active (psutil check)
    requires_launcher: bool    # True if url_protocol type (needs launcher to be open)
    source: str         # which discovery source produced this method


@dataclass
class InstalledApp:
    """An application observed as installed on this system.

    Contains only what was OBSERVED, not what the model guesses."""
    name: str                              # normalized (lowercase, stripped)
    display_name: str                      # original display name from source
    launch_methods: list[LaunchMethod] = field(default_factory=list)
    version: str | None = None
    source: str = ""  # "app_paths", "start_menu", "steam", "epic", "gog", "ubisoft", "uwp", "registry_uninstall", "path"
    discovered_at: str = field(default_factory=utc_now_iso)
    confidence: Confidence = Confidence.INFERRED


@dataclass
class SoftwareEnvironmentSnapshot:
    """Result of a discovery scan — what was observed at a point in time."""
    apps: list[InstalledApp] = field(default_factory=list)
    scanned_at: str = field(default_factory=utc_now_iso)
    sources_used: list[str] = field(default_factory=list)
    hostname: str = ""
    scan_level: int = 1  # 1=fast, 2=complete


@dataclass
class DiscoveredCapability:
    """A capability that was probed and confirmed (or not) on this system.

    CRITICAL: confirmed=True ONLY when a real probe produced real evidence.
    A model's general knowledge produces confirmed=False, confidence=HYPOTHESIS."""
    id: str                      # "cli", "gui", "python_api", "rest_api", "com", "dde"
    interface: str               # "cli", "gui", "python", "rest", "com", "dde", "unknown"
    executable: str | None       # real verified path or None
    confirmed: bool              # True ONLY if verified by real probe
    confidence: Confidence       # KNOWN_FACT only if confirmed=True with real evidence
    verification_method: str     # "path_check", "version_probe", "model_hypothesis"
    probe_command: str | None = None   # command used if version_probe
    probe_stdout: str | None = None    # stdout output of probe if any
    probe_exit_code: int | None = None
    provenance: str = ""         # what produced this (discovery source, user input, model)
    notes: str | None = None
