"""``python -m pymoney_desktop`` -- the desktop app."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from .ui.app import main as run
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SystemExit(
            "the 'desktop' extra is not installed: pip install 'pymoney-core[desktop]'"
        ) from exc
    return run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
