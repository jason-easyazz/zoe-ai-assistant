#!/usr/bin/env python3
"""The household CA for the panel relay (panel-anywhere section 3, phase R1): the CA, the box's leaf for ``<hid>.zoe.internal``, and
the panels' client certificates signed by hand from the panel's own CSR (section 6.1).

    household_pki.py init-ca    [--ca-dir ~/.zoe/relay-ca]
    household_pki.py issue-leaf --hid h1a2b3c4d5 [--out-dir <repo>/ssl/relay] [--days 90]
    household_pki.py sign-csr   --csr panel.csr --panel-id kitchen-panel [--out panel.pem] [--days 365]
    household_pki.py fingerprint panel.pem

What goes where (nothing here is ever committed; ``ssl/relay/`` is gitignored):

  * ``ca.key`` NEVER leaves ``--ca-dir`` (default ``~/.zoe/relay-ca``, mode 0700, OUTSIDE the docker-mounted ``ssl/`` tree): nginx only
    needs ``ca.pem``. The CA key is what mints a panel certificate; whoever holds it can make a relay client. Back it up offline.
  * ``issue-leaf`` writes ``ssl/relay/{ca.pem,leaf.pem,leaf.key}`` - exactly what ``enable_relay_listener.py`` requires. The leaf is the
    box's server certificate for ``<hid>.zoe.internal`` (short-lived: re-run before it expires, then ``docker exec zoe-ui nginx -s reload``).
  * ``sign-csr`` signs a certificate for a key the PANEL generated (its private key never leaves the panel). The certificate carries
    ONLY clientAuth - it cannot be used as a server certificate - and prints its SHA-1 fingerprint, which is what nginx forwards as
    ``X-Zoe-Client-Cert-Fp`` and what ``PUT /api/panels/<panel>/client-certs/<fingerprint>`` binds to the panel.

Uses the host ``openssl`` (EC P-256, SHA-256). Python >= 3.10, stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Same shape as relay_ingress._HOST_RE: random, lower-case, never derived from a name.
HID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{4,60}[a-z0-9]$")
PANEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
NAME_SUFFIX = ".zoe.internal"
DEFAULT_CA_DIR = Path("~/.zoe/relay-ca").expanduser()


def _openssl(*args: str, stdin: bytes | None = None) -> str:
    r = subprocess.run(["openssl", *args], input=stdin, capture_output=True, check=False)
    if r.returncode != 0:
        raise SystemExit("openssl %s failed: %s" % (" ".join(args[:2]), r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout.decode("utf-8", "replace")


def _private_write(path: Path, data: str = "") -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)


def fingerprint_sha1(cert: str | os.PathLike) -> str:
    """nginx's ``$ssl_client_fingerprint``: lower-case hex SHA-1 of the DER certificate."""
    out = _openssl("x509", "-in", str(cert), "-noout", "-fingerprint", "-sha1")
    return out.split("=", 1)[1].strip().replace(":", "").lower()


def init_ca(ca_dir: Path, *, name: str = "Zoe household CA", days: int = 3650) -> Path:
    ca_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ca_dir, 0o700)
    key, crt = ca_dir / "ca.key", ca_dir / "ca.pem"
    if key.exists() or crt.exists():
        raise SystemExit("refusing to overwrite an existing CA in %s" % ca_dir)
    _private_write(key)
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key))
    _openssl("req", "-new", "-x509", "-key", str(key), "-sha256", "-days", str(days), "-subj", "/CN=%s" % name,
             "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash", "-out", str(crt))
    return crt


def _sign(csr: Path, ca_dir: Path, out: Path, *, days: int, extensions: list[str]) -> None:
    ca_key, ca_crt = ca_dir / "ca.key", ca_dir / "ca.pem"
    if not ca_key.exists() or not ca_crt.exists():
        raise SystemExit("no CA in %s - run init-ca first" % ca_dir)
    with tempfile.NamedTemporaryFile("w", suffix=".ext", delete=False) as f:
        f.write("\n".join(extensions) + "\n")
        extfile = f.name
    try:
        _openssl("x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
                 "-days", str(days), "-sha256", "-extfile", extfile, "-out", str(out))
    finally:
        os.unlink(extfile)


