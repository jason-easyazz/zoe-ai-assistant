"""P0-2 bind-source move: zoe-p0-2-paths apply / verify / revert / inspect against a temp tree, plus the static
rules of the shipped manifest, mount unit, compose override example and backup-helper example
(docs/architecture/p0-deroot.md section 8; proven end to end in labs/p0-2-root-paths/).

Runs unprivileged: `--root` puts every manifest path under tmp_path, the owner is the test's own uid, and the
"service user" is `nobody` (who must not be able to write any component) or, for the negative controls, the test
user itself."""

from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
P02 = REPO / "scripts" / "setup" / "p0-deroot" / "p0-2"
TOOL = P02 / "zoe-p0-2-paths"
ME, MYGID = os.getuid(), os.getgid()


def tool(*args, stdin=None):
    return subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True, input=stdin)


@pytest.fixture
def box(tmp_path):
    """A miniature box: /home/zoe/app (old, the service user's) and /srv/zoe (new) under tmp_path."""
    root = tmp_path / "root"
    old = root / "home" / "zoe" / "app"
    (old / "sub").mkdir(parents=True)
    (old / "conf.yaml").write_text("synthetic\n")
    (old / "sub" / "x").write_text("x\n")
    (root / "home" / "zoe" / "app.conf").write_text("one file\n")
    (root / "srv").mkdir()
    os.chmod(root, 0o755)
    man = tmp_path / "m.manifest"
    man.write_text(f"dir  app  /home/zoe/app      /srv/zoe/app        {ME} {MYGID} 0700\n"
                   f"file conf /home/zoe/app.conf /srv/zoe/conf/app.conf {ME} {MYGID} 0600\n")
    common = ["--root", str(root), "--service-user", "nobody", "--state", str(tmp_path / "state.json")]
    return root, man, common


def test_apply_then_verify_passes(box):
    root, man, common = box
    a = tool("apply", str(man), *common, "--container-stopped")
    assert a.returncode == 0, a.stderr
    assert (root / "srv/zoe/app/conf.yaml").read_text() == "synthetic\n"
    assert (root / "srv/zoe/conf/app.conf").stat().st_mode & 0o777 == 0o600
    v = tool("verify", str(man), *common)
    assert v.returncode == 0 and v.stdout.count("PASS") == 2, v.stdout


def test_verify_negative_control_writable_parent(box):
    root, man, common = box
    tool("apply", str(man), *common, "--container-stopped")
    os.chmod(root / "srv" / "zoe", 0o777)  # one component world-writable: renamable by the service user
    v = tool("verify", str(man), *common)
    assert v.returncode == 1 and v.stdout.count("FAIL") == 2 and "world-writable" in v.stdout


def test_verify_negative_control_owned_by_service_user(box):
    root, man, _ = box
    tool("apply", str(man), "--root", str(root), "--service-user", "nobody", "--container-stopped")
    v = tool("verify", str(man), "--root", str(root), "--service-user", getpass.getuser())
    assert v.returncode == 1 and "owned by the service user" in v.stdout


def test_verify_negative_control_symlink_component(box, tmp_path):
    root, man, common = box
    tool("apply", str(man), *common, "--container-stopped")
    (root / "srv" / "zoe").rename(root / "srv" / "zoe.real")
    (root / "srv" / "zoe").symlink_to(root / "srv" / "zoe.real")
    v = tool("verify", str(man), *common)
    assert v.returncode == 1 and "is a symlink" in v.stdout


def test_verify_negative_control_wrong_mode(box):
    root, man, common = box
    tool("apply", str(man), *common, "--container-stopped")
    os.chmod(root / "srv/zoe/app", 0o755)
    v = tool("verify", str(man), *common, "--only", "app")
    assert v.returncode == 1 and "want" in v.stdout


def test_apply_is_idempotent_and_copies_once(box):
    root, man, common = box
    tool("apply", str(man), *common, "--container-stopped")
    (root / "home/zoe/app/conf.yaml").write_text("changed after the move\n")
    again = tool("apply", str(man), *common, "--container-stopped")
    assert again.returncode == 0 and again.stdout.count("re-asserted") == 2
    assert (root / "srv/zoe/app/conf.yaml").read_text() == "synthetic\n"


