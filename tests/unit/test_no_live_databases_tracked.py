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
    # Parse the ACTUAL volume source and resolve its default the way Compose
    # does (relative paths resolve against the compose file's directory) —
    # asserting the absence of one literal spelling misses every other
    # in-checkout spelling, e.g. ${ZOE_MA_DATA:-./data/music-assistant}
    # (review: Codex).
    import re as _re
    compose = COMPOSE.read_text()
    m = _re.search(r"\$\{ZOE_MA_DATA:-([^}]*)\}:/data", compose)
    assert m, "music-assistant /data mount must be ${ZOE_MA_DATA:-<default>}:/data"
    default = m.group(1)
    resolved = (COMPOSE.parent / default).resolve() if not default.startswith("/") \
        else Path(default).resolve()
    root = ROOT.resolve()
    assert not str(resolved).startswith(str(root) + "/") and resolved != root, (
        f"compose default {default!r} resolves to {resolved}, INSIDE the checkout — "
        "runtime data there is destroyed by deploy_live.sh's `git reset --hard`, "
        "and silently, since it is gitignored"
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
    # The literal changed when the destination became shell-quoted; assert the
    # PROPERTY (the resolved dest is carried into the restart command), not the
    # exact spelling, so the two tests cannot contradict each other again.
    assert "ZOE_MA_DATA=$(printf '%q' \"$DEST\")" in src
    assert 'docker compose -f $(printf \'%q\' "$REPO_ROOT/docker-compose.modules.yml")' in src, (
        "the emitted restart command must anchor the compose file to REPO_ROOT — "
        "a bare relative path fails from any other cwd, with MA already stopped")


def test_migration_manifest_covers_non_regular_entries():
    """`find -type f` hides symlinks, dirs and device nodes from BOTH manifests,
    so a stale symlinked sidecar in the destination survived --execute while the
    script printed DONE — and Music Assistant would follow it when opening its
    store. Codex reproduced this with a `stale.db-wal` symlink."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    # (Literal updated when the walk moved to privileged `-printf %P`; the
    # PROPERTY is: manifests enumerate ALL entry types via -mindepth 1, never
    # filtered to -type f.)
    assert r"-mindepth 1 -printf '%P\n'" in src, "manifest must cover all entry types"
    assert "-type f -printf '%P" not in src, "the -type f manifest must not come back"
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
    assert 'SRC_ABS="$(sudo readlink -m' in src
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
    assert src.index('destination path is a symlink') < src.index('DEST_ABS="$(sudo readlink -m')


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


def test_checksum_walk_is_materialised_and_validated():
    """`find … || true` masks a walk that emitted some paths then failed: the
    zero-count guard sees a non-zero count and passes, so the script promises
    every-file verification while later files were never read. A PARTIAL walk
    must be as fatal as an empty one (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'sudo find "$SRC" -type f -print0 > "$_filelist"' in src
    assert "source walk failed part-way" in src
    assert "-print0 || true" not in src


def test_agents_contract_describes_step_a_as_mount_only():
    """The DOX contract is what an agent reads FIRST, so a wrong sequence there
    outranks a wrong message in the script. I corrected the success text, then
    --help, and still left scripts/AGENTS.md saying step A is 'the untracking
    commit' — three instances of one error (review: Codex)."""
    doc = (ROOT / "scripts" / "AGENTS.md").read_text()
    assert "deploy the untracking commit" not in doc
    assert "step A is mount-only" in doc.lower() or "STEP A** re-points" in doc
    assert "STEP B is still outstanding" in doc


def test_no_operator_instruction_claims_the_next_deploy_untracks():
    """EVERY operator-facing exit must say the same thing. I corrected the
    success message and left the identical false wording in --help, so an
    operator who never reaches the terminal text would still believe step B was
    done and leave credentials in git indefinitely (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "deploy the untracking commit" not in src
    # both the --help path and the success path must name step A as mount-only
    assert src.count("RE-POINTS THE BIND MOUNT ONLY") >= 2


def test_closing_instructions_do_not_overstate_step_a():
    """The success message is the terminal instruction of the pre-deploy
    procedure. Saying the next deploy 'untracks the DBs' would let the operator
    believe credential removal is done and skip step B — the databases are still
    tracked at this commit (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "RE-POINTS THE BIND MOUNT ONLY" in src
    assert "SEPARATE step B, still outstanding" in src
    assert "deploy the commit that untracks the DBs" not in src


def test_source_stores_must_be_regular_files():
    """`test -f` follows symlinks, `cp -a` preserves the link rather than the
    data, and the `find -type f` checksum walk skips links — so a symlinked
    auth.db could yield 'verified 1/1 files' and DONE having verified nothing
    (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'sudo test -L "$SRC/$required"' in src, "must reject symlinked stores"
    assert "source database is a symlink, not a regular file" in src


def test_emitted_restart_command_is_shell_quoted():
    """The destination is printed for the operator to PASTE. An absolute path
    with whitespace or metacharacters copies and verifies fine, then the
    unquoted paste word-splits — Compose gets the wrong path (or evaluates the
    metacharacters) while MA is still stopped (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert """ZOE_MA_DATA=$(printf '%q' "$DEST")""" in src


def test_destination_inspection_runs_with_copy_privileges():
    """Unprivileged `[[ -L ]]`/`readlink -m` cannot traverse a root-owned 0700
    ancestor, so a symlink hidden beneath one reported "not a link" while the
    privileged `mkdir/cp` followed it — copying the store as root into the
    link's target. Checks must see the same filesystem as the actions
    (review: Codex, reproduced)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    # (Literal updated round 23: branch-deciding probes moved from raw
    # `sudo test` to the fail-closed sudo_probe helper — the privileged
    # property this test pins is unchanged.)
    assert 'sudo_probe -L "$DEST"' in src
    assert 'DEST_ABS="$(sudo readlink -m -- "$DEST")"' in src
    # no unprivileged inspection of DEST may remain in non-comment code
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert '[[ -L "$DEST" ]]' not in code
    assert '[[ -e "$DEST" ]]' not in code


def test_direction_check_probe_fails_closed():
    """Command substitution inside [[ -n ]] discards find's exit status, so a
    transient sudo/traversal failure read as 'destination empty', skipped the
    newer-destination check entirely, and --mirror could clear the LIVE
    destination and replace it with the stale source (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "cannot probe the destination for the direction check" in src
    assert "unreadable destination must not be treated as empty" in src


def test_header_does_not_claim_order_independence():
    """An earlier revision said 'each step is independently safe in any order' —
    directly contradicting the required sequence above it. Step A before
    --execute rolls the live databases back; step B before step A deletes stores
    the old mount still serves (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "independently safe in any order" not in src
    assert "ONLY SAFE ORDER" in src


def test_copy_time_emptiness_probe_fails_closed():
    """The direction probe was hardened one round earlier; the copy-time probe
    kept the identical masked-status shape, so a transient failure read as
    "destination empty" and `cp --remove-destination` proceeded without --mirror
    ever being consulted (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "dest_nonempty()" in src, "probes must go through the fail-closed helper"
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert '-mindepth 1 -print -quit 2>/dev/null' not in code, (
        "no unchecked emptiness probe may remain")


def test_all_source_symlinks_are_rejected_not_just_databases():
    """`cp -a` preserves links, the -type f walk omits them, and manifests
    compare pathnames not targets — so a linked settings.json or playlist ships
    unverified. The real store has zero symlinks (measured), so refusing the
    whole class costs nothing (review: Codex)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "source contains symlinks" in src
    assert 'sudo find "$SRC" -type l' in src


def test_completion_marker_is_the_primary_direction_defence():
    """`git reset --hard` rewrites rolled-back blobs with FRESH mtimes, so after
    step A deploys the stale source compares NEWER and the mtime heuristic
    permits the run; --mirror would then clear the migrated store and replace it
    with rolled-back data. Codex reproduced this. Checkout mtimes cannot decide
    direction — a persistent marker in the destination can."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'MARKER=".ma-migration-complete"' in src
    assert "destination carries a completion marker" in src
    # the gate must precede BOTH the mirror clearing and the copy
    assert src.index("destination carries a completion marker") < src.index("clearing non-empty destination")
    assert src.index("destination carries a completion marker") < src.index("cp -a --remove-destination")
    # and success must write it
    assert 'sudo tee "$DEST/$MARKER"' in src


def test_future_compose_mount_is_validated_via_compose_itself():
    """Rounds 20/25/28: hand-rolled dotenv parsing missed the `export` form,
    then the relative base, then whitespace around `=` — re-implementing
    Compose's grammar one spelling at a time loses by construction. The guard
    now runs `docker compose config` with ZOE_MA_DATA masked (future plain
    invocation semantics) and requires the mount Compose reports to equal the
    validated destination (review: Codex x3)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    # round 31: masking one variable was not enough — an .env value like
    # ${CUSTOM_MA_PATH:-default} inherits any transient shell variable. env -i
    # makes the .env and compose file the ONLY interpolation inputs.
    assert 'env -i PATH="$PATH" HOME="$HOME" docker compose' in src
    assert "config --format json" in src
    assert "future plain 'docker compose up' would mount" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "sed -nE 's/^[[:space:]]*(export" not in code, (
        "no hand-rolled dotenv parsing may return")


def test_marker_pathname_is_reserved_in_the_source():
    """A source already containing the marker gets copied and counted by the
    checksum loop, then the marker write replaces the destination copy —
    'verified N/N' with differing hashes, exit 0, and the stray marker then
    blocks the corrective rerun (review: Codex, reproduced)."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "source contains the reserved marker name" in src
    # MARKER must be defined before the source-reservation check uses it
    assert src.index('MARKER=".ma-migration-complete"') < src.index("reserved marker name")


def test_config_preflight_precedes_the_container_stop():
    """The .env-mismatch and completion-marker refusals are pure configuration
    checks; failing them AFTER `docker stop` turns a config error into an
    avoidable service outage, since the script exits without copying or
    restarting (review: Codex). Verified behaviourally with a docker stub:
    neither refusal invokes stop."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    stop_at = src.index('docker stop "$CONTAINER"')
    # (anchor updated with the effective-mount refactor; property unchanged)
    assert src.index("WHAT WILL COMPOSE ACTUALLY MOUNT") < stop_at
    assert src.index("destination carries a completion marker") < stop_at


def test_all_store_access_uses_copy_time_privileges():
    """Round 16 fixed the destination inspection; round 22 found the manifest
    walk still did an unprivileged `cd` and the checksum loop an unprivileged
    `-e` — both failing under a root-owned 0700 ancestor AFTER MA was stopped
    and the data copied: unmarked destination, service down (review: Codex).
    The .env read is the one deliberate exception: Compose reads it as the
    invoking user, so the check must see what Compose sees."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert '(cd "$SRC"' not in code and '(cd "$DEST"' not in code, (
        "no unprivileged cd into the stores")
    assert "-printf '%P\\n'" in src, "manifests must traverse under sudo via -printf %P"
    assert '[[ ! -e "$DEST/$rel" ]]' not in code
    assert 'sudo test -e "$DEST/$rel"' in src
    assert 'sudo test -d "$SRC"' in src


def test_filesystem_probes_yield_explicit_yes_no():
    """`sudo test -e` exits 1 for both 'absent' and 'probe failed', so a
    transient sudo error took the absent branch — mkdir/cp ran against an
    EXISTING destination without --mirror, overwrote matching live files, and
    reported DONE with the marker written (review: Codex, reproduced). Probes
    now emit yes/no with anything else fatal."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "sudo_probe()" in src
    assert "indeterminate probe must not be treated as 'absent'" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    # every branch-deciding probe goes through the helper; the four remaining
    # raw `sudo test` sites all FATAL on failure (audited), and no NEW raw
    # branch-deciding probe may appear on DEST/marker paths
    assert 'if sudo test -e "$DEST"' not in code
    assert 'if sudo test -f "$DEST/$MARKER"' not in code


def test_post_stop_container_polls_fail_closed():
    """The post-stop poll's `docker ps | grep || break` treated a daemon outage
    as "stopped", and the final check skipped its fatal branch on the same
    producer error — the script logged `stopped` and copied with the writer
    state unknown, risking the torn snapshot the stop prevents (review: Codex).
    All container-state queries now route through one fail-closed helper."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "container_running()" in src
    assert "cannot query container state mid-run" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "grep -qx \"$CONTAINER\" || break" not in code


def test_compose_guard_validates_the_pending_file_not_the_live_one():
    """In the mandated order the script runs BEFORE step A deploys, when the
    live checkout's compose file still carries the OLD in-checkout mount —
    validating that file refuses every legitimate pre-deploy run (review:
    Codex). The step-A file ships with the script, so the guard uses the
    script's own repo for the FILE and the live repo as --project-directory,
    which is exactly the future post-deploy invocation."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert 'SCRIPT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"' in src
    assert '--project-directory "$REPO_ROOT"' in src
    assert 'ZOE_MA_COMPOSE_FILE:-$SCRIPT_REPO' in src


def test_missing_pending_compose_file_is_fatal():
    """A misspelled ZOE_MA_COMPOSE_FILE silently skipped the effective-mount
    validation: copy done, marker written, exit 0, future mount unvalidated
    (review: Codex, reproduced). The pending file ships with the script, so
    absence is always a broken checkout or a typo — never a skip."""
    src = (ROOT / "scripts" / "maintenance" / "migrate_music_assistant_data.sh").read_text()
    assert "pending compose file not found" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert 'if [[ -f "$COMPOSE_FILE" ]]; then' not in code, "the fail-open wrapper must not return"
