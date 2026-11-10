import logging
import threading

import jwt
from django.conf import settings
from jwt import ExpiredSignatureError, ImmatureSignatureError, InvalidAudienceError, InvalidIssuerError, PyJWKClient
from jwt.exceptions import MissingRequiredClaimError, PyJWKClientError
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger(__name__)

# Claims every Keycloak access token carries; a token without them is refused.
REQUIRED_CLAIMS = ["exp", "iat", "iss", "sub"]

# One key client per JWKS URL and process. PyJWKClient keeps the realm's
# signing keys in memory (and refetches on an unknown ``kid``, i.e. after key
# rotation), so requests no longer download the keys every time. Keyed by the
# class too, so tests that patch ``PyJWKClient`` get their fake.
_jwks_clients: dict[tuple[type, str], PyJWKClient] = {}
_jwks_lock = threading.Lock()


def _jwks_client() -> PyJWKClient:
    url = settings.KEYCLOAK_JWKS_URL
    key = (PyJWKClient, url)
    client = _jwks_clients.get(key)
    if client is None:
        with _jwks_lock:
            client = _jwks_clients.get(key)
            if client is None:
                client = PyJWKClient(
                    url,
                    cache_keys=True,
                    lifespan=int(getattr(settings, "KEYCLOAK_JWKS_CACHE_SECONDS", 3600)),
                )
                _jwks_clients[key] = client
    return client


def _leeway() -> int:
    """Seconds of clock difference tolerated between Keycloak and this server
    (``exp``/``iat``/``nbf``). Docker VMs and hosts drift a few seconds; without
    it a freshly issued token is "not yet valid"."""
    return int(getattr(settings, "JWT_LEEWAY_SECONDS", 30))


def _refuse(reason: str, exc: Exception | None = None) -> AuthenticationFailed:
    """Log the real reason; answer the client with a generic one."""
    logger.warning("JWT verification failed: %s%s", reason, f" ({exc})" if exc else "")
    return AuthenticationFailed("Invalid or expired token.")


def verify_and_decode_token(token: str):
    """Verify a Keycloak access token (signature, issuer, audience, expiry) and
    return its claims. Raises ``AuthenticationFailed`` with a generic message;
    the reason is logged."""
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
    except (PyJWKClientError, jwt.DecodeError) as exc:
        raise _refuse("no matching signing key (wrong realm or malformed token)", exc) from exc
    except Exception as exc:  # JWKS endpoint unreachable
        logger.error("Fetching Keycloak signing keys failed: %s", exc)
        raise AuthenticationFailed("Invalid or expired token.") from exc

    common = {
        "algorithms": ["RS256"],
        "issuer": settings.KEYCLOAK_ISSUER,
        "leeway": _leeway(),
    }
    try:
        try:
            return jwt.decode(
                token,
                signing_key.key,
                audience=settings.ALLOWED_TOKEN_AUDIENCES,
                options={"require": REQUIRED_CLAIMS},
                **common,
            )
        except (InvalidAudienceError, MissingRequiredClaimError) as exc:
            if isinstance(exc, MissingRequiredClaimError) and exc.claim != "aud":
                raise
            # aud invalid or missing -> accept when the token was issued to an
            # allowed client (azp). PyJWT cannot check aud then, so skip it.
            unverified = jwt.decode(token, options={"verify_signature": False})
            azp = unverified.get("azp")
            if not azp or azp not in settings.ALLOWED_TOKEN_AUDIENCES:
                raise _refuse(
                    f"audience/azp not allowed (aud={unverified.get('aud')!r}, azp={azp!r})"
                ) from exc
            return jwt.decode(
                token,
                signing_key.key,
                options={"require": REQUIRED_CLAIMS, "verify_aud": False},
                **common,
            )
    except AuthenticationFailed:
        raise
    except ExpiredSignatureError as exc:
        raise _refuse("token expired", exc) from exc
    except ImmatureSignatureError as exc:
        raise _refuse("token not yet valid (clock difference larger than the leeway)", exc) from exc
    except InvalidIssuerError as exc:
        raise _refuse("issuer not allowed", exc) from exc
    except MissingRequiredClaimError as exc:
        raise _refuse(f"missing claim {exc.claim}", exc) from exc
    except jwt.PyJWTError as exc:
        raise _refuse("invalid token", exc) from exc
