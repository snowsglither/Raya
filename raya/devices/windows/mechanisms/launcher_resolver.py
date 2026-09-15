"""Technical resolution of launch options for a given application.

This module returns available LaunchMethods with objective properties.
It does NOT decide which method is preferred — that is Cognition's responsibility.

Properties returned are objectively observable:
- is the method technically available?
- is the launcher installed?
- is the launcher currently running?
- what type of interface does it use?
No business preference (Steam > Epic) is encoded here."""

from __future__ import annotations
import os
import shutil

try:
    import winreg
except ImportError:
    winreg = None

from raya.contracts.software import InstalledApp, LaunchMethod
from . import software_discovery as _disc


def resolve_launch_options(name: str, apps: list[InstalledApp] | None = None) -> list[LaunchMethod]:
    """Return all technically available launch methods for the given app name.

    Enriches each method with real-time properties (launcher_running, re-verified).
    Returns empty list if app not found — never invents a method."""
    matches = _disc.find_installed(name, apps)
    if not matches:
        return []
    # Use the best match (highest confidence, most launch methods)
    best = max(matches, key=lambda a: (
        {"known_fact": 2, "inferred": 1, "hypothesis": 0}.get(a.confidence, 0),
        len(a.launch_methods),
    ))
    return [_enrich(m) for m in best.launch_methods]


def _enrich(method: LaunchMethod) -> LaunchMethod:
    """Re-check real-time properties: launcher_running, verify exe still exists."""
    running = _disc._launcher_running(method.launcher) if method.requires_launcher else False
    # Re-verify exe/lnk still exists (could have changed since discovery)
    verified = method.verified
    if method.type in ("exe", "lnk") and method.value:
        verified = os.path.isfile(method.value)
    # Re-check launcher available (could have changed)
    launcher_available = method.launcher_available
    if method.launcher in ("steam", "epic", "gog", "ubisoft") and winreg is not None:
        _known_launcher_regs = {
            "steam": [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")],
            "epic":  [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher")],
            "gog":   [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient")],
            "ubisoft": [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher")],
        }
        reg_paths = _known_launcher_regs.get(method.launcher, [])
        launcher_available = _disc._check_launcher_present(method.launcher, reg_paths) if reg_paths else method.launcher_available
    return LaunchMethod(
        type=method.type, value=method.value, launcher=method.launcher,
        verified=verified, launcher_available=launcher_available,
        launcher_running=running, requires_launcher=method.requires_launcher,
        source=method.source,
    )


def probe_path(path: str) -> dict:
    """Probe a given explicit path — SAFE (no execution).

    Inspects the file at the given path and returns observable properties.
    Does NOT execute the file. For CLI probing (--version), use pc.shell.execute."""
    from pathlib import Path
    if not path:
        return {"status": "error", "error": "path required"}
    p = Path(path)
    if not p.exists():
        return {"status": "not_found", "path": path, "exists": False}
    if not p.is_file():
        return {"status": "not_a_file", "path": path, "exists": True, "is_dir": p.is_dir()}
    try:
        stat = p.stat()
        ext = p.suffix.lower()
        is_executable = ext in {".exe", ".bat", ".cmd", ".com", ".ps1", ".py", ".sh"}
        # Check if also on PATH
        on_path = shutil.which(p.name) is not None
        return {
            "status": "ok",
            "path": str(p.resolve()),
            "exists": True,
            "is_file": True,
            "name": p.name,
            "stem": p.stem,
            "extension": ext,
            "size_bytes": stat.st_size,
            "is_executable_type": is_executable,
            "on_path": on_path,
        }
    except OSError as exc:
        return {"status": "error", "path": path, "error": str(exc)}
