#!/bin/sh
# Image build step (root, INSIDE the lab image only): build the post-P0 box in miniature and install the
# deliverables from scripts/setup/p0-deroot/ VERBATIM. Synthetic secrets only. Never run on a host.
set -eu
D=/opt/p0/deliverables
L=/opt/p0/lab

# Identities (docs/architecture/p0-deroot.md section 3). `zoe` keeps uid 1000 and video, loses sudo + docker.
groupadd -f docker; groupadd -f video; groupadd -f render; groupadd --system zoe-dockerproxy
useradd -m -u 1000 -s /bin/bash -G video,render,zoe-dockerproxy zoe
useradd -m -s /bin/bash -G sudo zoe-admin && echo "zoe-admin:$(head -c 18 /dev/urandom | base64)" | chpasswd
for u in zoe-svc zoe-flue zoe-model zoe-deploy; do useradd --system --no-create-home -s /usr/sbin/nologin "$u"; done
usermod -aG zoe-dockerproxy,video,render zoe-svc; usermod -aG video,render zoe-model
useradd --system --no-create-home -s /usr/sbin/nologin -g zoe-dockerproxy zoe-sockproxy
# NEGATIVE CONTROL: today's `zoe` (NOPASSWD: ALL + docker group). Every probe must SEE its power.
useradd -m -s /bin/bash -G docker zoe-pre && echo 'zoe-pre ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/zoe-pre-control
chmod 0440 /etc/sudoers.d/zoe-pre-control

