"""The adopted vault client: the official `bw` CLI, run only as the broker's own user (credential-vault.md section 3).

The session key lives in this process's memory and reaches `bw` only through its environment (never argv, which
other users can read in /proc). The CLI's data directory is the broker's private state directory (ciphertext cache and
the session-protected user key). `lock()` forgets the session key and runs `bw lock`.

Decrypted item JSON passes through here on its way to the fill; callers take what they need and drop the rest.
Python strings cannot be zeroised, so "wiped from memory" means "no reference kept", which is best effort.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass


class VaultError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ItemMeta:
    item_id: str
    org_id: str | None
    collection_ids: list[str]
    name: str
    uris: list[dict]
    fields: dict            # non-hidden custom fields whose name starts with "zoe:" only
    deleted: bool


ZOE_FIELD_PREFIX = "zoe:"


def item_meta(raw: dict) -> ItemMeta:
    """Metadata only. Usernames, passwords, notes, TOTP and every non-`zoe:` custom field value are dropped."""
    login = raw.get("login") or {}
    uris = [{"uri": u.get("uri"), "match": u.get("match")} for u in (login.get("uris") or []) if isinstance(u, dict)]
    fields = {}
    for f in raw.get("fields") or []:
        name = (f or {}).get("name") or ""
        # type 0 = text, 1 = hidden: a hidden zoe: field is ignored (it is the member's secret, not config)
        if name.startswith(ZOE_FIELD_PREFIX) and f.get("type") == 0 and isinstance(f.get("value"), str):
            fields[name] = f["value"]
    return ItemMeta(item_id=raw.get("id") or "", org_id=raw.get("organizationId"),
                    collection_ids=list(raw.get("collectionIds") or []), name=raw.get("name") or "",
                    uris=uris, fields=fields, deleted=bool(raw.get("deletedDate")))


class BwClient:
    def __init__(self, *, bw_path: str, appdata_dir: str, server: str, email: str,
                 extra_ca: str | None = None, timeout_s: float = 90.0) -> None:
        self.bw_path = bw_path
        self.appdata_dir = appdata_dir
        self.server = server
        self.email = email
        self.extra_ca = extra_ca
        self.timeout_s = timeout_s
        self._session: str | None = None
        self._lock = threading.RLock()
        self.last_sync: float | None = None
        self.calls = 0

    # -- process plumbing ----------------------------------------------------------------------------------------
    def _env(self, extra: dict | None = None) -> dict:
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": self.appdata_dir,
            "BITWARDENCLI_APPDATA_DIR": self.appdata_dir,
            "BW_NOINTERACTION": "true",
            "LANG": "C.UTF-8",
        }
        if self.extra_ca:
            env["NODE_EXTRA_CA_CERTS"] = self.extra_ca
        if extra:
            env.update(extra)
        return env

    def _run(self, args: list[str], *, session: bool = True, stdin: str | None = None,
             extra_env: dict | None = None) -> subprocess.CompletedProcess:
        extra = dict(extra_env or {})
        if session:
            if not self._session:
                raise VaultError("locked")
            extra["BW_SESSION"] = self._session
        self.calls += 1
        try:
            return subprocess.run([self.bw_path, *args, "--nointeraction"], input=stdin, capture_output=True,
                                  text=True, env=self._env(extra), timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise VaultError("vault_timeout") from exc

    # -- lifecycle -----------------------------------------------------------------------------------------------
    @property
    def unlocked(self) -> bool:
        return self._session is not None

    def _status(self) -> dict:
        r = self._run(["status"], session=False)
        try:
            return json.loads(r.stdout or "{}")
        except ValueError:
            return {}

    def unlock(self, *, password_file: str | None = None, password: str | None = None) -> None:
        """Log in if needed, then unlock. The password comes from a 0600 credential file (U1) or from the phone
        unlock page (U3), and reaches `bw` through --passwordfile or a private environment variable."""
        if (password_file is None) == (password is None):
            raise VaultError("unlock_needs_one_secret_source")
        with self._lock:
            if password_file is not None:
                src = ["--passwordfile", password_file]
                extra = None
            else:
                src = ["--passwordenv", "ZOE_VAULTD_PW"]
                extra = {"ZOE_VAULTD_PW": password}
            status = self._status().get("status")
            if status == "unauthenticated":
                cfg = self._run(["config", "server", self.server], session=False)
                if cfg.returncode != 0:
                    raise VaultError("vault_config_failed")
                r = self._run(["login", self.email, *src, "--raw"], session=False, extra_env=extra)
            else:
                r = self._run(["unlock", *src, "--raw"], session=False, extra_env=extra)
            key = (r.stdout or "").strip()
            if r.returncode != 0 or not key or "\n" in key:
                raise VaultError("unlock_failed")
            self._session = key

    def lock(self) -> None:
        with self._lock:
            had = self._session is not None
            if had:
                try:
                    self._run(["lock"])
                except VaultError:
                    pass
            self._session = None

    # -- reads ---------------------------------------------------------------------------------------------------
    def sync(self) -> None:
        with self._lock:
            r = self._run(["sync"])
            if r.returncode != 0:
                raise VaultError("vault_unreachable")
            self.last_sync = time.time()

    def list_org_items(self, org_id: str) -> list[ItemMeta]:
        with self._lock:
            r = self._run(["list", "items", "--organizationid", org_id])
            if r.returncode != 0:
                raise VaultError("vault_list_failed")
            try:
                raw = json.loads(r.stdout or "[]")
            except ValueError as exc:
                raise VaultError("vault_bad_output") from exc
            out = [item_meta(x) for x in raw if isinstance(x, dict)]
            del raw
            return out

    def get_item(self, item_id: str) -> dict | None:
        """The full decrypted item, for the fill only. None when the item is gone or not visible."""
        with self._lock:
            r = self._run(["get", "item", item_id])
            if r.returncode != 0:
                return None
            try:
                obj = json.loads(r.stdout or "null")
            except ValueError:
                return None
            return obj if isinstance(obj, dict) else None


def appdata_ok(path: str) -> bool:
    """The CLI's data directory must be private to the broker's uid (0700, owned by us)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_uid == os.getuid() and (st.st_mode & 0o077) == 0