def test_apply_requires_the_container_stopped(box):
    _, man, common = box
    assert tool("apply", str(man), *common).returncode == 2


def test_apply_refuses_a_symlinked_old_path(box):
    root, man, common = box
    real = root / "home/zoe/app"
    real.rename(root / "home/zoe/app.real")
    (root / "home/zoe/app").symlink_to(root / "home/zoe/app.real")
    a = tool("apply", str(man), *common, "--only", "app", "--container-stopped")
    assert a.returncode == 1 and "symlink" in a.stderr and not (root / "srv/zoe/app").exists()


def test_revert_renames_new_and_keeps_old(box):
    root, man, common = box
    tool("apply", str(man), *common, "--container-stopped")
    r = tool("revert", str(man), *common, "--only", "app")
    assert r.returncode == 0 and "renamed to" in r.stdout
    assert not (root / "srv/zoe/app").exists() and (root / "home/zoe/app/conf.yaml").exists()
    assert any(p.name.startswith("app.reverted-") for p in (root / "srv/zoe").iterdir())
    assert tool("verify", str(man), *common, "--only", "app").returncode == 1
    # negative control: nothing left to revert is reported, not invented
    assert "nothing to revert" in tool("revert", str(man), *common, "--only", "app").stdout


def test_exec_item_fixed_in_place_and_reverted(tmp_path):
    root = tmp_path / "root"
    f = root / "usr/local/bin/unit-helper.py"
    f.parent.mkdir(parents=True)
    f.write_text("print('ok')\n")
    os.chmod(f, 0o775)
    os.chmod(root, 0o755)
    man = tmp_path / "e.manifest"
    man.write_text(f"exec unit /usr/local/bin/unit-helper.py /usr/local/bin/unit-helper.py {ME} {MYGID} 0755\n")
    common = ["--root", str(root), "--service-user", "nobody", "--state", str(tmp_path / "s.json")]
    before = tool("verify", str(man), *common)
    assert before.returncode == 1 and "want" in before.stdout and "writable" not in before.stdout  # mode only
    os.chmod(f, 0o777)
    assert "world-writable" in tool("verify", str(man), *common).stdout  # negative control
    assert tool("apply", str(man), *common, "--container-stopped").returncode == 0
    assert f.stat().st_mode & 0o777 == 0o755
    assert tool("verify", str(man), *common).returncode == 0
    r = tool("revert", str(man), *common)
    assert r.returncode == 0 and "restored" in r.stdout and f.stat().st_mode & 0o777 == 0o777
    assert "no apply record" in tool("revert", str(man), *common).stdout


def test_manifest_rejects_a_moving_exec_item(tmp_path):
    man = tmp_path / "bad.manifest"
    man.write_text("exec x /a/b /c/d root root 0755\n")
    assert tool("verify", str(man), "--service-user", "nobody").returncode == 2   # no production account needed


def _line(name, priv, sources, status="running"):
    mounts = [{"Type": "bind", "Source": s, "Destination": "/x", "RW": True} for s in sources]
    return "|".join(json.dumps(v) for v in ("/" + name, status, priv, "", None, None, mounts)) + "\n"


def test_inspect_flags_privileged_and_renamable_sources(box):
    root, man, common = box
    tool("apply", str(man), *common, "--container-stopped")
    os.chmod(root / "home", 0o777)
    lines = (_line("clean", False, ["/srv/zoe/app"]) + _line("priv", True, ["/srv/zoe/app"], "exited")
             + _line("homebind", False, ["/home/zoe/app"]))
    r = tool("inspect", "-", "--root", str(root), "--service-user", "nobody", stdin=lines)
    assert r.returncode == 1
    out = r.stdout.splitlines()
    assert out[0].startswith("PASS clean") and out[1].startswith("FAIL priv") and "privileged" in out[1]
    assert out[2].startswith("FAIL homebind") and "world-writable" in out[2]
    assert tool("inspect", "-", "--root", str(root), "--service-user", "nobody",
                stdin=_line("clean", False, ["/srv/zoe/app"])).returncode == 0


