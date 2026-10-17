"""Throwaway PKI for the F0 lab, generated with the host `openssl` CLI into a 0700 run directory.

Nothing here is ever committed or reused: every run mints fresh keys and the run directory is deleted at teardown
(and teardown asserts it is gone). EC P-256 throughout.

  relay CA  -> relay server cert (SAN: relay.lab)           frpc pins the CA (transport.tls.trustedCaFile)
  rogue CA  -> rogue relay cert  (SAN: relay.lab)           T9: a relay presenting a cert the box does not trust
  household CA (per hid) -> leaf for <hid>.zoe.internal     the box serves it; the panel pins the CA
                         -> panel client cert (clientAuth)  mTLS on the relay listener
  evil CA   -> leaf for <hidA>.zoe.internal                 T4: trusted by the panel's SYSTEM store, not by the pin
"""
from __future__ import annotations

import os
import subprocess
import tempfile


def _run(*args: str, cwd: str | None = None) -> None:
    subprocess.run(args, check=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def gen_key(path: str) -> None:
    _run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", path)
    os.chmod(path, 0o600)


def gen_ca(dirpath: str, name: str) -> tuple[str, str]:
    key, crt = os.path.join(dirpath, "ca.key"), os.path.join(dirpath, "ca.pem")
    os.makedirs(dirpath, exist_ok=True)
    gen_key(key)
    _run("openssl", "req", "-new", "-x509", "-key", key, "-sha256", "-days", "30", "-subj", f"/CN={name}",
         "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-addext", "subjectKeyIdentifier=hash", "-out", crt)
    return key, crt


def sign_leaf(dirpath: str, ca_key: str, ca_crt: str, *, cn: str, san: str | None, eku: str, stem: str) -> tuple[str, str]:
    """Issue <stem>.key / <stem>.pem in dirpath, signed by the given CA."""
    key, crt = os.path.join(dirpath, stem + ".key"), os.path.join(dirpath, stem + ".pem")
    csr = os.path.join(dirpath, stem + ".csr")
    gen_key(key)
    _run("openssl", "req", "-new", "-key", key, "-subj", f"/CN={cn}", "-out", csr)
    ext = ["basicConstraints=critical,CA:FALSE", "keyUsage=critical,digitalSignature",
           f"extendedKeyUsage={eku}", "subjectKeyIdentifier=hash", "authorityKeyIdentifier=keyid"]
    if san:
        ext.append(f"subjectAltName=DNS:{san}")
    with tempfile.NamedTemporaryFile("w", suffix=".ext", dir=dirpath, delete=False) as f:
        f.write("\n".join(ext) + "\n")
        extfile = f.name
    try:
        _run("openssl", "x509", "-req", "-in", csr, "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial",
             "-days", "7", "-sha256", "-extfile", extfile, "-out", crt)
    finally:
        os.unlink(extfile)
        os.unlink(csr)
    return key, crt


def fingerprint_sha1(crt: str) -> str:
    """nginx's $ssl_client_fingerprint: lower-case hex SHA-1 of the DER certificate."""
    out = subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-fingerprint", "-sha1"], check=True,
                         capture_output=True, text=True).stdout
    return out.split("=", 1)[1].strip().replace(":", "").lower()


def spki_sha256(path: str) -> str:
    """SHA-256 of the DER public key of a private key OR certificate file (to match a key by public half)."""
    import hashlib
    with open(path, encoding="ascii", errors="ignore") as f:
        is_key = "PRIVATE KEY" in f.read()
    if is_key:
        der = subprocess.run(["openssl", "pkey", "-in", path, "-pubout", "-outform", "DER"], check=True,
                             capture_output=True).stdout
    else:
        pub = subprocess.run(["openssl", "x509", "-in", path, "-noout", "-pubkey"], check=True, capture_output=True).stdout
        der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=pub, check=True,
                             capture_output=True).stdout
    return hashlib.sha256(der).hexdigest()


def build_all(root: str, hids: dict[str, str], relay_name: str = "relay.lab") -> dict:
    """Create the whole lab PKI under root; returns a dict of paths. hids = {"a": "<hid-a>", "b": "<hid-b>"}."""
    os.makedirs(root, mode=0o700, exist_ok=True)
    paths: dict = {}
    # relay (genuine) and rogue
    for who in ("relay", "rogue"):
        d = os.path.join(root, who)
        ck, cc = gen_ca(d, f"lab {who} CA")
        sk, sc = sign_leaf(d, ck, cc, cn=relay_name, san=relay_name, eku="serverAuth", stem="server")
        paths[who] = {"ca": cc, "cert": sc, "key": sk}
    # evil CA (in the panel's system store, never the pin)
    d = os.path.join(root, "evil")
    ek, ec = gen_ca(d, "lab evil public-ish CA")
    paths["evil"] = {"ca": ec, "key": ek}
    # households
    for tag, hid in hids.items():
        d = os.path.join(root, "hh-" + tag)
        ck, cc = gen_ca(d, f"household {hid} CA")
        lk, lc = sign_leaf(d, ck, cc, cn=f"{hid}.zoe.internal", san=f"{hid}.zoe.internal", eku="serverAuth", stem="leaf")
        pk, pc = sign_leaf(d, ck, cc, cn=f"panel-{hid}", san=None, eku="clientAuth", stem="panel")
        paths[tag] = {"hid": hid, "dir": d, "ca": cc, "ca_key": ck, "leaf": lc, "leaf_key": lk, "panel_cert": pc, "panel_key": pk,
                      "panel_fp": fingerprint_sha1(pc)}
    # evil leaf for household a's name
    ak = paths["a"]["hid"]
    lk, lc = sign_leaf(paths["evil"]["key"].rsplit("/", 1)[0], ek, ec, cn=f"{ak}.zoe.internal", san=f"{ak}.zoe.internal",
                       eku="serverAuth", stem="leaf-a")
    paths["evil"].update({"leaf": lc, "leaf_key": lk})
    return paths
