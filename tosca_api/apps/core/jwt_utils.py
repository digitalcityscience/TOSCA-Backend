import jwt
from jwt import PyJWKClient
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from jwt import InvalidAudienceError, ExpiredSignatureError, InvalidIssuerError
from jwt.exceptions import MissingRequiredClaimError
import logging

logger = logging.getLogger(__name__)

def verify_and_decode_token(token: str):
    try:
        jwks_client = PyJWKClient(settings.KEYCLOAK_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        try:
            decoded = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.ALLOWED_TOKEN_AUDIENCES,
                issuer=settings.KEYCLOAK_ISSUER,
            )

        except (InvalidAudienceError, MissingRequiredClaimError):
            # aud invalid OR missing → try azp fallback
            unverified = jwt.decode(token, options={"verify_signature": False})
            azp = unverified.get("azp")

            if not azp or azp not in settings.ALLOWED_TOKEN_AUDIENCES:
                raise AuthenticationFailed("Token audience (aud/azp) is invalid")

            # IMPORTANT:
            # aud yokken PyJWT audience doğrulaması YAPAMAZ
            decoded = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=settings.KEYCLOAK_ISSUER,
                options={"verify_aud": False},
            )
        except ExpiredSignatureError:
                raise AuthenticationFailed("Token has expired")

        except InvalidIssuerError:
            raise AuthenticationFailed("Token issuer (iss) is invalid")

        except Exception as exc:
            logger.warning("JWT verification failed: %s", exc)
            raise AuthenticationFailed("Invalid token")

        return decoded

    except Exception as exc:
        logger.error("JWT verification error: %s", exc)
        raise AuthenticationFailed("Invalid token")