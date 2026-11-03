# Credential-vault lab image (V0/V1): Debian bookworm + Chromium + Python 3 + the official `bw` CLI (OSS build,
# sha256-pinned by run.sh) + the zoe-vaultd broker under test, copied verbatim from services/zoe-vaultd/.
# The same image runs the lab client (broker, browser, phone, members) and the test sites. Built only by driver.py.
ARG BASE=node:22-bookworm-slim@sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752
FROM ${BASE}
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      chromium python3 python3-cryptography publicsuffix libnss3-tools ca-certificates time procps util-linux \
 && rm -rf /var/lib/apt/lists/*
COPY bw /usr/local/bin/bw
COPY zoe_vaultd/ /opt/zoe-vaultd/zoe_vaultd/
COPY lab/ /opt/cvlab/lab/
RUN chmod 0755 /usr/local/bin/bw && sh /opt/cvlab/lab/provision.sh
