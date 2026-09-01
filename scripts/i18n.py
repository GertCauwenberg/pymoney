"""The translation catalogue: extract, check, and ship to the frontend.

    python scripts/i18n.py            # copy nl.json into the frontend; report gaps
    python scripts/i18n.py --check    # exit 1 on a missing or stale translation (CI)
    python scripts/i18n.py --stub     # add missing keys to nl.json with "" values

Keys are collected from every `t("…")` in the web app, `_("…")` in the desktop
and `translate("…")`/`t("…")` in the server, plus the label tables that are
translated at render time (CLASS_LABELS, period presets, account types, roles,
commodity kinds, reconciliation marks).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "src" / "pymoney" / "i18n" / "nl.json"
FRONTEND_COPY = ROOT / "frontend" / "packages" / "core" / "src" / "generated" / "nl.json"

CALL = re.compile(r"""(?<![\w.])(?:t|tr|_|translate)\(\s*"((?:[^"\\]|\\.)+)"\s*[,)]""")
CALL_PY_MULTI = re.compile(r"""(?<![\w.])_\(\s*\n\s*((?:"(?:[^"\\]|\\.)*"\s*\n?\s*)+)\)""")
LABEL = re.compile(r"""\b(?:label|blurb|name|description): "([^"]+)\"""")


def keys_in(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = {m.group(1) for m in CALL.finditer(text)}
    for m in CALL_PY_MULTI.finditer(text):
        found.add("".join(json.loads(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"', m.group(1))))
    return {k.replace('\\"', '"') for k in found}


def label_keys() -> set[str]:
    out: set[str] = set()
    for rel in (
        "frontend/packages/core/src/reports.ts",
        "frontend/packages/core/src/periods.ts",
        "frontend/apps/web/src/pages/AccountsPage.tsx",
        "frontend/apps/web/src/pages/SharingPage.tsx",
        "frontend/apps/web/src/pages/CommoditiesPage.tsx",
        "frontend/apps/web/src/ledger/ReconcileMark.tsx",
        "frontend/apps/web/src/reports/NetWorthReport.tsx",
        "src/pymoney/desktop/ui/reports.py",
        "src/pymoney/desktop/ui/charts.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        head = text.split("export function")[0] if rel.endswith(("ts", "tsx")) else text
        out |= set(LABEL.findall(head))
        if rel.endswith("reports.py"):
            out |= set(re.findall(r'\("[a-z0-9-]+", "([^"]+)"\)', text))
            out |= set(re.findall(r'"[a-z-]+": "([^"]+)",', text.split("PERIOD_PRESETS")[0]))
    return out - {"Euro", "US Dollar", "Pound Sterling", "Swiss Franc", "Yen", "Canadian Dollar", "Australian Dollar",
                  "Swedish Krona", "Norwegian Krone", "Danish Krone", "Złoty", "Czech Koruna"}


def all_keys() -> set[str]:
    keys: set[str] = set()
    for pattern in ("frontend/apps/web/src/**/*.tsx", "frontend/packages/core/src/*.ts", "src/pymoney/desktop/**/*.py",
                    "src/pymoney/api/routers/auth.py"):
        for path in ROOT.glob(pattern):
            if "/generated/" in str(path) or "/test/" in str(path):
                continue
            keys |= keys_in(path)
    keys |= label_keys()
    # Role names shown through t(str(role)).
    keys |= {"viewer", "editor", "owner"}
    return {k for k in keys if k.strip()}


def main(argv: list[str]) -> int:
    catalogue: dict[str, str] = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    keys = all_keys()
    missing = sorted(k for k in keys if not catalogue.get(k))
    stale = sorted(k for k in catalogue if k not in keys)
    if "--stub" in argv:
        for k in missing:
            catalogue.setdefault(k, "")
        for k in stale:
            del catalogue[k]
        CATALOGUE.write_text(json.dumps(dict(sorted(catalogue.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"stubbed {len(missing)} key(s), removed {len(stale)} stale")
        return 0
    FRONTEND_COPY.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND_COPY.write_text(CATALOGUE.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{len(keys)} strings, {len(keys) - len(missing)} translated to Dutch")
    for k in missing:
        print(f"  missing: {k}")
    for k in stale:
        print(f"  stale:   {k}")
    if "--check" in argv and (missing or stale):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
