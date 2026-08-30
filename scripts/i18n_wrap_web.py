"""One-off helper: wrap the web app's literal UI strings in t("…").

Run once when introducing i18n (kept in the repository for the record and for
re-use on new components). JSX text nodes and a fixed set of text attributes
are wrapped; components that end up using t() get `const t = useT();` and the
import. Interpolated text (`Delete {name}?`) is left for a person.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ATTRS = ("label", "title", "placeholder", "hint", "aria-label", "emptyMessage", "valueHeader", "summary")
# A text node: between a closing ">" of a tag (not "=>") and the next "<".
TEXT = re.compile(r"(?<![=\-])>(\s*)([A-Za-z][^<>{}\"]*?[A-Za-z0-9.!?…)%])(\s*)<")
ATTR = re.compile(r"\b(" + "|".join(re.escape(a) for a in ATTRS) + r')="([^"{}]+)"')
FUNC = re.compile(r"^(export )?function ([A-Z]\w*)\(", re.M)


def wrap(source: str) -> str:
    def text_repl(m: re.Match) -> str:
        inner = re.sub(r"\s+", " ", m.group(2)).strip()
        if not re.search(r"[A-Za-z]{2}", inner):
            return m.group(0)
        return f'>{m.group(1)}{{t("{inner}")}}{m.group(3)}<'

    out = TEXT.sub(text_repl, source)
    out = ATTR.sub(lambda m: f'{m.group(1)}={{t("{m.group(2)}")}}', out)
    if 't("' not in out:
        return source
    # Give every component that uses t() a translator.
    lines = out.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        result.append(line)
        if FUNC.match(line):
            # Find the end of the signature: parentheses balanced and the line
            # ends with "{" (the body, not a destructured parameter).
            j = i
            parens = line.count("(") - line.count(")")
            while not (parens == 0 and lines[j].rstrip().endswith("{")):
                j += 1
                parens += lines[j].count("(") - lines[j].count(")")
                result.append(lines[j])
            # Does the body use t( ?
            k = j + 1
            depth = 1
            uses = False
            while k < len(lines) and depth > 0:
                depth += lines[k].count("{") - lines[k].count("}")
                if 't("' in lines[k]:
                    uses = True
                k += 1
            if uses and "const t = useT();" not in "\n".join(lines[j + 1 : j + 3]):
                result.append("  const t = useT();")
            i = j + 1
            continue
        i += 1
    out = "\n".join(result)
    if "useT" not in source:
        out = 'import { useT } from "@pymoney/core/hooks";\n' + out
    return out


def main() -> int:
    changed = 0
    for path in sorted(Path(sys.argv[1]).rglob("*.tsx")):
        if path.name in ("main.tsx",) or "/test/" in str(path):
            continue
        before = path.read_text()
        after = wrap(before)
        if after != before:
            path.write_text(after)
            changed += 1
            print("wrapped", path)
    print(f"{changed} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
