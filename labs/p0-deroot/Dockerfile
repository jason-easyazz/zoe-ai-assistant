# P0 de-root lab image: Ubuntu 22.04 (systemd 249, the Jetson's own) booting systemd as PID 1, with the
# deliverables from scripts/setup/p0-deroot/ installed verbatim by lab/provision.sh. Built only by run.sh.
ARG UBUNTU_IMAGE=ubuntu:22.04@sha256:0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956
ARG PROXY_IMAGE=wollomatic/socket-proxy:1.13.1@sha256:3935b709275e4ec35d6ed5a5c4a1f0d01ed31eec5e7234efc3357ecd47689002
FROM ${PROXY_IMAGE} AS proxy

FROM ${UBUNTU_IMAGE}
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends systemd systemd-sysv sudo python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=proxy /socket-proxy /usr/local/bin/socket-proxy
COPY deliverables/ /opt/p0/deliverables/
COPY lab/ /opt/p0/lab/
RUN sh /opt/p0/lab/provision.sh
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
