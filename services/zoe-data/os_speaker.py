"""The Zoe OS kernel's per-start secrets, read the way zoe-data reads ``creds.json``: speaker assertions, the eraser, the reconciler.

zoe-data is the component that VERIFIES people (the voice match, the panel session), so it is the kernel's ``speaker-verifier``: a
person-bound request it sends (forget, erase_item, undo, ...) carries a ``speaker`` assertion minted HERE for the person zoe-data has
already verified, with the audience that request needs. Written from the spec (``labs/zoe-os/README.md`` "Speaker assertions" and
"The reconcile digest"); this module imports NOTHING from ``labs/`` (``test_zoe_data_never_imports_the_lab_kernel``), and
``tests/test_os_speaker_contract.py`` proves the lab kernel accepts what it mints.

* ``identity-creds.json`` ``{"socket", "creds": {"speaker-verifier": hex}, "generation": 32 hex, "ttl_s"}``: the minting secret and
  the kernel RUN's generation (every token names it, ``"g"``), both FRESH at every kernel start, so they are read again for every mint
  (a small file; person-bound requests are rare) and a restart never strands an old secret or generation.
* ``erase-creds.json`` ``{"socket", "creds": {"eraser": hex}}``: the member-delete path's own credential (the forget family, no
  assertion). Only the authority kernel writes it.
* ``reconcile-creds.json`` ``{"socket", "creds": {"reconciler": hex}, "digest_key": hex}``: the only credential ``list_digest`` answers,
  and the key of its keyed digest (``keyed_digest``).

Every file must be 0600 (owner-only): a looser one is treated as absent. A token, a secret or a key is never logged, printed or
stored; failures raise ``os_shadow.KernelUnavailable`` with a fixed code.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from typing import Optional

IDENTITY = "speaker-verifier"
IDENTITY_CREDS = "identity-creds.json"
ERASER = "eraser"
ERASE_CREDS = "erase-creds.json"
RECONCILER = "reconciler"
RECONCILE_CREDS = "reconcile-creds.json"
#: the kernel's fixed refusal for a person-bound request without a valid assertion (the lab speaker module's ``UNPROVEN``)
UNPROVEN = "speaker unproven"
AUDIENCES = frozenset({"forget.person", "forget.scoped", "grant", "revoke", "undo", "explain"})
_DOMAIN = b"zoe-os speaker assertion v1\0"
_DIGEST_DOMAIN = b"zoe-os list digest v1\0"


def _unavailable(code: str):
    import os_shadow

    return os_shadow.KernelUnavailable(code)


def _read(name: str) -> dict:
    """One credential file from the provisioned state directory, read fresh. Absent, unreadable or not owner-only = unavailable."""
    import os_shadow

    path = os_shadow.state_dir() / name
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise _unavailable("no_" + name.split("-")[0]) from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            raise _unavailable("loose_" + name.split("-")[0])
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            raw = json.load(fh)
        if not isinstance(raw, dict) or not isinstance(raw.get("creds"), dict):
            raise ValueError("shape")
        return raw
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise _unavailable("bad_" + name.split("-")[0]) from exc
    finally:
        if fd >= 0:
            os.close(fd)


def present(name: str) -> bool:
    """The kernel provisioned this file (it may still be unreadable: the caller then fails as it would on any read)."""
    import os_shadow

    try:
        return (os_shadow.state_dir() / name).is_file()
    except OSError:
        return False


def _secret(raw: dict, who: str) -> bytes:
    try:
        key = bytes.fromhex(str(raw["creds"][who]))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unavailable("no_credential") from exc
    if len(key) < 32:
        raise _unavailable("short_secret")
    return key


def _ms(seconds: float) -> int:
    """Milliseconds ROUNDED, never truncated: the kernel rounds the same way on verify (``speaker._ms``)."""
    return int(round(seconds * 1000))


_GENERATION = re.compile(r"[0-9a-f]{32}")


def identity() -> tuple[bytes, str]:
    """``(key, generation)`` of the running kernel, read now from ``identity-creds.json``."""
    raw = _read(IDENTITY_CREDS)
    gen = raw.get("generation")
    if not isinstance(gen, str) or not _GENERATION.fullmatch(gen):
        raise _unavailable("no_generation")
    return _secret(raw, IDENTITY), gen


def mint(person: str, audience: str, *, key: Optional[bytes] = None, generation: Optional[str] = None,
         now: Optional[float] = None) -> str:
    """One assertion that ``person`` (already verified by zoe-data) decided a request of ``audience``, for the kernel run
    ``generation`` names. One per request: the kernel spends its nonce. Never log the return value. Body: unpadded base64url of the
    compact, key-sorted JSON ``{"a", "g", "n", "p", "t"}``; MAC: hex HMAC-SHA256 over the domain plus the body."""
    if audience not in AUDIENCES:
        raise ValueError("unknown audience")
    if not isinstance(person, str) or not person:
        raise ValueError("no person")
    if key is None or generation is None:
        key, generation = identity()
    claims = {"p": person, "t": _ms(time.time() if now is None else now), "n": secrets.token_hex(16), "a": audience,
              "g": generation}
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=")
    mac = hmac.new(key, _DOMAIN + body, hashlib.sha256).hexdigest().encode()
    return (body + b"." + mac).decode()


def speaker_arg(person: str, audience: str) -> dict:
    """``{"speaker": token}`` when the kernel provisioned an identity secret, else ``{}`` (an older kernel takes no ``speaker``
    argument at all, and refuses an unknown one). A provisioned but unreadable secret raises: never a silent unproven request."""
    if not present(IDENTITY_CREDS):
        return {}
    return {"speaker": mint(person, audience)}


def is_unproven(reason: str) -> bool:
    return UNPROVEN in (reason or "").lower()


def eraser_creds() -> tuple[str, dict]:
    """``(socket, {"eraser": secret})`` for ``os_shadow._Kernel``."""
    raw = _read(ERASE_CREDS)
    return str(raw.get("socket", "")), {ERASER: _secret(raw, ERASER)}


def reconciler_creds() -> tuple[str, bytes, bytes]:
    """``(socket, reconciler secret, digest key)``. Read at every run: the key is fresh at every kernel start."""
    raw = _read(RECONCILE_CREDS)
    try:
        key = bytes.fromhex(str(raw["digest_key"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unavailable("no_digest_key") from exc
    if len(key) < 32:
        raise _unavailable("short_secret")
    return str(raw.get("socket", "")), _secret(raw, RECONCILER), key


def keyed_digest(key: bytes, member: str, list_name: str, item: str) -> str:
    """The kernel's ``authority.keyed_digest``: HMAC-SHA256 under the per-start digest key over the domain plus the compact JSON
    array ``[member, list, item.strip().lower()]`` (``ensure_ascii=False``), 64 hex. Equal items of two members or two lists differ."""
    msg = json.dumps([member, list_name, item.strip().lower()], separators=(",", ":"), ensure_ascii=False).encode()
    return hmac.new(key, _DIGEST_DOMAIN + msg, hashlib.sha256).hexdigest()
