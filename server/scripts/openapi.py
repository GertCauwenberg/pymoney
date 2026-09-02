"""Write docs/openapi.json from the code. Run via `make openapi`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pymoney.api import create_app
from pymoney.api.settings import Settings


def main() -> None:
    # A throwaway file-backed database: create_app only needs a valid URL, and
    # the document must not depend on anything in it.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(
            settings=Settings(
                database_url=f"sqlite+pysqlite:///{Path(tmp) / 'spec.sqlite'}"
            )
        )
        target = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {target} ({target.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
