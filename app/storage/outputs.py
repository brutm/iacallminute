"""Scrittura di trascrizione, riassunto e minute (.md e .docx)."""
from __future__ import annotations

import re
import time
from pathlib import Path


def stamp_now() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def format_ts(seconds: float) -> str:
    s = int(max(0, seconds))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def display_date() -> str:
    return time.strftime("%d/%m/%Y %H:%M")


def build_transcript_md(results: list[dict], duration: float) -> str:
    lines = [
        "# Trascrizione della chiamata",
        "",
        f"- **Data:** {display_date()}",
        f"- **Durata trascritta:** {format_ts(duration)}",
        "",
        "---",
        "",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"**[{format_ts(r['start'])}]** *errore: {r['error']}*")
            lines.append("")
            continue
        italian = (r.get("italian") or "").strip()
        original = (r.get("original") or "").strip()
        if not italian and not original:
            continue
        lang = (r.get("language") or "").strip() or "?"
        badge = "" if lang.lower().startswith("it") else f" `{lang}`"
        if not italian:
            lines.append(f"**[{format_ts(r['start'])}]**{badge} {original}")
            lines.append("")
            continue
        lines.append(f"**[{format_ts(r['start'])}]**{badge} {italian}")
        original = (r.get("original") or "").strip()
        if r.get("bilingual") and original and original != italian:
            lines.append(f"> originale ({lang}): {original}")
        lines.append("")
    return "\n".join(lines) + "\n"


def build_transcript_plain(results: list[dict]) -> str:
    """Trascrizione formattata per i prompt AI."""
    parts = []
    for r in results:
        if r.get("error"):
            continue
        italian = (r.get("italian") or "").strip()
        original = (r.get("original") or "").strip()
        if not italian:
            if original:
                parts.append(f"[{format_ts(r['start'])}] {original}")
            continue
        if r.get("bilingual") and original and original != italian:
            parts.append(f"[{format_ts(r['start'])}] (originale) {original}")
            parts.append(f"[{format_ts(r['start'])}] (IT) {italian}")
        else:
            parts.append(f"[{format_ts(r['start'])}] {italian}")
    return "\n".join(parts)


def write_text(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Markdown -> DOCX (parser minimale, sufficiente per i nostri output)
# --------------------------------------------------------------------------- #
def markdown_to_docx(md_text: str, path: Path) -> Path:
    from docx import Document

    doc = Document()
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        stripped = stripped.replace("`", "")
        if line.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif line.startswith("> "):
            doc.add_paragraph(stripped[2:], style="Intense Quote"
                              if "Intense Quote" in [s.name for s in doc.styles]
                              else None)
        elif re.match(r"^[-*] ", stripped):
            doc.add_paragraph(stripped[2:], style="List Bullet")
        elif re.match(r"^\d+\. ", stripped):
            doc.add_paragraph(re.sub(r"^\d+\. ", "", stripped), style="List Number")
        elif set(stripped) <= {"-", " "} and len(stripped) >= 3:
            continue  # riga separatore
        else:
            doc.add_paragraph(stripped)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path
