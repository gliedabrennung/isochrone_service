FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

RUN groupadd --gid 10001 isochrone \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin isochrone

COPY pyproject.toml /srv/pyproject.toml
COPY app /srv/app

FROM base AS dev
RUN pip install --no-cache-dir -e ".[dev]"
USER isochrone
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS runtime
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache
USER isochrone
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log", \
     "--timeout-graceful-shutdown", "10"]
