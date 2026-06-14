ARG ARCHIPELAGO_VERSION=0.6.7

# Python 3.13 - matches the version bundled in Archipelago 0.6.7
FROM python:3.13-slim

ARG ARCHIPELAGO_VERSION

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    tar \
    ca-certificates \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Binary - provides ArchipelagoGenerate and ArchipelagoServer
RUN wget -qO /tmp/archipelago_bin.tar.gz \
    "https://github.com/ArchipelagoMW/Archipelago/releases/download/${ARCHIPELAGO_VERSION}/Archipelago_${ARCHIPELAGO_VERSION}_linux-x86_64.tar.gz" \
    && mkdir -p /app/Archipelago \
    && tar -xf /tmp/archipelago_bin.tar.gz -C /app/Archipelago/ \
    && rm /tmp/archipelago_bin.tar.gz

# Source tree - provides worlds/*.py files readable by system Python 3.13
RUN wget -qO /tmp/archipelago_src.tar.gz \
    "https://github.com/ArchipelagoMW/Archipelago/archive/refs/tags/${ARCHIPELAGO_VERSION}.tar.gz" \
    && tar -xf /tmp/archipelago_src.tar.gz -C /tmp/ \
    && mv "/tmp/Archipelago-${ARCHIPELAGO_VERSION}" /app/ArchipelagoSrc \
    && rm /tmp/archipelago_src.tar.gz

RUN grep -vE '^\s*(kivy|cython|cymem|pyshortcuts|Pymem|.* @ git\+)' \
        /app/ArchipelagoSrc/requirements.txt \
    | pip install --no-cache-dir -r /dev/stdin

RUN pip install --no-cache-dir \
    aiohttp \
    websockets \
    boto3 \
    setuptools

# Secret of Evermore (soe) needs pyevermizer at *generation* time. Archipelago
# would lazily pip-install it when loading soe.apworld, but the gen container has
# no network - so bake it at build time (build has network). Without it the world
# fails to import ("name '_loc' is not defined") and any seed including SoE breaks.
RUN pip install --no-cache-dir pyevermizer==0.50.1

# The generation container is sealed (no outbound network), yet Archipelago still
# attempts to pip-install each world's optional *play-time* deps at load. Force
# pip offline so those attempts fail instantly instead of burning ~8s each on DNS
# retries (~50s per generation). All build-time installs above already ran with
# network, so this only affects the lazy installs at runtime.
ENV PIP_NO_INDEX=1 \
    PIP_RETRIES=0 \
    PIP_DEFAULT_TIMEOUT=1

# Scripts from repo root (standalone repo - no archipelago/ prefix)
COPY generate_template.py /usr/local/bin/generate_template.py
COPY introspect_options.py /usr/local/bin/introspect_options.py
COPY generate_multiworld.py /usr/local/bin/generate_multiworld.py
COPY reachable.py /reachable/reachable.py
COPY read_save.py /readsave/read_save.py
COPY ap_server.sh /ap_server.sh
COPY entrypoint.sh /entrypoint.sh

# Strip CRLF then mark executable. The build context may be checked out on Windows
# (autocrlf) where these files carry CRLF; a CRLF in a script's shebang makes the
# kernel look for the interpreter "/bin/sh\r" and fail with "exec: no such file or
# directory". Normalize to LF at build time so the image works regardless of the
# host's git line-ending config.
RUN for f in /usr/local/bin/generate_template.py \
             /usr/local/bin/introspect_options.py \
             /usr/local/bin/generate_multiworld.py \
             /reachable/reachable.py \
             /readsave/read_save.py \
             /ap_server.sh \
             /entrypoint.sh; do \
        sed -i 's/\r$//' "$f"; \
    done \
    && chmod +x /usr/local/bin/generate_template.py \
    /usr/local/bin/introspect_options.py \
    /usr/local/bin/generate_multiworld.py \
    /readsave/read_save.py \
    /ap_server.sh \
    /entrypoint.sh

ENV PATH="/app/Archipelago/Archipelago:${PATH}"

WORKDIR /workspace

CMD ["/entrypoint.sh"]
