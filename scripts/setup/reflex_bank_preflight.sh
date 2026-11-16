#!/usr/bin/env bash
# Sourced by deploy-pi-voice.sh (and by tests/unit/test_deploy_pi_voice_script.py): the reflex-bank preflight as a function.
#
#   reflex_bank_preflight <bank-root> <path to zoe_voice_reflex.py> <files-from output path>
#
# Prints ONE enum / count line (never file contents) and returns 0 only for a tree that is EXACTLY a rendered bank: the PANEL'S
# OWN loader (load_bank_dir, stdlib-only, isolated mode) passes every <voice>/<sha8>/, AND each bank directory's entry set (read
# WITHOUT following symlinks) equals the manifest's file set: manifest.json + one <id>.wav per listed take, each a regular file.
# An extra file / subdirectory / symlink / hidden or special file, a listed file that is a symlink or not regular, or a missing one
# is refused. On success the exact relative paths are written, one per line, to the third argument for ``rsync --files-from``,
# so what is checked is the only thing that is shipped.
reflex_bank_preflight() {
  python3 -I - "$1" "$2" "$3" <<'PY'
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("zoe_voice_reflex_preflight", sys.argv[2])
rx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rx
spec.loader.exec_module(rx)
root, banks, ship = Path(sys.argv[1]), 0, []
s = rx.Settings(enabled=True)
for v in sorted(root.iterdir()):
    if v.is_symlink() or not v.is_dir() or not rx.VOICE_RE.fullmatch(v.name):
        print("bad_voice_dir"); sys.exit(1)
    for b in sorted(v.iterdir()):
        if b.is_symlink() or not b.is_dir() or not rx.BANK_SHA_RE.fullmatch(b.name):
            print("bad_bank_dir"); sys.exit(1)
        res = rx.load_bank_dir(b, v.name, s)
        if res.bank is None:
            print(res.reason); sys.exit(1)
        want = {"manifest.json"} | {f"{t}.wav" for t in res.bank.takes}
        have, not_regular = set(), 0
        with os.scandir(b) as it:
            for e in it:
                have.add(e.name)
                if not e.is_file(follow_symlinks=False):
                    not_regular += 1
        if have != want or not_regular:
            print(f"entry_set_mismatch extra={len(have - want)} missing={len(want - have)} not_regular={not_regular}")
            sys.exit(1)
        ship += [f"{v.name}/{b.name}/{n}" for n in sorted(want)]
        banks += 1
if not banks:
    print("missing"); sys.exit(1)
Path(sys.argv[3]).write_text("".join(p + "\n" for p in ship))
print("ok")
PY
}
