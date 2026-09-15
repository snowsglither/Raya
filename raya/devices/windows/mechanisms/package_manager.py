"""Read-only winget package manager wrapper.

Only list/search/show operations are exposed in Chantier 1.
install/uninstall/update are intentionally NOT implemented here —
they require UAC elevation handling (Chantier 2)."""

from __future__ import annotations
import re
import subprocess

_WINGET = "winget"
_TIMEOUT_LIST = 30
_TIMEOUT_SEARCH = 30
_TIMEOUT_SHOW = 20
_MAX_OUTPUT_CHARS = 6000


def winget_available() -> bool:
    try:
        r = subprocess.run(
            [_WINGET, "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0
    except Exception:
        return False


def _run(args: list[str], timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [_WINGET] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = ((r.stdout or "") + (r.stderr or ""))[:_MAX_OUTPUT_CHARS]
        return r.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"winget timeout ({timeout}s)"
    except FileNotFoundError:
        return False, "winget not found"
    except Exception as exc:
        return False, str(exc)


def _parse_table(output: str) -> list[dict[str, str]]:
    lines = output.splitlines()
    header_idx = sep_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and len(stripped) > 5 and stripped.replace("-", "").replace(" ", "") == "":
            sep_idx = i
            header_idx = i - 1
            break
    if header_idx is None or sep_idx is None or header_idx < 0:
        return []
    header = lines[header_idx]
    col_starts: list[int] = []
    for m in re.finditer(r"(?:^|\s{2,})(\S)", header):
        col_starts.append(m.start(1))
    if not col_starts:
        return []
    col_ends = col_starts[1:] + [None]

    def split_row(line: str) -> list[str]:
        return [(line[s:e] if e else line[s:]).strip() for s, e in zip(col_starts, col_ends)]

    col_names = [n.lower() for n in split_row(header)]
    rows = []
    for line in lines[sep_idx + 1:]:
        if not line.strip():
            continue
        low = line.strip().lower()
        if low.startswith("--") or "résultat" in low or "result" in low:
            break
        parts = split_row(line)
        row = {col_names[j]: (parts[j] if j < len(parts) else "") for j in range(len(col_names))}
        if any(row.values()):
            rows.append(row)
    return rows


def list_installed(query: str = "", max_results: int = 50) -> dict:
    """winget list — read-only."""
    if not winget_available():
        return {"status": "unavailable", "error": "winget not found", "packages": []}
    args = ["list", "--accept-source-agreements", "--disable-interactivity"]
    if query:
        args += ["--query", query]
    ok, output = _run(args, _TIMEOUT_LIST)
    if not ok and "winget" in output.lower():
        return {"status": "unavailable", "error": output, "packages": []}
    rows = _parse_table(output)
    packages = []
    for row in rows[:max_results]:
        name = row.get("nom") or row.get("name") or ""
        if not name:
            continue
        packages.append({
            "name": name,
            "id": row.get("id", ""),
            "version": row.get("version", ""),
            "available": row.get("version disponible") or row.get("available", ""),
        })
    return {"status": "ok", "count": len(packages), "packages": packages}


def search(query: str, max_results: int = 10) -> dict:
    """winget search — read-only."""
    if not query:
        return {"status": "error", "error": "query required", "packages": []}
    if not winget_available():
        return {"status": "unavailable", "error": "winget not found", "packages": []}
    ok, output = _run(
        ["search", query, "--accept-source-agreements", "--disable-interactivity"],
        _TIMEOUT_SEARCH,
    )
    rows = _parse_table(output)
    packages = []
    for row in rows[:max_results]:
        name = row.get("nom") or row.get("name") or ""
        if not name:
            continue
        packages.append({
            "name": name,
            "id": row.get("id", ""),
            "version": row.get("version", ""),
            "source": row.get("source", ""),
        })
    return {"status": "ok", "count": len(packages), "packages": packages}


def show(package_id: str) -> dict:
    """winget show — read-only package metadata."""
    if not package_id:
        return {"status": "error", "error": "package_id required"}
    if not winget_available():
        return {"status": "unavailable", "error": "winget not found"}
    ok, output = _run(
        ["show", package_id, "--accept-source-agreements", "--disable-interactivity"],
        _TIMEOUT_SHOW,
    )
    info: dict = {"id": package_id}
    for line in output.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            k = key.strip().lower().replace(" ", "_")
            if k and val.strip():
                info[k] = val.strip()
    return {"status": "ok", "package": info}
