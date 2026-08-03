"""No live runtime database may be tracked, and MA's data must live outside the checkout.

Why (2026-08-03): `data/music-assistant/{auth,library}.db` and their WALs were
tracked while Music Assistant rewrites them continuously. The live checkout was
PERMANENTLY dirty with *tracked* changes — a hard block on the deploy readiness
gate that, unlike untracked cruft, no `--untracked-files=no` can clear. Every
deploy would be refused forever, and the documented consequence of a gate that
always says no is that operators bypass it
(`tests/unit/test_deploy_live_clean_tree.py`). `auth.db` also carried provider
credentials into git history.

The near-miss worth pinning: untracking them IN PLACE is destructive. `git rm
--cached` records a deletion and `deploy_live.sh` runs `git reset --hard`, which
applies it to disk — wiping a running service's stores, because the directory
was bind-mounted as MA's `/data`. Caught in review on #1604. Hence the bind
mount must point OUTSIDE the checkout; that is asserted here so the two halves
cannot drift apart.

Suffix coverage is GENERIC (`-wal`/`-shm`/`-journal`), not `.db`-anchored
(review: Codex) — SQLite creates `-journal` in rollback mode, and a
`runtime.sqlite` yields `runtime.sqlite-wal`, none of which a `*.db-wal` pattern
would catch.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.modules.yml"

# Live-store shapes. Generic sidecars, not just the `.db` spellings.
DB_RE = re.compile(r"\.(db|sqlite|sqlite3)$|(-wal|-shm|-journal)$")

# Human-authored schema/fixture data is legitimately tracked. Keep SHORT and
# explicit — anything added here should be a file a person wrote, not one a
# service writes at runtime.
ALLOWED = ("data/schema/", "tests/", "labs/")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_no_NEW_live_database_is_tracked():
    """Step A of the migration deliberately leaves the four Music Assistant
    databases tracked — deleting them here would be applied by CD's
    `git reset --hard` before anyone could run the migration (review: Codex).
    They stop being rewritten once the bind mount moves, and are untracked in
    step B once nothing writes to that path.

    What must hold NOW: no OTHER live store creeps in.
    """
    known = {
        "data/music-assistant/auth.db",
        "data/music-assistant/auth.db-wal",
        "data/music-assistant/library.db",
        "data/music-assistant/library.db-wal",
    }
    offenders = [f for f in _tracked()
                 if DB_RE.search(f) and not f.startswith(ALLOWED) and f not in known]
    assert not offenders, (
        "new live databases/sidecars are tracked; they make the live checkout "
        "permanently dirty and block every deploy:\n  " + "\n  ".join(offenders)
    )


def test_generic_sqlite_sidecars_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text()
    for pattern in ("*-journal", "*.sqlite-wal", "*.sqlite-shm",
                    "data/music-assistant/"):
        assert pattern in ignore, f"{pattern} must be gitignored"


def test_music_assistant_data_is_mounted_outside_the_checkout():
    """The load-bearing half. If the bind mount ever points back at a
    repo-relative path, untracked data lands where `reset --hard` runs and the
    original hazard returns — this time silently, since the files would be
    gitignored rather than tracked."""
    compose = COMPOSE.read_text()
    assert "- ./data/music-assistant:/data" not in compose, (
        "music-assistant is bind-mounted from inside the git checkout again; "
        "runtime data there is destroyed by deploy_live.sh's `git reset --hard`"
    )
    assert "ZOE_MA_DATA" in compose, (
        "expected the mount to use ${ZOE_MA_DATA:-...} pointing outside the repo"
    )


def test_migration_script_is_dry_run_by_default():
    """Destructive maintenance is dry-run by default (scripts/AGENTS.md), and
    this one stops a live service, so the default must not act."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "EXECUTE=0" in src
    assert "--execute" in src
    assert "DRY RUN" in src
    # It must COPY, never move: the original is the rollback.
    assert "cp -a" in src and "mv " not in src.replace("remove", "")
    # And it must verify rather than assume.
    assert "sha256sum" in src


