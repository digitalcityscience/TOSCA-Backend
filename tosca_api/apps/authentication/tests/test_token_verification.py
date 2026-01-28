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