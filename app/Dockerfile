# One image for both containers of the console (web and runner). The runner needs
# the Docker CLI and the compose plugin (static binaries from the official image),
# git (tag check of the checkouts), and bash, gzip, sha256sum (dump and checksums).
FROM docker:29-cli AS docker

FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY console ./console

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
