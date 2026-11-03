"""Seeding the lab vault: a scripted family registers, makes organisations and items, the way the Bitwarden web vault
would. The `bw` CLI cannot register accounts or create organisations, so this module does the client-side crypto
(Bitwarden security white paper: PBKDF2-SHA256 master key, HKDF-expand stretch, AES-256-CBC + HMAC-SHA256 EncString
type 2, RSA-2048 OAEP-SHA1 for organisation keys) against Vaultwarden's API. LAB SEEDING ONLY: the broker never uses
it; it reads the vault with the official `bw` CLI. Every value it writes is synthetic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as apad
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

KDF_ITERATIONS = 600_000


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def enc(key64: bytes, plaintext: bytes | str) -> str:
    if isinstance(plaintext, str):
        plaintext = plaintext.encode()
    ek, mk = key64[:32], key64[32:]
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext) + padder.finalize()
    e = Cipher(algorithms.AES(ek), modes.CBC(iv)).encryptor()
    ct = e.update(data) + e.finalize()
    mac = hmac.new(mk, iv + ct, hashlib.sha256).digest()
    return f"2.{b64(iv)}|{b64(ct)}|{b64(mac)}"


def dec(key64: bytes, s: str) -> bytes:
    kind, rest = s.split(".", 1)
    assert kind == "2"
    iv, ct, mac = (base64.b64decode(x) for x in rest.split("|"))
    if not hmac.compare_digest(hmac.new(key64[32:], iv + ct, hashlib.sha256).digest(), mac):
        raise ValueError("mac")
    d = Cipher(algorithms.AES(key64[:32]), modes.CBC(iv)).decryptor()
    data = d.update(ct) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    return u.update(data) + u.finalize()


def rsa_enc(pub_der_b64: str, data: bytes) -> str:
    pub = serialization.load_der_public_key(base64.b64decode(pub_der_b64))
    ct = pub.encrypt(data, apad.OAEP(mgf=apad.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    return "4." + b64(ct)


def new_keypair(sym_key: bytes) -> tuple[str, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    priv_der = priv.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    return b64(pub_der), enc(sym_key, priv_der)


class Api:
    def __init__(self, base: str, cafile: str) -> None:
        self.base = base.rstrip("/")
        self.ctx = ssl.create_default_context(cafile=cafile)

    def call(self, method: str, path: str, *, body=None, form=None, token=None, accept_json=True):
        headers = {"Accept": "application/json"} if accept_json else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300]!r}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode()


class Member:
    """One synthetic person with a Vaultwarden account."""

    def __init__(self, api: Api, email: str, name: str, password: str) -> None:
        self.api, self.email, self.name, self.password = api, email.lower(), name, password
        self.user_key = os.urandom(64)
        self.pub: str | None = None
        self.token: str | None = None
        self.user_id: str | None = None

    def _master_key(self) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", self.password.encode(), self.email.encode(), KDF_ITERATIONS, 32)

    def _hash(self, mk: bytes) -> str:
        return b64(hashlib.pbkdf2_hmac("sha256", mk, self.password.encode(), 1, 32))

    def register(self) -> None:
        mk = self._master_key()
        stretched = HKDFExpand(hashes.SHA256(), 32, b"enc").derive(mk) + HKDFExpand(hashes.SHA256(), 32,
                                                                                     b"mac").derive(mk)
        token = self.api.call("POST", "/identity/accounts/register/send-verification-email",
                              body={"email": self.email, "name": self.name, "receiveMarketingEmails": False})
        self.pub, enc_priv = new_keypair(self.user_key)
        self.api.call("POST", "/identity/accounts/register/finish", body={
            "email": self.email, "name": self.name, "emailVerificationToken": token,
            "masterPasswordHash": self._hash(mk), "masterPasswordHint": None,
            "key": enc(stretched, self.user_key), "kdf": 0, "kdfIterations": KDF_ITERATIONS,
            "keys": {"publicKey": self.pub, "encryptedPrivateKey": enc_priv}})

    def login(self) -> None:
        mk = self._master_key()
        res = self.api.call("POST", "/identity/connect/token", form={
            "grant_type": "password", "username": self.email, "password": self._hash(mk),
            "scope": "api offline_access", "client_id": "web", "deviceType": "10",
            "deviceIdentifier": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.email)), "deviceName": "lab-seed"})
        self.token = res["access_token"]
        self.user_id = self.api.call("GET", "/api/accounts/profile", token=self.token)["id"]

    # -- organisations -------------------------------------------------------------------------------------------
    def create_org(self, name: str, first_collection: str) -> tuple[str, bytes, str]:
        org_key = os.urandom(64)
        opub, opriv = new_keypair(org_key)
        res = self.api.call("POST", "/api/organizations", token=self.token, body={
            "name": name, "billingEmail": self.email, "collectionName": enc(org_key, first_collection),
            "key": rsa_enc(self.pub, org_key), "keys": {"publicKey": opub, "encryptedPrivateKey": opriv},
            "planType": 0})
        org_id = res["id"]
        cols = self.api.call("GET", f"/api/organizations/{org_id}/collections", token=self.token)["data"]
        return org_id, org_key, cols[0]["id"]

    def create_collection(self, org_id: str, org_key: bytes, name: str) -> str:
        res = self.api.call("POST", f"/api/organizations/{org_id}/collections", token=self.token,
                            body={"name": enc(org_key, name), "groups": [], "users": []})
        return res["id"]

    def invite(self, org_id: str, other: "Member", collection_ids: list[str], *, read_only=True) -> None:
        self.api.call("POST", f"/api/organizations/{org_id}/users/invite", token=self.token, body={
            "emails": [other.email], "groups": [], "type": 2, "permissions": {},
            "collections": [{"id": c, "readOnly": read_only, "hidePasswords": False, "manage": False}
                            for c in collection_ids]})

    def confirm(self, org_id: str, org_key: bytes, other: "Member") -> None:
        users = self.api.call("GET", f"/api/organizations/{org_id}/users", token=self.token)["data"]
        row = next(u for u in users if u["email"].lower() == other.email)
        pub = self.api.call("GET", f"/api/users/{row['userId']}/public-key", token=self.token)["publicKey"]
        self.api.call("POST", f"/api/organizations/{org_id}/users/{row['id']}/confirm", token=self.token,
                      body={"key": rsa_enc(pub, org_key)})

    # -- items ---------------------------------------------------------------------------------------------------
    def create_login(self, *, name: str, username: str, password: str, uris: list[tuple[str, int | None]],
                     fields: list[tuple[str, str, int]] = (), notes: str | None = None,
                     org: tuple[str, bytes] | None = None, collections: list[str] = ()) -> str:
        key = org[1] if org else self.user_key
        cipher = {
            "type": 1, "name": enc(key, name), "notes": enc(key, notes) if notes else None, "favorite": False,
            "reprompt": 0, "folderId": None, "organizationId": org[0] if org else None,
            "encryptedFor": self.user_id,   # required by Vaultwarden since web-v2025.6.0
            "login": {"username": enc(key, username), "password": enc(key, password), "totp": None,
                      "uris": [{"uri": enc(key, u), "match": m} for u, m in uris]},
            "fields": [{"name": enc(key, n), "value": enc(key, v), "type": t} for n, v, t in fields]}
        if org:
            res = self.api.call("POST", "/api/ciphers/create", token=self.token,
                                body={"cipher": cipher, "collectionIds": list(collections)})
        else:
            res = self.api.call("POST", "/api/ciphers", token=self.token, body=cipher)
        return res["id"]
