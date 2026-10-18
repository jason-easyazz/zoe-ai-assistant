"""Pins the P0 de-root deliverables in scripts/setup/p0-deroot/ (docs/architecture/p0-deroot.md).

These files are NOT applied by anything; the owner installs them phase by phase. What this test guards is the
security property each one exists for, so a later edit cannot quietly widen it:
  - the sudoers fragment is valid and stays an allowlist (exact argv, root-owned helpers, start/stop/restart only);
  - the Docker proxy never allows create/build/pull/restart/delete or exec into a bind-swappable container;
  - the service drop-ins never run as root/zoe, never mount a bind SOURCE from /home, and keep the sandbox on;
  - the root helpers refuse every argument outside their fixed set before touching anything.
The hermetic proof of the same files (real systemd 249, real sudo, the real proxy binary) is labs/p0-deroot.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "p0-deroot"
SUDOERS = ROOT / "sudoers.d" / "60-zoe-deroot"
PROXY = ROOT / "systemd" / "zoe-docker-proxy.service"
DROPINS = {p.parent.name.removesuffix(".service.d"): p for p in ROOT.glob("systemd/*.service.d/50-p0-deroot.conf")}
UNITS = {"llama-server", "kokoro-tts", "functiongemma-router", "zoe-data", "flue-zoe-brain-2x", "flue-zoe-telegram"}


def sudoers_problems(text: str) -> list[str]:
    """Static allowlist rules visudo cannot express. Returns the violations (empty = clean)."""
    joined = re.sub(r"\\\n\s*", "", text)
    problems, aliases = [], {}
    for line in joined.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if m := re.match(r"Cmnd_Alias\s+(\w+)\s*=\s*(.+)", line):
            aliases[m.group(1)] = [c.strip() for c in m.group(2).split(",")]
            continue
        m = re.match(r"([\w-]+)\s+ALL=\(root\)\s+NOPASSWD:\s*(.+)", line)
        if not m or m.group(1) not in {"zoe", "zoe-deploy"}:
            problems.append(f"unexpected rule: {line}")
            continue
        for name in (n.strip() for n in m.group(2).split(",")):
            if name not in aliases:
                problems.append(f"{m.group(1)} granted a non-alias command: {name}")
    for name, cmds in aliases.items():
        for cmd in cmds:
            argv = cmd.split()
            if "*" in cmd or "ALL" in argv or len(argv) < 2:
                problems.append(f"{name}: not an exact argv: {cmd}")
            elif argv[0].startswith("/usr/local/libexec/zoe-priv/"):
                continue
            elif argv[0] == "/usr/bin/systemctl":
                if len(argv) != 3 or argv[1] not in {"start", "stop", "restart"} \
                        or argv[2].removesuffix(".service") not in UNITS:
                    problems.append(f"{name}: systemctl beyond start/stop/restart of a Zoe unit: {cmd}")
            else:
                problems.append(f"{name}: command outside the helper dir and systemctl: {cmd}")
    return problems


def test_sudoers_fragment_is_an_allowlist():
    assert sudoers_problems(SUDOERS.read_text()) == []


@pytest.mark.parametrize("bad", [
    "zoe ALL=(ALL) NOPASSWD: ALL\n",
    "Cmnd_Alias X = /usr/bin/systemctl edit zoe-data.service\nzoe ALL=(root) NOPASSWD: X\n",
    "Cmnd_Alias X = /usr/local/libexec/zoe-priv/zoe-mem-compact *\nzoe ALL=(root) NOPASSWD: X\n",
    "Cmnd_Alias X = /bin/sh -c true\nzoe ALL=(root) NOPASSWD: X\n",
    "Cmnd_Alias X = /usr/bin/systemctl restart ssh.service\nzoe ALL=(root) NOPASSWD: X\n",
    "zoe-svc ALL=(root) NOPASSWD: /usr/bin/true\n",
])
def test_sudoers_linter_catches_widening(bad):
    assert sudoers_problems(bad), "negative control: the linter must reject this"


@pytest.mark.skipif(not (shutil.which("visudo") or Path("/usr/sbin/visudo").exists()), reason="visudo not installed")
def test_sudoers_fragment_parses_with_visudo(tmp_path):
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    assert subprocess.run([visudo, "-c", "-f", str(SUDOERS)], capture_output=True).returncode == 0
    broken = tmp_path / "broken"
    broken.write_text("zoe ALL=(root) NOPASSWD /bin/true\n")  # missing ':' after NOPASSWD
    assert subprocess.run([visudo, "-c", "-f", str(broken)], capture_output=True).returncode != 0


def proxy_rules() -> dict[str, list[re.Pattern]]:
    rules: dict[str, list[re.Pattern]] = {}
    for method, pattern in re.findall(r"-allow([A-Z]+)=(\S+)", PROXY.read_text()):
        rules.setdefault(method, []).append(re.compile(pattern))  # the proxy anchors ^...$ itself
    return rules


ALLOWED = [("POST", "/v1.47/containers/zoe-music-assistant/start"), ("POST", "/containers/livekit/stop"),
           ("GET", "/v1.47/containers/zoe-database/json"), ("POST", "/v1.47/containers/zoe-database/exec"),
           ("POST", "/v1.47/exec/" + "a" * 64 + "/start"), ("HEAD", "/_ping"), ("GET", "/v1.47/containers/json")]
DENIED = [("POST", "/v1.47/containers/create"), ("POST", "/v1.47/build"), ("POST", "/v1.47/images/create"),
          ("POST", "/v1.47/containers/homeassistant/exec"), ("POST", "/v1.47/containers/zoe-omnigent/exec"),
          ("POST", "/v1.47/containers/zoe-ui/restart"), ("GET", "/v1.47/containers/zoe-auth/json"),
          ("POST", "/v1.47/containers/zoe-music-assistant/../homeassistant/exec"), ("POST", "/v1.47/exec/abc/start"),
          ("DELETE", "/v1.47/containers/zoe-database"), ("POST", "/v1.47/containers/zoe-database/kill"),
          ("PUT", "/v1.47/containers/zoe-database/archive"), ("POST", "/v1.47/containers/zoe-music-assistant/update")]


@pytest.mark.parametrize("method,path", ALLOWED)
def test_proxy_allows_what_zoe_needs(method, path):
    assert any(p.fullmatch(path) for p in proxy_rules().get(method, []))


@pytest.mark.parametrize("method,path", DENIED)
def test_proxy_denies_root_equivalent_calls(method, path):
    assert not any(p.fullmatch(path) for p in proxy_rules().get(method, []))


def test_proxy_is_pinned_and_socket_only():
    text = PROXY.read_text()
    assert re.search(r"wollomatic/socket-proxy:[\d.]+@sha256:[0-9a-f]{64}", text)
    assert "-proxysocketendpoint=/run/zoe-docker-proxy/docker.sock" in text and "PrivateNetwork=yes" in text
    assert set(proxy_rules()) <= {"GET", "HEAD", "POST"}
    assert ".*" not in text.split("[Service]", 1)[1]


def directives(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith(("#", "[")):
            key, value = line.split("=", 1)
            out.setdefault(key.strip(), []).append(value.strip())
    return out


def test_one_dropin_per_tier():
    assert set(DROPINS) == {"zoe-data", "flue-zoe-brain-2x", "llama-server"}


@pytest.mark.parametrize("unit", sorted(DROPINS))
def test_dropin_keeps_the_sandbox(unit):
    d = directives(DROPINS[unit])
    assert d["User"][-1] not in {"root", "zoe", ""}
    for key, value in {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "tmpfs",
                       "PrivateTmp": "yes", "CapabilityBoundingSet": "", "RestrictSUIDSGID": "yes"}.items():
        assert d.get(key, [None])[-1] == value, f"{unit}: {key}"
    groups = " ".join(d.get("SupplementaryGroups", [])).split()
    assert "docker" not in groups and "sudo" not in groups
    assert ("zoe-dockerproxy" in groups) is (unit == "zoe-data"), "only zoe-data talks to the Docker proxy"
    assert not any(v.startswith("-") for v in d.get("Nice", []) + d.get("OOMScoreAdjust", []))
    for env in d.get("EnvironmentFile", []):
        assert env == "" or env.startswith("/etc/zoe/env/"), "secrets come from root-owned files only"


@pytest.mark.parametrize("unit", sorted(DROPINS))
def test_no_bind_source_under_home(unit):
    """A root-run mount must never follow a path an LLM-exposed uid can rename (the symlink-swap class)."""
    d = directives(DROPINS[unit])
    for key in ("BindPaths", "BindReadOnlyPaths"):
        for spec in " ".join(d.get(key, [])).split():
            source = spec.lstrip("-").split(":", 1)[0]
            assert not source.startswith("/home/"), f"{unit}: {key} source {source} is under /home"


def test_gpu_tier_can_reach_the_jetson_gpu():
    d = directives(DROPINS["llama-server"])
    assert "video" in " ".join(d["SupplementaryGroups"]).split()
    assert d.get("PrivateDevices", ["no"])[-1] != "yes" and "DevicePolicy" not in d


@pytest.mark.parametrize("argv", [["zoe-mem-compact"], ["zoe-mem-compact", "2"], ["zoe-mem-compact", "-1"], ["zoe-mem-compact", "3", "x"],
                                  ["zoe-nvmap-read"], ["zoe-nvmap-read", "/etc/shadow"], ["zoe-nvmap-read", "free", "x"]])
def test_helpers_refuse_arguments_outside_their_set(argv):
    r = subprocess.run(["sh", str(ROOT / "libexec" / argv[0]), *argv[1:]], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr
