"""Analisi opzionale di una cartella progetto per arricchire il contesto AI."""
from __future__ import annotations

from pathlib import Path

MAX_CHARS = 12000
MAX_TREE_ENTRIES = 220
MAX_README_CHARS = 4000

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", ".next", ".nuxt", "target", "out", ".idea",
    ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
    "vendor", ".gradle", ".terraform", "bin", "obj", "packages",
}

# file alla radice che vale la pena leggere (in ordine di priorità)
INFO_FILES = [
    "README.md", "README.txt", "README", "AGENTS.md", "CLAUDE.md",
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml",
    "composer.json", "*.sln", "Makefile", "docker-compose.yml",
]

# estensioni testuali leggibili come anteprima
TEXT_SUFFIXES = {
    ".md", ".txt", ".rst", ".toml", ".json", ".yaml", ".yml", ".ini",
    ".cfg", ".gradle", ".mod",
}


def _read_text(path: Path, limit: int) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    data = data.strip()
    if len(data) > limit:
        data = data[:limit] + "\n…(troncato)"
    return data


def _tree_lines(folder: Path) -> list[str]:
    lines: list[str] = []
    count = 0

    def walk(dir_path: Path, depth: int) -> None:
        nonlocal count
        if count >= MAX_TREE_ENTRIES or depth > 3:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return
        for entry in entries:
            if count >= MAX_TREE_ENTRIES:
                lines.append("  … (altri file omessi)")
                return
            if entry.is_dir():
                if entry.name.lower() in SKIP_DIRS or entry.name.startswith("."):
                    continue
                lines.append("  " * depth + f"{entry.name}/")
                count += 1
                walk(entry, depth + 1)
            else:
                lines.append("  " * depth + entry.name)
                count += 1

    walk(folder, 0)
    return lines


def build_project_context(folder: Path, extra_notes: str = "") -> str:
    """Restituisce un digest del progetto da iniettare nel prompt (≤ MAX_CHARS)."""
    folder = Path(folder)
    parts: list[str] = []

    if extra_notes.strip():
        parts.append("NOTE DELL'UTENTE:\n" + extra_notes.strip())

    if not folder.is_dir():
        parts.append(f"(cartella progetto non trovata: {folder})")
        return "\n\n".join(parts)

    parts.append(f"CARTELLA PROGETTO: {folder}")

    # file informativi principali
    read: list[str] = []
    for pattern in INFO_FILES:
        for path in sorted(folder.glob(pattern)):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            content = _read_text(path, MAX_README_CHARS)
            if content:
                read.append(f"--- {path.name} ---\n{content}")
            if len(read) >= 4:
                break
        if len(read) >= 4:
            break
    if read:
        parts.append("FILE PRINCIPALI:\n" + "\n\n".join(read))

    # struttura alberale
    tree = _tree_lines(folder)
    if tree:
        parts.append("STRUTTURA:\n" + "\n".join(tree))

    result = "\n\n".join(parts)
    if len(result) > MAX_CHARS:
        result = result[:MAX_CHARS] + "\n…(contesto troncato)"
    return result


def context_placeholder() -> str:
    return "nessuno"
