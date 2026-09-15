"""Multi-source software discovery for Windows.

Level 1 (fast, <200ms): App Paths registry, PATH executables, launcher presence.
Level 2 (complete, 500ms-2s): Start Menu, UWP, Registry Uninstall, launcher manifests.

Discovery produces observations — never invents applications.
All results carry source, confidence, and timestamp."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None

from raya.contracts.software import InstalledApp, LaunchMethod, SoftwareEnvironmentSnapshot
from raya.contracts.world_state import Confidence

_CACHE: dict[int, tuple[SoftwareEnvironmentSnapshot, float]] = {}
_CACHE_TTL_S = 3600.0

_NOISE_NAMES = frozenset({
    "uninstall", "readme", "help", "support", "release notes", "manual",
    "website", "documentation", "changelog", "license", "redistributable",
    "redistributables", "vcredist", "directx", "runtime",
})


def _normalize(name: str) -> str:
    return (name or "").lower().strip()


def _is_noise(name: str) -> bool:
    n = _normalize(name)
    return not n or any(noise in n for noise in _NOISE_NAMES)


def _launcher_running(launcher_name: str) -> bool:
    """Check if a launcher process is currently running — generic via psutil."""
    try:
        import psutil
        _known_procs = {
            "steam": {"steam.exe"},
            "epic": {"epicgameslauncher.exe"},
            "gog": {"galaxyclient.exe"},
            "ubisoft": {"ubisoft connect.exe", "ubisoftconnect.exe"},
        }
        targets = _known_procs.get(launcher_name.lower(), {launcher_name.lower() + ".exe"})
        running = {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
        return bool(targets & running)
    except Exception:
        return False


def _make_launch_method(
    type_: str, value: str, launcher: str, source: str,
    verified: bool = False,
    launcher_available: bool = True,
) -> LaunchMethod:
    requires_launcher = type_ == "url_protocol"
    running = _launcher_running(launcher) if requires_launcher else False
    return LaunchMethod(
        type=type_, value=value, launcher=launcher, verified=verified,
        launcher_available=launcher_available, launcher_running=running,
        requires_launcher=requires_launcher, source=source,
    )


# ─────────────────────────────────────────────────────────────
# Level 1 — Fast sources
# ─────────────────────────────────────────────────────────────

def _scan_app_paths() -> list[InstalledApp]:
    """App Paths registry: HKLM + HKCU SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths."""
    if winreg is None:
        return []
    results: list[InstalledApp] = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, base) as root:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, i)
                        i += 1
                        try:
                            with winreg.OpenKey(root, subkey_name) as k:
                                path = winreg.QueryValue(k, None)
                                if not path:
                                    continue
                                path = path.strip('"').strip()
                                exe_name = Path(subkey_name).stem
                                if _is_noise(exe_name):
                                    continue
                                verified = os.path.isfile(path)
                                method = _make_launch_method(
                                    "exe", path, "direct", "app_paths", verified=verified
                                )
                                results.append(InstalledApp(
                                    name=_normalize(exe_name),
                                    display_name=exe_name,
                                    launch_methods=[method],
                                    source="app_paths",
                                    confidence=Confidence.KNOWN_FACT if verified else Confidence.INFERRED,
                                ))
                        except OSError:
                            continue
                    except OSError:
                        break
        except OSError:
            continue
    return results


def _scan_path_tools() -> list[InstalledApp]:
    """Detect executables available on PATH via shutil.which."""
    known_cli_tools = [
        "git", "python", "python3", "node", "npm", "pip", "pip3",
        "powershell", "cmd", "ffmpeg", "ffprobe", "curl", "wget",
        "nmap", "ping", "tracert", "nslookup", "ipconfig", "netsh",
        "code", "notepad", "calc", "mspaint", "explorer",
        "winget", "choco", "scoop",
    ]
    results: list[InstalledApp] = []
    for name in known_cli_tools:
        path = shutil.which(name)
        if path:
            method = _make_launch_method("exe", path, "direct", "path", verified=True)
            results.append(InstalledApp(
                name=_normalize(name),
                display_name=name,
                launch_methods=[method],
                source="path",
                confidence=Confidence.KNOWN_FACT,
            ))
    return results


def _check_launcher_present(launcher: str, registry_paths: list[tuple]) -> bool:
    """Generic launcher presence check via registry. registry_paths = list of (hive, subkey)."""
    if winreg is None:
        return False
    for hive, subkey in registry_paths:
        if hive is None:
            continue
        try:
            with winreg.OpenKey(hive, subkey):
                return True
        except OSError:
            continue
    return False


def scan_level1() -> SoftwareEnvironmentSnapshot:
    """Fast discovery: App Paths + PATH + launcher presence. <200ms typical."""
    apps: list[InstalledApp] = []
    sources: list[str] = []

    app_paths = _scan_app_paths()
    if app_paths:
        apps.extend(app_paths)
        sources.append("app_paths")

    path_tools = _scan_path_tools()
    if path_tools:
        apps.extend(path_tools)
        sources.append("path")

    merged = merge_apps(apps)
    return SoftwareEnvironmentSnapshot(
        apps=merged, sources_used=sources, hostname=socket.gethostname(), scan_level=1
    )


# ─────────────────────────────────────────────────────────────
# Level 2 — Complete sources
# ─────────────────────────────────────────────────────────────

def _scan_start_menu() -> list[InstalledApp]:
    """Start Menu .lnk shortcuts — ProgramData + AppData."""
    results: list[InstalledApp] = []
    dirs = [
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for d in dirs:
        if not d.exists():
            continue
        try:
            for lnk in d.rglob("*.lnk"):
                name = lnk.stem
                if _is_noise(name):
                    continue
                method = _make_launch_method("lnk", str(lnk), "direct", "start_menu", verified=lnk.exists())
                results.append(InstalledApp(
                    name=_normalize(name),
                    display_name=name,
                    launch_methods=[method],
                    source="start_menu",
                    confidence=Confidence.INFERRED,
                ))
        except (PermissionError, OSError):
            continue
    return results


def _scan_uwp() -> list[InstalledApp]:
    """UWP apps via PowerShell Get-StartApps."""
    results: list[InstalledApp] = []
    cmd = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-StartApps | Where-Object { $_.AppID -like '*!*' } "
        "| Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = proc.stdout.decode("utf-8-sig", errors="replace").strip()
        if not raw:
            return results
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        for item in data:
            name = (item.get("Name") or "").strip()
            app_id = (item.get("AppID") or "").strip()
            if not name or not app_id or _is_noise(name):
                continue
            method = _make_launch_method("uwp", app_id, "uwp", "uwp", verified=True, launcher_available=True)
            results.append(InstalledApp(
                name=_normalize(name),
                display_name=name,
                launch_methods=[method],
                source="uwp",
                confidence=Confidence.KNOWN_FACT,
            ))
    except Exception:
        pass
    return results


def _scan_registry_uninstall() -> list[InstalledApp]:
    """Registry Uninstall keys — HKLM + HKCU."""
    if winreg is None:
        return []
    results: list[InstalledApp] = []
    bases = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    seen: set[str] = set()
    for hive, base in bases:
        try:
            with winreg.OpenKey(hive, base) as root:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, i)
                        i += 1
                        try:
                            with winreg.OpenKey(root, subkey_name) as k:
                                try:
                                    display_name = winreg.QueryValueEx(k, "DisplayName")[0] or ""
                                except OSError:
                                    continue
                                if not display_name or _is_noise(display_name):
                                    continue
                                norm = _normalize(display_name)
                                if norm in seen:
                                    continue
                                seen.add(norm)
                                try:
                                    version = winreg.QueryValueEx(k, "DisplayVersion")[0] or None
                                except OSError:
                                    version = None
                                try:
                                    install_loc = winreg.QueryValueEx(k, "InstallLocation")[0] or ""
                                except OSError:
                                    install_loc = ""
                                # Find executable from InstallLocation
                                methods: list[LaunchMethod] = []
                                if install_loc and os.path.isdir(install_loc):
                                    # Try to find a main exe in install location (non-recursive, top level only)
                                    try:
                                        for entry in os.scandir(install_loc):
                                            if entry.name.lower().endswith(".exe") and entry.is_file():
                                                m = _make_launch_method("exe", entry.path, "direct", "registry_uninstall", verified=True)
                                                methods.append(m)
                                                break  # take first exe found — model reasons about which one
                                    except (PermissionError, OSError):
                                        pass
                                results.append(InstalledApp(
                                    name=norm,
                                    display_name=display_name,
                                    launch_methods=methods,
                                    version=version or None,
                                    source="registry_uninstall",
                                    confidence=Confidence.KNOWN_FACT if methods else Confidence.INFERRED,
                                ))
                        except OSError:
                            continue
                    except OSError:
                        break
        except OSError:
            continue
    return results


def _scan_launcher_manifests() -> list[InstalledApp]:
    """Discover apps registered by game launchers (Steam, Epic, GOG, Ubisoft).

    Each launcher stores its app list in a known location (registry or manifest files).
    This is generic: we read from wherever the launcher's data is, not app-specific logic."""
    if winreg is None:
        return []
    results: list[InstalledApp] = []
    results.extend(_scan_steam_library())
    results.extend(_scan_epic_manifests())
    results.extend(_scan_gog_registry())
    results.extend(_scan_ubisoft_registry())
    return results