# --- the shipped files -----------------------------------------------------------------------------------------

def manifest_items():
    return [ln.split() for ln in (P02 / "move.manifest").read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_public_manifest_shape():
    items = manifest_items()
    names = {i[1] for i in items}
    assert {"homeassistant", "zoe-auth-touch-cache", "livekit-config", "music-assistant", "smb-drop",
            "homeassistant-mcp-bridge"} <= names  # livekit: the realtime-media config the design omitted (B7)
    for kind, name, old, new, owner, group, mode in items:
        assert kind in {"dir", "file"}, name
        assert old.startswith("/home/zoe/") and new.startswith("/srv/zoe/"), name
        assert owner != "zoe" and group != "zoe" and not int(mode, 8) & 0o022, name


def test_compose_override_example_only_binds_moved_sources():
    text = (P02 / "docker-compose.p0-2.override.example.yml").read_text()
    news = {i[3] for i in manifest_items()}
    sources = re.findall(r"^\s*- (/[^:\s]+):", text, re.M)
    assert sources and all(any(n == s or n.startswith(s + "/") for n in news) for s in sources), sources
    assert "/home/" not in "".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"^\s*privileged:\s*true", text, re.M)
    services = re.findall(r"^  ([\w-]+):\s*$", text, re.M)
    blocks = re.split(r"^  [\w-]+:\s*$", text, flags=re.M)[1:]
    assert len(services) == len(blocks) >= 6
    for svc, block in zip(services, blocks):
        assert "volumes: !override" in block and "no-new-privileges:true" in block, svc


def test_mount_unit_is_nosuid_nodev_but_not_noexec():
    opts = re.search(r"^Options=(\S+)", (P02 / "srv-zoe.mount").read_text(), re.M).group(1).split(",")
    assert {"bind", "nosuid", "nodev"} <= set(opts) and "noexec" not in opts


def test_ha_export_example_matches_the_installed_helper_line():
    helper = (REPO / "scripts/setup/p0-deroot/libexec/zoe-ha-config-export").read_text()
    example = (P02 / "zoe-ha-config-export.p0-2.example").read_text()
    assert len(re.findall(r"^HA_CONFIG = .*", helper, re.M)) == 1  # the sed in the example hits exactly one line
    assert re.search(r'^HA_CONFIG = "/srv/zoe/homeassistant"$', example, re.M)
    assert any(i[1] == "homeassistant" and i[3] == "/srv/zoe/homeassistant" for i in manifest_items())


def _raced_tool(tmp_path):
    """The tool with its up-front path check disabled: the state it sees is the one a service user who swaps a
    component AFTER that check passed would leave. The pinned copy must still refuse on its own."""
    text = TOOL.read_text()
    check = "os.path.realpath(old) != os.path.normpath(old)"
    assert text.count(check) == 1
    t = tmp_path / "raced-tool"
    t.write_text(text.replace(check, "False"))
    return t


def _raced(t, *args):
    return subprocess.run([sys.executable, "-I", str(t), *args], capture_output=True, text=True)


@pytest.mark.parametrize("where", ["item", "ancestor"])
def test_a_swap_after_the_check_cannot_redirect_the_root_copy(box, tmp_path, where):
    root, man, common = box
    secret = tmp_path / "root-only"
    secret.mkdir()
    (secret / "token").write_text("not for the service user\n")
    if where == "item":            # old itself swapped for a link to a root-only directory
        (root / "home/zoe/app").rename(root / "home/zoe/app.real")
        (root / "home/zoe/app").symlink_to(secret)
    else:                          # an ancestor swapped: /home/zoe -> a directory whose app is the root-only one
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        (decoy / "app").symlink_to(secret)
        (root / "home/zoe").rename(root / "home/zoe.real")
        (root / "home/zoe").symlink_to(decoy)
    a = _raced(_raced_tool(tmp_path), "apply", str(man), *common, "--only", "app", "--container-stopped")
    assert a.returncode != 0, a.stdout
    assert not (root / "srv/zoe/app/token").exists(), "the root copy followed a swapped-in symlink"


