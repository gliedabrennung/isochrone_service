FROM debian:bookworm-20260803-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gdal-bin \
        jq \
        osmium-tool \
        python3 \
        python3-numpy \
        python3-pyproj \
        python3-shapely \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/prepare_data.sh scripts/prepare_water.sh scripts/water_build.py /opt/scripts/

RUN chmod +x /opt/scripts/prepare_data.sh /opt/scripts/prepare_water.sh

WORKDIR /opt/scripts

ENTRYPOINT ["/opt/scripts/prepare_data.sh"]