def test_migration_fails_closed_on_missing_databases():
    """Run AFTER the untracking deploy — the exact ordering failure this guards —
    and the directory still exists (ignored settings/playlists remain) while the
    stores are gone. A directory-only check would copy remnants and print DONE,
    and the operator would restart MA against an incomplete store (review:
    Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "missing live databases" in src
    assert "for required in auth.db library.db" in src


def test_migration_fails_closed_when_container_state_is_unknown():
    """A failing `docker ps` (daemon down, no permissions, CLI absent) must not
    be indistinguishable from 'stopped'. Copying under a live writer yields a
    torn SQLite set whose checksums still match — verification proving the wrong
    property (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'if ! ps_out="$(docker ps' in src, "docker state query must be checked"
    assert "cannot determine container state" in src
    # the old fail-open form must not come back
    assert "docker ps --format '{{.Names}}' 2>/dev/null | grep -qx" not in src


def test_migration_rejects_destination_inside_the_checkout():
    """ZOE_MA_DATA is substituted into Compose too, so an override pointing back
    into the repo silently re-creates the hazard — and worse than the original,
    since the files would be gitignored and nothing would look dirty
    (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "readlink -m" in src, "destination must be canonicalised before the check"
    assert "destination is inside the git checkout" in src
    assert "must be an ABSOLUTE path" in src


def test_migration_refuses_to_overwrite_a_newer_destination():
    """After the deploy MA writes to DEST, so a re-run would overlay live state
    with the retained pre-migration snapshot and lose auth/library/settings
    changes. 'Idempotent' was the wrong claim (review: Greptile)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "destination has NEWER data than the source" in src
    assert "migration is already done" in src


def test_migration_detects_stale_destination_files():
    """`cp -a` overlays and never deletes, and verification walked SRC only — so
    a stale WAL/journal or old credential artifact in DEST survived unseen and
    would join the store MA starts against (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "destination has files the source does not" in src
    assert "--mirror" in src


def test_migration_prints_restart_command_with_resolved_destination():
    """A one-shot `ZOE_MA_DATA=... ./migrate` does not survive the script, so a
    restart command without it makes Compose fall back to the default and
    initialise an EMPTY store (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'ZOE_MA_DATA=$DEST docker compose' in src


def test_migration_manifest_covers_non_regular_entries():
    """`find -type f` hides symlinks, dirs and device nodes from BOTH manifests,
    so a stale symlinked sidecar in the destination survived --execute while the
    script printed DONE — and Music Assistant would follow it when opening its
    store. Codex reproduced this with a `stale.db-wal` symlink."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "find . -mindepth 1 | sort" in src, "manifest must cover all entry types"
    assert "find . -type f | sort" not in src, "the -type f manifest must not come back"
    assert "rm -rf" in src, "--mirror must be able to remove dirs/symlinks, not just files"


def test_migration_copies_into_a_clean_destination():
    """The overlay design produced three escalating bugs — stale files, stale
    symlinks, then a symlink at a name ALSO in the source. GNU `cp -a` follows a
    destination symlink, so `sudo cp` would overwrite its target AS ROOT while
    the name-based manifest saw a match and sha256sum followed the link too:
    a root-privileged arbitrary write reported as DONE (review: Codex).

    Rather than enumerate hazards, the class is removed — the destination is
    absent/empty or --mirror clears it, and the copy lands in a fresh directory.
    """
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "destination is not empty" in src, "must refuse a dirty destination"
    assert "destination is itself a symlink" in src
    assert "--remove-destination" in src, "belt-and-braces against following dest links"
    # the clear must happen BEFORE the copy, or the damage is already done
    assert src.index("clearing non-empty destination") < src.index("cp -a --remove-destination")


def test_migration_rejects_containment_in_both_directions():
    """A one-way "is DEST inside REPO" test passes for an ANCESTOR like
    `/home/zoe`. The script would then report a non-empty destination, advise
    --mirror, and `sudo rm -rf` every top-level entry beneath it — including the
    checkout and the source databases. Codex reproduced the SRC=DEST case with
    both databases deleted. The guard would cause the loss it exists to prevent.
    """
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "destination and source are the same path" in src
    assert "ANCESTOR of the source" in src
    assert "ANCESTOR of the git checkout" in src
    assert "destination is inside the source" in src
    assert "destination is inside the git checkout" in src
    # containment must be decided on canonicalised paths, and before any clearing
    assert 'SRC_ABS="$(readlink -m' in src
    assert src.index("ANCESTOR of the git checkout") < src.index("clearing non-empty destination")


def test_containment_helper_handles_the_filesystem_root():
    """`contains "/" X` built the pattern `//*`, which matches nothing — so
    ZOE_MA_DATA=/ bypassed BOTH ancestor guards and --mirror would have run
    `rm -rf` over every top-level entry on the box (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'local parent="${1%/}"' in src, "trailing slash must be normalised"
    assert '[[ "$child" == /* ]]' in src, "root must be treated as ancestor of everything"


def test_mirror_lists_deletion_candidates_before_acting():
    """Destructive maintenance prints the candidate list before deleting
    (scripts/AGENTS.md). A generic message then silent deletion gave the
    operator nothing to review, and the dry-run never inspected the destination
    at all (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "will DELETE these destination entries" in src
    assert "DELETE these existing destination entries" in src, "dry-run must preview too"
    assert src.index("will DELETE these destination entries") < src.index("-exec rm -rf")


def test_destination_symlink_checked_before_canonicalisation():
    """`readlink -m` resolves the link and DEST is replaced by its target, so a
    `-L "$DEST"` guard placed after that could never fire for the links it was
    written to catch — and --mirror would erase the target (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert src.index('destination path is a symlink') < src.index('DEST_ABS="$(readlink -m')


def test_no_sigpipe_prone_pipelines():
    """`find … | head -N` SIGPIPEs the producer once head exits; under
    `set -euo pipefail` that is exit 141 — and it fires AFTER the container is
    stopped, leaving MA down with no diagnostic. Codex reproduced it with a
    10,000-file directory."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "| head" not in code, "use awk 'NR<=N', which consumes the whole stream"
    assert "sort -rn" not in code, "use an awk max, not sort|head"
    assert "-print -quit 2>/dev/null | grep -q" not in code


def test_manifests_are_materialised_and_validated():
    """Bash does not propagate a process-substitution exit status, so a failing
    `sudo find` yields an EMPTY source manifest — every destination entry then
    looks extra and --mirror deletes valid data (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "source manifest is EMPTY" in src
    assert "could not enumerate the source" in src
    assert "verified 0 files" in src, "a zero-file verification must fail, not pass"
