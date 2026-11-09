"""Rendering leggero di Markdown in un tk.Text (senza dipendenze esterne)."""
from __future__ import annotations

import re
import tkinter as tk

_INLINE_RE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<bi>\*\*\*[^*\n]+\*\*\*)"
    r"|(?P<strong>\*\*[^*\n]+\*\*)"
    r"|(?P<em>\*[^*\n]+\*|_[^_\n]+_)"
)
_BULLET_RE = re.compile(r"^(?P<mark>[-*+]|\d+\.)\s+(?P<txt>.*)$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")


def setup_tags(w: tk.Text) -> None:
    """Configura i tag usati da insert_markdown()."""
    w.tag_configure("h1", font=("Segoe UI Semibold", 15),
                    foreground="#0f172a", spacing1=8, spacing3=4)
    w.tag_configure("h2", font=("Segoe UI Semibold", 13),
                    foreground="#1d4ed8", spacing1=7, spacing3=3)
    w.tag_configure("h3", font=("Segoe UI Semibold", 11),
                    foreground="#2563eb", spacing1=5, spacing3=2)
    w.tag_configure("strong", font=("Segoe UI Semibold", 11))
    w.tag_configure("em", font=("Segoe UI", 11, "italic"))
    w.tag_configure("code", font=("Consolas", 10),
                    background="#eef2f7", foreground="#b45309")
    w.tag_configure("codeblock", font=("Consolas", 9),
                    background="#f1f5f9", lmargin1=16, lmargin2=16)
    w.tag_configure("quote", font=("Segoe UI", 10, "italic"),
                    foreground="#64748b", lmargin1=18, lmargin2=26)
    w.tag_configure("li", lmargin1=16, lmargin2=32,
                    font=("Segoe UI", 11))
    w.tag_configure("tbl", font=("Consolas", 9),
                    foreground="#334155", lmargin1=8)
    w.tag_configure("hr", foreground="#cbd5e1", font=("Consolas", 10))


def _inline(w: tk.Text, line: str, base: tuple) -> None:
    pos = 0
    for m in _INLINE_RE.finditer(line):
        if m.start() > pos:
            w.insert("end", line[pos:m.start()], base)
        tok = m.group(0)
        if m.lastgroup == "code":
            w.insert("end", tok[1:-1], tuple(base) + ("code",))
        elif m.lastgroup == "bi":
            w.insert("end", tok[3:-3], tuple(base) + ("strong", "em"))
        elif m.lastgroup == "strong":
            w.insert("end", tok[2:-2], tuple(base) + ("strong",))
        else:
            w.insert("end", tok[1:-1], tuple(base) + ("em",))
        pos = m.end()
    if pos < len(line):
        w.insert("end", line[pos:], base)


def insert_markdown(w: tk.Text, md: str) -> None:
    """Svuota (parzialmente) e inserisce il markdown formattato in w.

    Il widget deve essere in stato "normal"; il chiamante torna in
    "disabled" alla fine.
    """
    in_fence = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            w.insert("end", "\n")
            continue
        if in_fence:
            w.insert("end", (line or " ") + "\n", ("codeblock",))
            continue
        if not line.strip():
            w.insert("end", "\n")
            continue
        if _HR_RE.match(line):
            w.insert("end", "─" * 46 + "\n", ("hr",))
            continue
        h = _H_RE.match(line)
        if h:
            lvl = min(len(h.group(1)), 3)
            _inline(w, h.group(2), (f"h{lvl}",))
            w.insert("end", "\n")
            continue
        if _TABLE_RE.match(line):
            if _TABLE_SEP_RE.match(line):
                continue
            w.insert("end", line + "\n", ("tbl",))
            continue
        q = _QUOTE_RE.match(line)
        if q:
            _inline(w, q.group(1), ("quote",))
            w.insert("end", "\n")
            continue
        b = _BULLET_RE.match(line)
        if b:
            mark = b.group("mark")
            head = f"{mark} " if mark.endswith(".") else "• "
            w.insert("end", "   " + head, ("li",))
            _inline(w, b.group("txt"), ("li",))
            w.insert("end", "\n")
            continue
        _inline(w, line, ())
        w.insert("end", "\n")
