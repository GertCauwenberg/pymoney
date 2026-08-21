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
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

USER pymoney
WORKDIR /home/pymoney

# No server yet, so the default command is the component inventory. It is also
# the image's smoke test: if the wheel is broken, this fails.
CMD ["python", "-m", "pymoney"]

# --------------------------------------------------------------------------
# test: the same wheel, plus the suite that proves it works
# --------------------------------------------------------------------------
FROM runtime AS test

USER root
# Installed from the source tree rather than the wheel so that tests/ and the
# tool configuration in pyproject.toml are both present.
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
RUN pip install --no-cache-dir -e '.[dev]' && chown -R pymoney:pymoney /home/pymoney

USER pymoney
ENV HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis
CMD ["pytest", "--hypothesis-profile=ci", "--cov=pymoney", "--cov-report=term-missing"]
