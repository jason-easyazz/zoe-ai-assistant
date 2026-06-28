"""Bootstrap the OIDC provider on startup."""
import logging
import os
from oidc.clients import upsert_client
from oidc.keys import ensure_signing_key

logger = logging.getLogger(__name__)


def bootstrap_oidc() -> None:
    """Generate RSA key if needed and seed built-in OIDC clients. Safe to re-run."""
    key = ensure_signing_key()
    logger.info(f"OIDC signing key active: kid={key['kid']}")

    base_url = os.getenv("ZOE_BASE_URL", "http://zoe.local").rstrip("/")

    ha_secret = os.getenv("HA_OIDC_CLIENT_SECRET", "")
    ha_client_id = os.getenv("HA_OIDC_CLIENT_ID", "home-assistant")
    if ha_secret:
        ha_redirect_uris = [
            "http://homeassistant.local:8123/auth/oidc/callback",
            "http://homeassistant:8123/auth/oidc/callback",
            f"{base_url}:8123/auth/oidc/callback",
        ]
        # Support per-client redirect URI override
        ha_redirect_uri_override = os.getenv("HA_OIDC_REDIRECT_URI", "")
        if ha_redirect_uri_override:
            ha_redirect_uris.append(ha_redirect_uri_override)
        
        upsert_client(
            client_id=ha_client_id,
            client_name="Home Assistant",
            secret=ha_secret,
            redirect_uris=ha_redirect_uris,
            public_issuer=os.getenv("HA_OIDC_ISSUER"),
        )
        logger.info(f"OIDC client seeded: {ha_client_id}")
    else:
        logger.warning("HA_OIDC_CLIENT_SECRET not set — Home Assistant OIDC client not seeded")

    multica_secret = os.getenv("MULTICA_OIDC_CLIENT_SECRET", "")
    multica_client_id = os.getenv("MULTICA_OIDC_CLIENT_ID", "multica")
    if multica_secret:
        multica_redirect_uris = [
            f"{base_url}/multica/auth/callback",
            "http://multica:3000/auth/callback",
        ]
        # Support per-client redirect URI override
        multica_redirect_uri_override = os.getenv("MULTICA_OIDC_REDIRECT_URI", "")
        if multica_redirect_uri_override:
            multica_redirect_uris.append(multica_redirect_uri_override)
        
        upsert_client(
            client_id=multica_client_id,
            client_name="Multica",
            secret=multica_secret,
            redirect_uris=multica_redirect_uris,
            public_issuer=os.getenv("MULTICA_OIDC_ISSUER"),
        )
        logger.info(f"OIDC client seeded: {multica_client_id}")
    else:
        logger.warning("MULTICA_OIDC_CLIENT_SECRET not set — Multica OIDC client not seeded")

    omnigent_secret = os.getenv("OMNIGENT_OIDC_CLIENT_SECRET", "")
    omnigent_client_id = os.getenv("OMNIGENT_OIDC_CLIENT_ID", "omnigent")
    if omnigent_secret:
        omnigent_redirect_uris = [
            f"{base_url}:6767/auth/callback",
            "http://zoe.local:6767/auth/callback",
        ]
        # Support per-client redirect URI override
        omnigent_redirect_uri_override = os.getenv("OMNIGENT_OIDC_REDIRECT_URI", "")
        if omnigent_redirect_uri_override:
            omnigent_redirect_uris.append(omnigent_redirect_uri_override)
        
        upsert_client(
            client_id=omnigent_client_id,
            client_name="Omnigent",
            secret=omnigent_secret,
            redirect_uris=omnigent_redirect_uris,
            public_issuer=os.getenv("OMNIGENT_OIDC_ISSUER"),
        )
        logger.info(f"OIDC client seeded: {omnigent_client_id}")
    else:
        logger.warning("OMNIGENT_OIDC_CLIENT_SECRET not set — Omnigent OIDC client not seeded")
