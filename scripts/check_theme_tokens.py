#!/usr/bin/env python3
"""Fail when a color bypasses the design tokens.

Every color in the desktop UI must resolve through an ``--obd-*`` token so the
light and dark themes stay in sync. Palette utilities (``text-slate-500``),
arbitrary values (``bg-[#1f2833]``), and raw hex in component stylesheets are
rejected outside the token blocks in ``styles.css`` and the print stylesheet,
which intentionally targets white paper.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

UI_SRC = Path("apps/desktop/ui/src")
STYLES = UI_SRC / "styles.css"

# Tailwind palette utilities, e.g. text-slate-500, bg-white/10, border-black/30.
PALETTE_RE = re.compile(
    r"\b(?:text|bg|border|ring|divide|placeholder|from|to|via|outline|shadow|"
    r"fill|stroke|decoration|accent|caret)-(?:slate|gray|zinc|neutral|stone|"
    r"emerald|green|teal|amber|yellow|orange|rose|red|pink|sky|blue|cyan|"
    r"indigo|violet|purple|fuchsia|white|black)\b"
)
# Arbitrary color values, e.g. bg-[#1f2833].
ARBITRARY_RE = re.compile(r"-\[#[0-9a-fA-F]{3,8}\]")

# Shadow tint is allowed: a drop shadow is an optical darkening and stays dark
# in both themes, unlike surfaces and text which must follow the tokens.
ALLOWED = {('shadow', 'black')}

# Raw colors are allowed only between these markers in styles.css.
TOKEN_BLOCK_START = "/* Design tokens."
PRINT_BLOCK_START = "@media print {"
TAIL_MARKER = "/* Keep the project tab strip"


def check_components() -> list[str]:
    problems: list[str] = []
    for path in sorted(UI_SRC.glob("*.ts*")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            for match in PALETTE_RE.finditer(line):
                family = match.group(0).rsplit('-', 1)[0]
                if (family, match.group(0).rsplit('-', 1)[1]) in ALLOWED:
                    continue
                problems.append(f"{path}:{number}: palette utility {match.group(0)}")
            for match in ARBITRARY_RE.finditer(line):
                problems.append(f"{path}:{number}: literal color {match.group(0)}")
    return problems


def check_stylesheet() -> list[str]:
    if not STYLES.exists():
        return [f"{STYLES}: missing"]
    source = STYLES.read_text()
    head_end = source.find("html,\nbody,")
    if head_end == -1:
        return [f"{STYLES}: could not locate the token block"]
    print_start = source.find(PRINT_BLOCK_START)
    tail_start = source.find(TAIL_MARKER)
    if print_start == -1 or tail_start == -1:
        return [f"{STYLES}: could not locate the print or scrollbar block"]

    problems: list[str] = []
    themed = [(1, source[head_end:print_start]), (1, source[tail_start:])]
    for base, chunk in themed:
        for number, line in enumerate(chunk.splitlines(), start=base):
            if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", line):
                problems.append(f"{STYLES}: {line.strip()}")
    return problems


def main() -> int:
    problems = check_components() + check_stylesheet()
    if problems:
        print("Colors that bypass the --obd-* tokens:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nUse a token-backed utility (text-obd-muted, bg-obd-surface, "
            "border-obd-line) or add a token to styles.css.",
            file=sys.stderr,
        )
        return 1
    print("OK: every UI color resolves through an --obd-* token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
