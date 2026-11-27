"""Who may use the MCP server: read from the Keycloak token, like the API."""

import asyncio
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed

from tosca_api.apps.mcp_server.principal import (
    KeycloakTokenVerifier,
    principal_from_claims,
    verify_token_claims,
)

READER = {
    "sub": "sub-reader", "preferred_username": "rita", "azp": "tosca-mcp", "exp": 4102444800,
    "scope": "openid profile", "realm_access": {"roles": ["ROLE_DCS_READER"]}, "default_organization": "dcs",
}
WRITER = {**READER, "sub": "sub-writer", "realm_access": {"roles": ["ROLE_DCS_READER", "ROLE_DCS_WRITER"]}}
NO_ROLE = {**READER, "sub": "sub-none", "realm_access": {"roles": ["offline_access"]}}
OTHER_ORG_ROLE = {**READER, "sub": "sub-other", "realm_access": {"roles": ["ROLE_GQ2_ADMIN"]}}
PLATFORM_ADMIN = {"sub": "sub-admin", "preferred_username": "root", "exp": 4102444800,
                  "realm_access": {"roles": ["DJANGO_SUPERADMIN"]}}


class PrincipalFromClaimsTests(SimpleTestCase):
    def test_reader_of_the_own_organization_may_use_mcp(self):
        principal = principal_from_claims(READER)
        self.assertEqual((principal.sub, principal.org_slug, principal.level), ("sub-reader", "dcs", "READER"))
        self.assertTrue(principal.may_use_mcp)
        self.assertEqual(principal.viewer.org_slug, "dcs")
        self.assertFalse(principal.viewer.exempt)

    def test_highest_role_counts(self):
        self.assertEqual(principal_from_claims(WRITER).level, "WRITER")

    def test_without_any_organization_role_is_refused(self):
        principal = principal_from_claims(NO_ROLE)
        self.assertIsNone(principal.level)
        self.assertFalse(principal.may_use_mcp)

    def test_role_in_another_organization_passes_but_sees_only_the_claimed_one(self):
        principal = principal_from_claims(OTHER_ORG_ROLE)
        self.assertTrue(principal.may_use_mcp)
        self.assertIsNone(principal.level)
        self.assertEqual(principal.viewer.org_slug, "dcs")

    def test_reader_role_without_organization_claim_takes_org_from_role(self):
        # The ngcn-prod "web" client sends roles but no organization claim.
        claims = {**READER, "realm_access": {"roles": ["ROLE_NGCN_READER"]}}
        del claims["default_organization"]
        principal = principal_from_claims(claims)
        self.assertTrue(principal.may_use_mcp)
        self.assertEqual(principal.org_roles, (("ngcn", "READER"),))
        self.assertEqual(principal.viewer.org_slug, "ngcn")
        self.assertEqual(principal.level, "READER")

    def test_roles_in_several_orgs_without_claim_see_public_only(self):
        claims = {**READER, "realm_access": {"roles": ["ROLE_NGCN_READER", "ROLE_DCS_WRITER"]}}
        del claims["default_organization"]
        principal = principal_from_claims(claims)
        self.assertTrue(principal.may_use_mcp)
        self.assertIsNone(principal.viewer.org_slug)

    def test_platform_admin_passes_without_an_organization(self):
        principal = principal_from_claims(PLATFORM_ADMIN)
        self.assertTrue(principal.may_use_mcp)
        self.assertTrue(principal.viewer.exempt)

    @override_settings(MCP_MIN_ROLE_LEVEL="WRITER")
    def test_minimum_role_is_configurable(self):
        self.assertFalse(principal_from_claims(READER).may_use_mcp)
        self.assertTrue(principal_from_claims(WRITER).may_use_mcp)


class TokenVerificationTests(TestCase):
    @override_settings(MCP_TOKEN_AUDIENCES=["https://tosca.example/mcp"])
    def test_uses_the_shared_keycloak_verification_with_the_mcp_audience(self):
        with patch(
            "tosca_api.apps.mcp_server.principal.verify_and_decode_token", return_value=READER
        ) as verify:
            self.assertEqual(verify_token_claims("token"), READER)
        verify.assert_called_once_with("token", audiences=["https://tosca.example/mcp"])

    def test_refused_token_gives_none(self):
        with patch(
            "tosca_api.apps.mcp_server.principal.verify_and_decode_token",
            side_effect=AuthenticationFailed("Invalid or expired token."),
        ):
            self.assertIsNone(verify_token_claims("token"))

    def test_token_of_a_deactivated_user_is_refused(self):
        user = get_user_model().objects.create(username="rita", is_active=False)
        SocialAccount.objects.create(user=user, provider="keycloak", uid="sub-reader")
        with patch("tosca_api.apps.mcp_server.principal.verify_and_decode_token", return_value=READER):
            self.assertIsNone(verify_token_claims("token"))

    def test_verification_never_creates_a_user(self):
        with patch("tosca_api.apps.mcp_server.principal.verify_and_decode_token", return_value=READER):
            verify_token_claims("token")
        self.assertFalse(get_user_model().objects.filter(username="rita").exists())
        self.assertFalse(SocialAccount.objects.filter(uid="sub-reader").exists())

    def test_verifier_maps_claims_to_the_sdk_token(self):
        async def claims(_token):
            return READER

        with patch("tosca_api.apps.mcp_server.principal.verify_token_claims_async", claims):
            token = asyncio.run(KeycloakTokenVerifier().verify_token("token"))
        self.assertEqual(
            (token.subject, token.client_id, token.scopes, token.expires_at),
            ("sub-reader", "tosca-mcp", ["openid", "profile"], 4102444800),
        )
        self.assertEqual(token.claims, READER)
