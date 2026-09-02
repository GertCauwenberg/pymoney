# pymoney-desktop: the desktop client

The PySide6 client for PyMoney. The same API as the web app, two ways: a thin
online client, or a local SQLite replica with an outbox that replays when the
server is back. Depends on the server package in `../server` -- it speaks the
API's wire schemas and, offline, runs the API's own read models on its replica
-- so in a checkout both are installed into the shared `../.venv`.

```bash
make install          # ../server (editable) and this package, with dev extras
make check            # lint, types, the offscreen Qt suite with its coverage gate
make run              # python -m pymoney_desktop, against $PYMONEY_SERVER
make build            # PyInstaller one-folder build into dist/PyMoney
```

See "The desktop app" in the repository's [README](../README.md).
