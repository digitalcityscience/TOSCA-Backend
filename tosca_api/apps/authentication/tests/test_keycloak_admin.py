"""Tests for the Keycloak Admin API client (Epic 11 Phase 1, §5)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from tosca_api.apps.authentication.keycloak_admin import (
    KeycloakAdminError,
    get_admin_token,
    list_realm_roles,
)


def _resp(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


@patch("tosca_api.apps.authentication.keycloak_admin.requests.post")
def test_get_admin_token_returns_access_token(mock_post):
    mock_post.return_value = _resp({"access_token": "abc123"})
    assert get_admin_token() == "abc123"
    # client_credentials grant with the existing client id/secret.
    assert mock_post.call_args.kwargs["data"]["grant_type"] == "client_credentials"


@patch("tosca_api.apps.authentication.keycloak_admin.requests.post")
def test_get_admin_token_missing_token_raises(mock_post):
    mock_post.return_value = _resp({"not_a_token": "x"})
    with pytest.raises(KeycloakAdminError):
        get_admin_token()


@patch("tosca_api.apps.authentication.keycloak_admin.requests.post")
def test_get_admin_token_wraps_http_errors(mock_post):
    mock_post.side_effect = requests.ConnectionError("down")
    with pytest.raises(KeycloakAdminError):
        get_admin_token()


@patch("tosca_api.apps.authentication.keycloak_admin.requests.get")
def test_list_realm_roles_returns_names(mock_get):
    mock_get.return_value = _resp(
        [{"name": "ROLE_DCS_READER"}, {"name": "offline_access"}, {"id": "x"}]
    )
    # Passing an explicit token avoids a token round-trip.
    assert list_realm_roles(token="tok") == ["ROLE_DCS_READER", "offline_access"]
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


@patch("tosca_api.apps.authentication.keycloak_admin.requests.get")
def test_list_realm_roles_wraps_http_errors(mock_get):
    mock_get.side_effect = requests.Timeout("slow")
    with pytest.raises(KeycloakAdminError):
        list_realm_roles(token="tok")
