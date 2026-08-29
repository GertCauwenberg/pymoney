# PyMoney container image.
#
# Targets:
#   runtime  the installed package, no build tools, non-root  (default)
#   test     runtime plus dev dependencies and the test suite
#
#   docker build --target test . && docker run --rm <image>
#
# Pinned to a minor version of the base image; Dependabot bumps it. Pin to a
# digest instead once there is something to deploy for real.
ARG PYTHON_VERSION=3.12

# --------------------------------------------------------------------------
# builder: produce a wheel, so the runtime image never sees a compiler
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN pip install --no-cache-dir build==1.2.2.post1

# Source layout only; see .dockerignore for what is deliberately excluded.
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m build --wheel --outdir /dist

# --------------------------------------------------------------------------
# runtime: what would actually be deployed
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Unprivileged from the start: a container that never needs root should never
# have it, and retrofitting this once volumes exist is painful.
RUN groupadd --system --gid 1001 pymoney \
    && useradd --system --uid 1001 --gid pymoney --create-home pymoney

COPY --from=builder /dist/*.whl /tmp/
# The `api`, `store` and `postgres` extras: the domain core needs nothing, but an
# image that cannot serve or reach a database is not worth deploying.
RUN pip install --no-cache-dir "$(echo /tmp/*.whl)[api,store,postgres]" \
    && rm -f /tmp/*.whl

USER pymoney
WORKDIR /home/pymoney

EXPOSE 8000

# The component inventory, not the server: it exits, so it doubles as the
# image's smoke test (a broken wheel or a missing extra shows up here). Compose
# overrides it with uvicorn for the api service. Migrations run from the same
# image with `python -m pymoney.store.migrate`; there is no alembic.ini here and
# none is needed.
CMD ["python", "-m", "pymoney"]

# --------------------------------------------------------------------------
# test: the same wheel, plus the suite that proves it works
# --------------------------------------------------------------------------
FROM runtime AS test

USER root
# Installed from the source tree rather than the wheel so that tests/ and the
# tool configuration in pyproject.toml are both present.  alembic.ini is needed
# too: the migration tests build their Config from it relative to the working
# directory, and alembic silently treats a missing ini as an empty config
# ("No 'script_location' key found in configuration").
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY tests ./tests
# The shipped API contract; tests/test_api_sync.py checks the code still
# matches it (resolved as <repo>/docs/openapi.json relative to tests/).
COPY docs/openapi.json ./docs/openapi.json
# `dev` pulls PySide6 for the desktop tests (a quarter of a gigabyte of Qt);
# the image does without, and those tests skip when PySide6 is absent.
RUN pip install --no-cache-dir -e '.[store,postgres,api]' \
        pytest pytest-cov hypothesis httpx ruff mypy \
    && chown -R pymoney:pymoney /home/pymoney

USER pymoney
ENV HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis
CMD ["pytest", "--hypothesis-profile=ci", "--cov=src/pymoney", "--cov-report=term-missing"]