def test_a_symlink_inside_the_tree_is_copied_as_a_link(box, tmp_path):
    root, man, common = box
    (root / "home/zoe/app/sub/link").symlink_to("/etc/hostname")
    a = tool("apply", str(man), *common, "--only", "app", "--container-stopped")
    assert a.returncode == 0, a.stderr
    copied = root / "srv/zoe/app/sub/link"
    assert copied.is_symlink() and os.readlink(copied) == "/etc/hostname"
    assert (root / "srv/zoe/app/sub/x").read_text() == "x\n"



def test_a_planted_stamp_symlink_is_refused_and_its_target_untouched(box, tmp_path):
    """The service user owns the old tree: a `.p0-2-moved` there that links to a host file must never be opened
    (as root it would be truncated)."""
    root, man, common = box
    victim = tmp_path / "host-file"
    victim.write_text("must survive\n")
    (root / "home/zoe/app/.p0-2-moved").symlink_to(victim)
    a = tool("apply", str(man), *common, "--only", "app", "--container-stopped")
    assert a.returncode != 0 and "already contains" in a.stderr, (a.returncode, a.stderr)
    assert victim.read_text() == "must survive\n"


def test_a_source_that_is_not_a_directory_is_refused(box):
    root, man, common = box
    import shutil as _sh
    _sh.rmtree(root / "home/zoe/app")
    (root / "home/zoe/app").write_text("a file where a directory belongs\n")
    a = tool("apply", str(man), *common, "--only", "app", "--container-stopped")
    assert a.returncode != 0 and "not a directory" in a.stderr, a.stderr
    assert not (root / "srv/zoe/app").exists()


def test_copied_files_lose_group_and_world_write(box):
    root, man, common = box
    os.chmod(root / "home/zoe/app/conf.yaml", 0o666)
    a = tool("apply", str(man), *common, "--only", "app", "--container-stopped")
    assert a.returncode == 0, a.stderr
    assert (root / "srv/zoe/app/conf.yaml").stat().st_mode & 0o022 == 0


def test_an_exec_item_swapped_for_a_symlink_is_refused(tmp_path):
    root = tmp_path / "root"
    (root / "opt").mkdir(parents=True)
    victim = tmp_path / "protected"
    victim.write_text("protected\n")
    os.chmod(victim, 0o600)
    (root / "opt" / "run.sh").symlink_to(victim)
    man = tmp_path / "e.manifest"
    man.write_text(f"exec run /opt/run.sh /opt/run.sh {ME} {MYGID} 0755\n")
    a = tool("apply", str(man), "--root", str(root), "--service-user", "nobody", "--state", str(tmp_path / "s.json"),
             "--container-stopped")
    assert a.returncode != 0
    assert victim.stat().st_mode & 0o777 == 0o600, "apply changed the link target"


def test_a_copy_that_fails_midway_is_never_published_and_the_retry_completes(box):
    """A file-size limit makes the copy fail after its first kilobyte. The destination must not exist afterwards
    (a direct write would leave a truncated file that the retry then takes as done), and the retry copies it all."""
    import resource
    import signal
    root, man, common = box
    big = "x" * 8192 + "\n"
    (root / "home/zoe/app.conf").write_text(big)

    def small_files():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))

    cut = subprocess.run([sys.executable, "-I", str(TOOL), "apply", str(man), *common, "--only", "conf",
                          "--container-stopped"], capture_output=True, text=True, preexec_fn=small_files)
    assert cut.returncode != 0, cut.stdout
    assert not (root / "srv/zoe/conf/app.conf").exists(), "a half copy was published"
    again = tool("apply", str(man), *common, "--only", "conf", "--container-stopped")
    assert again.returncode == 0, again.stderr
    assert (root / "srv/zoe/conf/app.conf").read_text() == big
