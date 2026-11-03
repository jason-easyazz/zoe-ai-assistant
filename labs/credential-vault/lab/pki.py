"""The lab CA: one throwaway CA and one server certificate for every lab host name, valid for two days.
Run once per lab run into a fresh Docker volume; the volume is removed at teardown. Synthetic, never reused."""

from __future__ import annotations

import datetime as dt
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

sys.path.insert(0, os.path.dirname(__file__))
from hosts import ALL_TLS_HOSTS  # noqa: E402


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def main(out: str) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(_name("credential-vault lab CA")).issuer_name(_name("credential-vault lab CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(_name("credential-vault lab server")).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in ALL_TLS_HOSTS]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem_cert = cert.public_bytes(serialization.Encoding.PEM)
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    with open(os.path.join(out, "ca.crt"), "wb") as fh:
        fh.write(ca.public_bytes(serialization.Encoding.PEM))
    os.chmod(os.path.join(out, "ca.crt"), 0o644)
    for sub, uid in (("vw", 1000), ("sites", 2100)):
        d = os.path.join(out, sub)
        os.makedirs(d, exist_ok=True)
        for name, data in (("server.crt", pem_cert), ("server.key", pem_key)):
            p = os.path.join(d, name)
            with open(p, "wb") as fh:
                fh.write(data)
            os.chown(p, uid, uid)
            os.chmod(p, 0o400)
        os.chown(d, uid, uid)
        os.chmod(d, 0o500)
    os.chmod(out, 0o755)


if __name__ == "__main__":
    main(sys.argv[1])
