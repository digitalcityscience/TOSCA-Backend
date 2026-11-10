import pytest
import jwt
import datetime
from unittest.mock import patch
from rest_framework.exceptions import AuthenticationFailed
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from tosca_api.apps.core.jwt_utils import verify_and_decode_token


# ---- RSA key generation (test-only, real crypto) ----
def generate_rsa_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    return private_pem, public_pem


TEST_PRIVATE_KEY, TEST_PUBLIC_KEY = generate_rsa_keys()
TEST_KID = "test-key-id"


# ---- JWT factory ----
def make_token(payload_overrides=None, headers=None):
    now = datetime.datetime.now(datetime.UTC)

    payload = {
        "sub": "user1",
        "preferred_username": "user1",
        "iss": "https://issuer.example.com/",
        "aud": "test-aud",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=5),
    }

    if payload_overrides:
        payload.update(payload_overrides)

    headers = headers or {"kid": TEST_KID}

    return jwt.encode(
        payload,
        TEST_PRIVATE_KEY,
        algorithm="RS256",
        headers=headers,
    )


# ---- PyJWKClient mock ----
class FakeJWK:
    def __init__(self, key):
        self.key = key


class FakeJWKClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_signing_key_from_jwt(self, token):
        return FakeJWK(TEST_PUBLIC_KEY)


# ---- Settings override ----
@pytest.fixture(autouse=True)
def override_settings(settings):
    settings.KEYCLOAK_JWKS_URL = "https://fake-jwks/"
    settings.KEYCLOAK_ISSUER = "https://issuer.example.com/"
    settings.ALLOWED_TOKEN_AUDIENCES = ["test-aud", "other-aud"]
    yield


# ---- Tests ----
@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_valid_token():
    token = make_token()
    payload = verify_and_decode_token(token)
    assert payload["sub"] == "user1"
    assert payload["preferred_username"] == "user1"


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_expired_token():
    expired = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    token = make_token({"exp": expired})
    with pytest.raises(AuthenticationFailed):
        verify_and_decode_token(token)


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_invalid_audience():
    token = make_token({"aud": "not-allowed"})
    with pytest.raises(AuthenticationFailed):
        verify_and_decode_token(token)


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_invalid_issuer():
    token = make_token({"iss": "https://wrong-issuer/"})
    with pytest.raises(AuthenticationFailed):
        verify_and_decode_token(token)


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_azp_fallback():
    token = make_token({"aud": None, "azp": "test-aud"})
    payload = verify_and_decode_token(token)
    assert payload["sub"] == "user1"
    assert payload["azp"] == "test-aud"

@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_small_clock_difference_is_tolerated(settings):
    # Keycloak's clock is a few seconds ahead: iat lies in the future.
    settings.JWT_LEEWAY_SECONDS = 30
    ahead = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=10)
    payload = verify_and_decode_token(make_token({"iat": ahead}))
    assert payload["sub"] == "user1"


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_large_clock_difference_is_refused(settings):
    settings.JWT_LEEWAY_SECONDS = 30
    ahead = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)
    with pytest.raises(AuthenticationFailed):
        verify_and_decode_token(make_token({"iat": ahead, "exp": ahead + datetime.timedelta(minutes=5)}))


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_token_without_sub_is_refused():
    token = jwt.encode(
        {"iss": "https://issuer.example.com/", "aud": "test-aud",
         "iat": datetime.datetime.now(datetime.UTC),
         "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)},
        TEST_PRIVATE_KEY, algorithm="RS256", headers={"kid": TEST_KID},
    )
    with pytest.raises(AuthenticationFailed):
        verify_and_decode_token(token)


@patch("tosca_api.apps.core.jwt_utils.PyJWKClient", FakeJWKClient)
def test_error_message_does_not_reveal_the_reason():
    token = make_token({"iss": "https://wrong-issuer/"})
    with pytest.raises(AuthenticationFailed) as raised:
        verify_and_decode_token(token)
    assert "issuer" not in str(raised.value.detail).lower()


def test_signing_keys_client_is_reused(settings):
    from tosca_api.apps.core import jwt_utils

    created = []

    class CountingClient(FakeJWKClient):
        def __init__(self, *args, **kwargs):
            created.append(args)

    settings.KEYCLOAK_JWKS_URL = "https://fake-jwks/reuse"
    with patch("tosca_api.apps.core.jwt_utils.PyJWKClient", CountingClient):
        verify_and_decode_token(make_token())
        verify_and_decode_token(make_token())
    assert len(created) == 1
    jwt_utils._jwks_clients.clear()