# Deliverables, verbatim.
install -m 0440 "$D/sudoers.d/60-zoe-deroot" /etc/sudoers.d/60-zoe-deroot
install -d -m 0755 /usr/local/libexec/zoe-priv
install -m 0755 "$D"/libexec/* /usr/local/libexec/zoe-priv/        # every root helper, P0-1's deploy helper included
install -m 0644 "$D/systemd/zoe-docker-proxy.service" /etc/systemd/system/
install -m 0644 "$D/tmpfiles.d/zoe-p0-deroot.conf" /etc/tmpfiles.d/   # applied at boot by systemd-tmpfiles-setup
for u in zoe-data flue-zoe-brain-2x llama-server; do
  install -d "/etc/systemd/system/$u.service.d"
  install -m 0644 "$D/systemd/$u.service.d/50-p0-deroot.conf" "/etc/systemd/system/$u.service.d/"
done

# Stand-in BASE units: the ported system unit minus the real binary (ExecStart runs the stand-in instead) and
# with the directives the drop-ins must compose with (memory guards, mlock, the stdout capture zoe-data uses).
base() {  # name port extra-directives
  printf '[Unit]\nDescription=P0 lab stand-in %s\n[Service]\nExecStart=/usr/bin/python3 %s/standin.py %s %s\n%s\nRestart=no\n[Install]\nWantedBy=multi-user.target\n' \
    "$1" "$L" "$1" "$2" "$3" > "/etc/systemd/system/$1.service"
}
base zoe-data 8000 'MemorySwapMax=0
MemoryLow=2G
StandardOutput=append:/home/zoe/.zoe-logs/zoe-data.stdout.log'
base flue-zoe-brain-2x 3579 'MemorySwapMax=0
MemoryLow=512M
MemoryMax=2G'
base llama-server 11434 'MemorySwapMax=0
MemoryLow=6G
LimitMEMLOCK=infinity'
# The fake daemon takes the real daemon's unit name, so the proxy unit's Requires=docker.service stays verbatim.
printf '[Unit]\nDescription=P0 lab fake dockerd\n[Service]\nExecStart=/usr/bin/python3 %s/fake_dockerd.py\nExecStartPost=/bin/sh -c "for i in 1 2 3 4 5 6 7 8 9 10; do [ -S /var/run/docker.sock ] && exit 0; sleep 0.5; done; exit 1"\n[Install]\nWantedBy=multi-user.target\n' "$L" \
  > /etc/systemd/system/docker.service
printf '[Unit]\nDescription=P0 lab cells\nAfter=zoe-data.service flue-zoe-brain-2x.service llama-server.service zoe-docker-proxy.service\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 %s/cells.py\n[Install]\nWantedBy=multi-user.target\n' "$L" \
  > /etc/systemd/system/p0lab-cells.service

# The filesystem the drop-ins expect: root-owned code/models/venvs under /srv, secrets root 0600 under /etc/zoe.
install -d -m 0755 /srv/zoe/assistant/labs/flue-zoe-brain-2x/dist /srv/zoe/models /srv/zoe/venvs /srv/zoe/node \
  /srv/zoe/llama.cpp-b11982 /etc/zoe/env
echo "# lab checkout" > /srv/zoe/assistant/README.md
echo "// lab" > /srv/zoe/assistant/labs/flue-zoe-brain-2x/dist/server.mjs
head -c 4096 /dev/urandom > /srv/zoe/models/lab.gguf
for s in zoe-data flue-zoe-brain-2x; do
  printf 'P0LAB_SECRET=synthetic-%s-%s\n' "$s" "$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "/etc/zoe/env/$s.env"
  chmod 0600 "/etc/zoe/env/$s.env"
done
install -d -o zoe-svc -g zoe-svc -m 0750 /var/lib/zoe-svc /var/lib/zoe-svc/dot-zoe /var/lib/zoe-svc/voice-samples \
  /var/lib/zoe-svc/mempalace
echo '{"lab": true}' > /var/lib/zoe-svc/dot-zoe/state.json && chown zoe-svc:zoe-svc /var/lib/zoe-svc/dot-zoe/state.json
# The REAL home stays zoe's: a private key that no service may see.
install -d -o zoe -g zoe -m 0700 /home/zoe/.ssh && echo lab-key > /home/zoe/.ssh/id_lab && chown zoe:zoe /home/zoe/.ssh/id_lab

systemctl enable docker.service zoe-docker-proxy.service zoe-data.service flue-zoe-brain-2x.service \
  llama-server.service p0lab-cells.service
systemctl mask getty@tty1.service console-getty.service systemd-logind.service 2>/dev/null || true

# P0-1 callers (P cells). A fake docker CLI owns /usr/bin/docker (it records argv, runs nothing); a local bare repo
# stands in for the public GitHub origin through a ROOT-owned /etc/gitconfig insteadOf (the helper's ORIGIN line stays
# verbatim); /etc/zoe/compose.env is root 0600; /srv/zoe holds the "moved" bind sources plus a planted symlink.
install -m 0755 "$L/fake_docker_cli.py" /usr/bin/docker
O=/opt/p0/origin.git; W=/opt/p0/work
git init -q --bare -b main "$O"; git init -q -b main "$W"
install -d "$W/services/zoe-ui" "$W/services/zoe-auth"
echo "services: {}" > "$W/docker-compose.yml"; echo "services: {}" > "$W/docker-compose.modules.yml"
echo "server {}" > "$W/services/zoe-ui/nginx.conf"; echo "FROM scratch" > "$W/services/zoe-auth/Dockerfile"
G="git -C $W -c user.name=lab -c user.email=lab@lab.invalid"
$G add -A && $G commit -qm main-1 && $G push -q "$O" main
$G checkout -qb side && echo off > "$W/off" && $G add -A && $G commit -qm off-main && $G push -q "$O" side
printf 'main %s\noff %s\n' "$(git -C "$O" rev-parse main)" "$(git -C "$O" rev-parse side)" > /opt/p0/shas
# The deploy helper reads no system/global git config (GIT_CONFIG_NOSYSTEM), so the lab's copy has its ONE ORIGIN line
# pointed at the local repo; cell P1 proves that is the only difference from the deliverable.
sed -i 's#^ORIGIN = "https://github.com/jason-easyazz/zoe-ai-assistant.git"$#ORIGIN = "file:///opt/p0/origin.git"#' \
  /usr/local/libexec/zoe-priv/zoe-compose-deploy
git config --system --add safe.directory /opt/p0/checkout
git clone -q "$O" /opt/p0/checkout
install -d /opt/p0/checkout/scripts/deploy /opt/p0/checkout/scripts/maintenance /opt/p0/checkout/scripts/lib
install -m 0755 /opt/p0/callers/compose_deploy.sh /opt/p0/checkout/scripts/deploy/
install -m 0755 /opt/p0/callers/brain_cold_start.sh /opt/p0/checkout/scripts/maintenance/
install -m 0644 /opt/p0/callers/zoe_priv.py /opt/p0/checkout/scripts/lib/
printf 'POSTGRES_PASSWORD=synthetic-lab\n' > /etc/zoe/compose.env && chmod 0600 /etc/zoe/compose.env
install -d -m 0755 /srv/zoe/zoe-auth/data /srv/zoe/zoe-ui/ssl /srv/zoe/music-assistant /srv/zoe/elsewhere
ln -s /srv/zoe/elsewhere /srv/zoe/link-data