def _scan_steam_library() -> list[InstalledApp]:
    """Read Steam library from registry + libraryfolders.vdf + appmanifest_*.acf."""
    if winreg is None:
        return []
    results: list[InstalledApp] = []
    steam_root: Path | None = None
    for reg_path in (r"SOFTWARE\WOW6432Node\Valve\Steam", r"SOFTWARE\Valve\Steam"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as k:
                steam_root = Path(winreg.QueryValueEx(k, "InstallPath")[0])
                break
        except OSError:
            continue
    if steam_root is None:
        return results

    # Check launcher_available
    steam_exe = steam_root / "steam.exe"
    launcher_available = steam_exe.exists()

    library_paths: list[Path] = [steam_root / "steamapps"]
    vdf = steam_root / "steamapps/libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = Path(m.group(1).replace("\\\\", "\\")) / "steamapps"
                if p.exists():
                    library_paths.append(p)
        except Exception:
            pass

    for lib in library_paths:
        try:
            for acf in lib.glob("appmanifest_*.acf"):
                try:
                    text = acf.read_text(encoding="utf-8", errors="ignore")
                    app_id_m = re.search(r'"appid"\s+"(\d+)"', text)
                    name_m = re.search(r'"name"\s+"([^"]+)"', text)
                    if not app_id_m or not name_m:
                        continue
                    name = name_m.group(1)
                    app_id = app_id_m.group(1)
                    if _is_noise(name):
                        continue
                    url = f"steam://rungameid/{app_id}"
                    method = _make_launch_method(
                        "url_protocol", url, "steam", "steam_manifests",
                        verified=True, launcher_available=launcher_available,
                    )
                    results.append(InstalledApp(
                        name=_normalize(name),
                        display_name=name,
                        launch_methods=[method],
                        source="steam_manifests",
                        confidence=Confidence.KNOWN_FACT,
                    ))
                except Exception:
                    continue
        except (PermissionError, OSError):
            continue
    return results


def _scan_epic_manifests() -> list[InstalledApp]:
    """Read Epic Games launcher manifests (*.item files)."""
    results: list[InstalledApp] = []
    manifests = (
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        / "Epic/EpicGamesLauncher/Data/Manifests"
    )
    if not manifests.exists():
        return results
    # Check launcher available
    epic_available = False
    if winreg is not None:
        epic_available = _check_launcher_present("epic", [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher"),
        ])

    try:
        for item in manifests.glob("*.item"):
            try:
                data = json.loads(item.read_text(encoding="utf-8"))
                name = data.get("DisplayName", "")
                app_name = data.get("AppName", "")
                if not name or not app_name or _is_noise(name):
                    continue
                url = f"com.epicgames.launcher://apps/{app_name}?action=launch&silent=true"
                method = _make_launch_method(
                    "url_protocol", url, "epic", "epic_manifests",
                    verified=True, launcher_available=epic_available,
                )
                results.append(InstalledApp(
                    name=_normalize(name),
                    display_name=name,
                    launch_methods=[method],
                    source="epic_manifests",
                    confidence=Confidence.KNOWN_FACT,
                ))
            except Exception:
                continue
    except (PermissionError, OSError):
        pass
    return results


def _scan_gog_registry() -> list[InstalledApp]:
    """GOG Galaxy game registry."""
    if winreg is None:
        return []
    results: list[InstalledApp] = []
    gog_available = _check_launcher_present("gog", [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient"),
    ])
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games") as root:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, i)
                    i += 1
                    try:
                        with winreg.OpenKey(root, subkey_name) as k:
                            name = winreg.QueryValueEx(k, "gameName")[0] or ""
                            exe = winreg.QueryValueEx(k, "exe")[0] or ""
                            if not name or _is_noise(name):
                                continue
                            verified = os.path.isfile(exe) if exe else False
                            method = _make_launch_method(
                                "exe", exe, "gog", "gog_registry",
                                verified=verified, launcher_available=gog_available,
                            )
                            results.append(InstalledApp(
                                name=_normalize(name),
                                display_name=name,
                                launch_methods=[method],
                                source="gog_registry",
                                confidence=Confidence.KNOWN_FACT if verified else Confidence.INFERRED,
                            ))
                    except OSError:
                        continue
                except OSError:
                    break
    except OSError:
        pass
    return results


def _scan_ubisoft_registry() -> list[InstalledApp]:
    """Ubisoft Connect/Uplay registry."""
    if winreg is None:
        return []
    results: list[InstalledApp] = []
    ubisoft_available = _check_launcher_present("ubisoft", [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"),
    ])
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs") as root:
            i = 0
            while True:
                try:
                    game_id = winreg.EnumKey(root, i)
                    i += 1
                    for uninstall_base in (
                        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    ):
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{uninstall_base}\\Uplay Install {game_id}") as uk:
                                name = winreg.QueryValueEx(uk, "DisplayName")[0] or ""
                                if not name or _is_noise(name):
                                    break
                                url = f"uplay://launch/{game_id}/0"
                                method = _make_launch_method(
                                    "url_protocol", url, "ubisoft", "ubisoft_registry",
                                    verified=True, launcher_available=ubisoft_available,
                                )
                                results.append(InstalledApp(
                                    name=_normalize(name),
                                    display_name=name,
                                    launch_methods=[method],
                                    source="ubisoft_registry",
                                    confidence=Confidence.KNOWN_FACT,
                                ))
                                break
                        except OSError:
                            continue
                except OSError:
                    break
    except OSError:
        pass
    return results


def scan_level2() -> SoftwareEnvironmentSnapshot:
    """Complete discovery: Level 1 + Start Menu + UWP + Registry Uninstall + Launcher manifests."""
    all_apps: list[InstalledApp] = []
    sources: list[str] = []

    # Include level 1 sources
    snap1 = scan_level1()
    all_apps.extend(snap1.apps)
    sources.extend(snap1.sources_used)

    start = _scan_start_menu()
    if start:
        all_apps.extend(start)
        sources.append("start_menu")

    uwp = _scan_uwp()
    if uwp:
        all_apps.extend(uwp)
        sources.append("uwp")

    uninstall = _scan_registry_uninstall()
    if uninstall:
        all_apps.extend(uninstall)
        sources.append("registry_uninstall")

    manifests = _scan_launcher_manifests()
    if manifests:
        all_apps.extend(manifests)
        sources.append("launcher_manifests")

    merged = merge_apps(all_apps)
    return SoftwareEnvironmentSnapshot(
        apps=merged, sources_used=list(dict.fromkeys(sources)),
        hostname=socket.gethostname(), scan_level=2,
    )


# ─────────────────────────────────────────────────────────────
# Merge / deduplication
# ─────────────────────────────────────────────────────────────

def _method_key(m: LaunchMethod) -> str:
    return f"{m.type}:{m.value}"


def merge_apps(app_lists: list[InstalledApp]) -> list[InstalledApp]:
    """Deduplicate by normalized name, merge launch methods.

    Higher confidence wins for version/metadata.
    All unique launch methods are preserved (model chooses)."""
    by_name: dict[str, InstalledApp] = {}
    for app in app_lists:
        key = app.name
        if key not in by_name:
            by_name[key] = InstalledApp(
                name=app.name, display_name=app.display_name,
                launch_methods=list(app.launch_methods),
                version=app.version, source=app.source,
                discovered_at=app.discovered_at, confidence=app.confidence,
            )
        else:
            existing = by_name[key]
            # Merge launch methods — keep unique ones
            existing_keys = {_method_key(m) for m in existing.launch_methods}
            for m in app.launch_methods:
                if _method_key(m) not in existing_keys:
                    existing.launch_methods.append(m)
                    existing_keys.add(_method_key(m))
            # Higher confidence wins
            _conf_rank = {Confidence.HYPOTHESIS: 0, Confidence.INFERRED: 1, Confidence.KNOWN_FACT: 2}
            if _conf_rank.get(app.confidence, 0) > _conf_rank.get(existing.confidence, 0):
                existing.confidence = app.confidence
                existing.display_name = app.display_name
            # Version: prefer non-None
            if existing.version is None and app.version:
                existing.version = app.version
    return list(by_name.values())


# ─────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────

def find_installed(name: str, apps: list[InstalledApp] | None = None) -> list[InstalledApp]:
    """Find installed apps matching name.

    If apps not provided, uses cached level1 snapshot.
    Returns empty list (never invents) if not found."""
    query = _normalize(name)
    if not query:
        return []
    if apps is None:
        snap = get_cached_or_scan(level=1)
        apps = snap.apps

    # 1. Exact match
    exact = [a for a in apps if a.name == query]
    if exact:
        return exact
    # 2. Prefix (query starts with app name or vice versa)
    prefix = [a for a in apps if a.name.startswith(query) or query.startswith(a.name)]
    if prefix:
        return sorted(prefix, key=lambda a: len(a.name))[:5]
    # 3. Contains (query in name or all words of query in name)
    words = query.split()
    contains = [a for a in apps if query in a.name or all(w in a.name for w in words)]
    if contains:
        return sorted(contains, key=lambda a: len(a.name))[:5]
    # 4. Fuzzy
    names = [a.name for a in apps]
    close = difflib.get_close_matches(query, names, n=5, cutoff=0.6)
    return [a for a in apps if a.name in close]


# ─────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────

def get_cached_or_scan(level: int = 1, refresh: bool = False) -> SoftwareEnvironmentSnapshot:
    """Return cached snapshot or scan fresh. Cache keyed by level, TTL=3600s."""
    now = time.monotonic()
    if not refresh and level in _CACHE:
        snap, ts = _CACHE[level]
        if now - ts < _CACHE_TTL_S:
            return snap
    snap = scan_level2() if level >= 2 else scan_level1()
    _CACHE[level] = (snap, now)
    return snap


def invalidate_cache() -> None:
    _CACHE.clear()
