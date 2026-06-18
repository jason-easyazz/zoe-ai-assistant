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
        upsert_client(
            client_id=ha_client_id,
            client_name="Home Assistant",
            secret=ha_secret,
            redirect_uris=[
                "http://homeassistant.local:8123/auth/oidc/callback",
                "http://homeassistant:8123/auth/oidc/callback",
                f"{base_url}:8123/auth/oidc/callback",
            ],
        )
        logger.info(f"OIDC client seeded: {ha_client_id}")
    else:
        logger.warning("HA_OIDC_CLIENT_SECRET not set — Home Assistant OIDC client not seeded")

    multica_secret = os.getenv("MULTICA_OIDC_CLIENT_SECRET", "")
    multica_client_id = os.getenv("MULTICA_OIDC_CLIENT_ID", "multica")
    if multica_secret:
        upsert_client(
            client_id=multica_client_id,
            client_name="Multica",
            secret=multica_secret,
            redirect_uris=[
                f"{base_url}/multica/auth/callback",
                "http://multica:3000/auth/callback",
            ],
        )
        logger.info(f"OIDC client seeded: {multica_client_id}")
    else:
        logger.warning("MULTICA_OIDC_CLIENT_SECRET not set — Multica OIDC client not seeded")

    omnigent_secret = os.getenv("OMNIGENT_OIDC_CLIENT_SECRET", "")
    omnigent_client_id = os.getenv("OMNIGENT_OIDC_CLIENT_ID", "omnigent")
    if omnigent_secret:
        upsert_client(
            client_id=omnigent_client_id,
            client_name="Omnigent",
            secret=omnigent_secret,
            redirect_uris=[
                f"{base_url}:6767/auth/callback",
                "http://zoe.local:6767/auth/callback",
            ],
        )
        logger.info(f"OIDC client seeded: {omnigent_client_id}")
    else:
        logger.warning("OMNIGENT_OIDC_CLIENT_SECRET not set — Omnigent OIDC client not seeded")
