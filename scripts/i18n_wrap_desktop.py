"""One-off helper: wrap the desktop's literal UI strings in _("…").

Targets the Qt calls that take user-visible text. f-strings and strings built
by concatenation are left for a person.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CALLS = (
    "QLabel", "QPushButton", "QCheckBox", "QAction", "setWindowTitle", "setToolTip",
    "setPlaceholderText", "setText", "addMenu", "setTitle",
)
CALL = re.compile(r"\b(" + "|".join(CALLS) + r')\(\s*"([A-Z][^"{}\\]*)"')
ADDROW = re.compile(r'\.addRow\(\s*"([A-Za-z][^"{}\\]*)"')
ADDITEM = re.compile(r'\.addItem\(\s*"([A-Z][^"{}\\]*)"')
LISTS = re.compile(r"(setHeaderLabels|setHorizontalHeaderLabels)\(\s*\[([^\]]*)\]")
STR = re.compile(r'"([A-Z][^"{}\\]*)"')


def wrap(source: str) -> str:
    out = CALL.sub(lambda m: f'{m.group(1)}(_("{m.group(2)}")', source)
    out = ADDROW.sub(lambda m: f'.addRow(_("{m.group(1)}")', out)
    out = ADDITEM.sub(lambda m: f'.addItem(_("{m.group(1)}")', out)
    out = LISTS.sub(lambda m: f"{m.group(1)}([{STR.sub(lambda s: f'_({s.group(0)})', m.group(2))}]", out)
    if out != source and "from ..i18n import _" not in out:
        out = re.sub(r"(\nfrom \.\.\.api\.schemas import)", r"\nfrom ..i18n import _\1", out, count=1)
    return out


for path in sorted(Path(sys.argv[1]).glob("*.py")):
    before = path.read_text()
    after = wrap(before)
    if after != before:
        path.write_text(after)
        print("wrapped", path)