def issue_leaf(hid: str, ca_dir: Path, out_dir: Path, *, days: int = 90) -> Path:
    if not HID_RE.match(hid):
        raise SystemExit("hid must be 6-62 characters of a-z, 0-9 and '-' (random; never derived from a name)")
    name = hid + NAME_SUFFIX
    out_dir.mkdir(parents=True, exist_ok=True)
    key, crt = out_dir / "leaf.key", out_dir / "leaf.pem"
    _private_write(key)
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key))
    with tempfile.TemporaryDirectory() as tmp:
        csr = Path(tmp) / "leaf.csr"
        _openssl("req", "-new", "-key", str(key), "-subj", "/CN=%s" % name, "-out", str(csr))
        _sign(csr, ca_dir, crt, days=days, extensions=[
            "basicConstraints=critical,CA:FALSE", "keyUsage=critical,digitalSignature", "extendedKeyUsage=serverAuth",
            "subjectKeyIdentifier=hash", "authorityKeyIdentifier=keyid", "subjectAltName=DNS:%s" % name])
    (out_dir / "ca.pem").write_bytes((ca_dir / "ca.pem").read_bytes())
    os.chmod(out_dir / "ca.pem", 0o644)
    os.chmod(crt, 0o644)
    return crt


def sign_csr(csr: Path, panel_id: str, ca_dir: Path, out: Path, *, days: int = 365) -> str:
    if not PANEL_ID_RE.match(panel_id):
        raise SystemExit("panel id may contain only letters, digits, '_', '.' and '-'")
    _openssl("req", "-in", str(csr), "-noout", "-verify")  # the CSR must be signed by the key it carries (proof of possession)
    # `x509 -req` does NOT copy the CSR's requested extensions, so a panel cannot ask for CA:TRUE or serverAuth; what the
    # certificate may do is fixed here. The subject is informational: the binding that authorises a certificate is its
    # fingerprint, registered against ONE panel (PUT /api/panels/<panel>/client-certs/<fingerprint>), never its name.
    _sign(csr, ca_dir, out, days=days, extensions=[
        "basicConstraints=critical,CA:FALSE", "keyUsage=critical,digitalSignature", "extendedKeyUsage=clientAuth",
        "subjectKeyIdentifier=hash", "authorityKeyIdentifier=keyid"])
    os.chmod(out, 0o644)
    return fingerprint_sha1(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-ca")
    p.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    p = sub.add_parser("issue-leaf")
    p.add_argument("--hid", required=True)
    p.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    p.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parents[3] / "ssl" / "relay")
    p.add_argument("--days", type=int, default=90)
    p = sub.add_parser("sign-csr")
    p.add_argument("--csr", type=Path, required=True)
    p.add_argument("--panel-id", required=True)
    p.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--days", type=int, default=365)
    p = sub.add_parser("fingerprint")
    p.add_argument("cert", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "init-ca":
        print("CA certificate:", init_ca(a.ca_dir))
        print("CA key stays in %s (0700). Back it up offline; it is the only thing that mints relay clients." % a.ca_dir)
    elif a.cmd == "issue-leaf":
        crt = issue_leaf(a.hid, a.ca_dir, a.out_dir, days=a.days)
        print("leaf for %s%s: %s (valid %d days; also wrote ca.pem and leaf.key beside it)" % (a.hid, NAME_SUFFIX, crt, a.days))
    elif a.cmd == "sign-csr":
        out = a.out or a.csr.with_suffix(".pem")
        fp = sign_csr(a.csr, a.panel_id, a.ca_dir, out, days=a.days)
        print("client certificate:", out)
        print("fingerprint (bind it): %s" % fp)
        print("  PUT /api/panels/%s/client-certs/%s   (admin session, ZOE_RELAY_INGRESS on)" % (a.panel_id, fp))
    else:
        print(fingerprint_sha1(a.cert))
    return 0


if __name__ == "__main__":
    sys.exit(main())
