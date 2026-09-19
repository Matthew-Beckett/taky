# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-alpine AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# setuptools-scm normally derives the version from git tags. .git is excluded
# from the build context, so CI passes the version explicitly.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:${PYTHON_VERSION}-alpine AS runtime

RUN addgroup -S taky && \
    mkdir -p /etc/taky /var/taky && \
    adduser -S -G taky -h /var/taky taky && \
    chown taky:taky /var/taky

COPY --from=builder /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH

USER taky
WORKDIR /var/taky

# 8087/8089: COT server (plain/SSL), 8080/8443: data package server (plain/SSL)
EXPOSE 8080 8443 8087 8089

VOLUME ["/var/taky"]

# Config is auto-discovered: ./taky.conf, then /etc/taky/taky.conf.
# Mount a config to /etc/taky or run with --entrypoint taky_dps/takyctl.
ENTRYPOINT ["taky"]
