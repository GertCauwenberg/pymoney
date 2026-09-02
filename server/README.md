# pymoney-core: the server

The Python package behind PyMoney: the double-entry domain model (`pymoney.core`,
pure stdlib), persistence over SQLite and PostgreSQL (`pymoney.store`), price
quotes (`pymoney.quotes`), the KMyMoney importer (`pymoney.importers`) and the
HTTP API (`pymoney.api`). One of three independently deployable components; the
others are `../desktop` (the PySide6 client, which depends on this package) and
`../frontend` (the web app, which generates its types from `docs/openapi.json`
here).

```bash
make install          # ../.venv, editable, with dev extras
make check            # lint, types, tests with the coverage gate (what CI runs)
make serve            # the API on http://127.0.0.1:8000/docs
make image            # the container image (this directory is the build context)
make help             # everything else
```

The full story -- the design decisions, the rounding guarantee, the API's
contract, persistence, performance -- is in the repository's
[README](../README.md).
