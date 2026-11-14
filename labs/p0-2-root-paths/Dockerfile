# P0-2 root-path lab image (README.md). Debian bookworm python slim, digest-pinned and already on the box, so the
# build needs no network and no package install: it has python3, util-linux (mount, unshare, setpriv, runuser)
# and coreutils, which is everything the cells use. No systemd, no sudo, no Docker inside. Built only by run.sh.
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
RUN useradd -m -u 1000 -s /bin/sh zoe && chmod 0750 /home/zoe
COPY deliverables/ /opt/p02/deliverables/
COPY lab/ /opt/p02/lab/
RUN install -m 0755 /opt/p02/lab/fake_docker.py /usr/bin/docker
CMD ["python3", "-B", "/opt/p02/lab/cells.py"]
