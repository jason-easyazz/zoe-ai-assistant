"""Validate a harvested cookie against Music Assistant, reversibly.

LAB-ONLY. See ./AGENTS.md.

Reuses the *production* bridge ``services/zoe-data/music_service.py`` (read-only
import — nothing is wired the other way) to:
  1. probe the local PO-token generator (ytmusic-potoken :4416),
  2. save the ytmusic provider with {username, cookie}  (po_token URL auto-injected),
  3. confirm the account resolves: providers list + a library search + players.

Everything is reversible:  python3 validate.py --remove

The cookie is read from the gitignored secret file written by harvest.py (or
$ZOE_YTMUSIC_COOKIE). MA creds are read from services/zoe-data/.env the same way
the live service reads them. No secret is printed.

    python3 validate.py --username you@gmail.com
    python3 validate.py --remove
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import common

REPO_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = REPO_ROOT / "services" / "zoe-data"
INSTANCE_FILE = common.SECRET_DIR / "ytmusic.instance_id"


def _load_ma_env() -> None:
    """Populate MUSIC_ASSISTANT_URL/TOKEN from services/zoe-data/.env if unset,
    so this lab script authenticates exactly like the live service does."""
    env_path = ZOE_DATA / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in ("MUSIC_ASSISTANT_URL", "MUSIC_ASSISTANT_TOKEN") and key not in os.environ:
            os.environ[key] = val.strip()


def _read_cookie() -> str:
    env = os.environ.get("ZOE_YTMUSIC_COOKIE")
    if env:
        return env
    if common.COOKIE_FILE.exists():
        return common.COOKIE_FILE.read_text(encoding="utf-8").strip()
    return ""


async def _do_save(ms, username: str, cookie: str) -> int:
    url = ms._ytmusic_potoken_url()
    if not await ms._potoken_reachable(url):
        print(f"PO-token generator not reachable at {url}", file=sys.stderr)
        print("start it:  docker compose -f docker-compose.modules.yml up -d ytmusic-potoken", file=sys.stderr)
        return 2
    print(f"PO-token generator reachable at {url} ✓")
    print(f"saving ytmusic provider (cookie {common.redact(cookie)}) …")
    saved = await ms.save_provider("ytmusic", {"username": username, "cookie": cookie})
    if not saved:
        print("MA rejected the save (bad/expired cookie, or missing __Secure-3PAPISID?).", file=sys.stderr)
        return 1
    instance_id = saved.get("instance_id") or saved.get("id") or ""
    if instance_id:
        common.ensure_secret_dir()
        INSTANCE_FILE.write_text(instance_id, encoding="utf-8")
    print(f"saved ytmusic provider (instance_id={instance_id or '?'}) ✓")

    # Give MA a moment, then confirm the account actually resolved.
    await asyncio.sleep(3)
    providers = await ms._ma("config/providers") or []
    connected = any(isinstance(p, dict) and p.get("domain") == "ytmusic" for p in providers)
    print(f"ytmusic in configured providers: {'yes ✓' if connected else 'no ✗'}")
    hit = await ms._ma("music/search", search_query="daft punk", media_types=["artist", "track"], limit=1)
    ok_search = isinstance(hit, dict) and any(hit.get(k) for k in ("artists", "tracks"))
    print(f"library/search resolves via the account: {'yes ✓' if ok_search else 'no (still syncing?)'}")
    players = await ms.get_players()
    print(f"players available for playback: {len(players)}")
    print("\nto play a track through the account:  (in a Zoe voice turn) “play <song>”")
    print("to undo:  python3 validate.py --remove")
    return 0 if connected else 1


async def _do_remove(ms) -> int:
    instance_id = ""
    if INSTANCE_FILE.exists():
        instance_id = INSTANCE_FILE.read_text(encoding="utf-8").strip()
    if not instance_id:
        providers = await ms._ma("config/providers") or []
        for p in providers:
            if isinstance(p, dict) and p.get("domain") == "ytmusic":
                instance_id = p.get("instance_id") or p.get("id") or ""
                break
    if not instance_id:
        print("no ytmusic provider instance found to remove.")
        return 0
    await ms._ma("config/providers/remove", instance_id=instance_id)
    print(f"removed ytmusic provider instance {instance_id} ✓")
    INSTANCE_FILE.unlink(missing_ok=True)
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="Validate/remove the ytmusic provider in Music Assistant.")
    ap.add_argument("--username", default=os.environ.get("ZOE_YTMUSIC_USERNAME", ""), help="the account's email/label")
    ap.add_argument("--remove", action="store_true", help="remove the ytmusic provider and exit")
    args = ap.parse_args()

    _load_ma_env()
    if not os.environ.get("MUSIC_ASSISTANT_TOKEN"):
        print("MUSIC_ASSISTANT_TOKEN not set and not found in services/zoe-data/.env", file=sys.stderr)
        return 2

    sys.path.insert(0, str(ZOE_DATA))
    import music_service as ms  # production bridge, imported read-only

    if args.remove:
        return await _do_remove(ms)

    cookie = _read_cookie()
    if not cookie:
        print(f"no cookie found. run harvest.py first (writes {common.COOKIE_FILE}) "
              "or set ZOE_YTMUSIC_COOKIE.", file=sys.stderr)
        return 2
    _, names = common.assemble_cookie_header(
        [{"name": kv.split("=", 1)[0].strip(), "value": "x", "domain": ".youtube.com"}
         for kv in cookie.split(";") if "=" in kv]
    )
    if not common.has_required(names):
        print(f"cookie is missing {common.REQUIRED_COOKIE} — MA will reject it.", file=sys.stderr)
        return 1
    if not args.username:
        print("pass --username (the account email/label MA stores alongside the cookie).", file=sys.stderr)
        return 2
    return await _do_save(ms, args.username, cookie)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
