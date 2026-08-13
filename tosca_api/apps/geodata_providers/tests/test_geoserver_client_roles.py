"""Tests for GeoServerClient's role-service methods (epic-11 Phase 2).
`_request`/`requests` are mocked -- no real GeoServer.
"""
from unittest import TestCase
from unittest.mock import patch

from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient


def make_client():
    with patch(
        "tosca_api.apps.geodata_providers.geoserver.client.GeoServerRestClient"
    ) as mock_rest_client_cls:
        client = GeoServerClient("http://geoserver.example.com/geoserver", "admin", "secret")
        client._client = mock_rest_client_cls.return_value
    return client


def _response(status_code, text="", json_data=None):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("no json")
    return response


class GetRolesTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_unwraps_roles_key(self):
        with patch.object(
            self.client, "_request", return_value=_response(200, json_data={"roles": ["ADMIN", "ROLE_DCS_READER"]})
        ):
            self.assertEqual(self.client.get_roles(), ["ADMIN", "ROLE_DCS_READER"])

    def test_requests_json_explicitly(self):
        # /rest/security/roles defaults to XML; without this header .json()
        # would raise and get_roles() would silently return [].
        with patch.object(
            self.client, "_request", return_value=_response(200, json_data={"roles": []})
        ) as mock_request:
            self.client.get_roles()

        mock_request.assert_called_once_with(
            "get", "/rest/security/roles", headers={"Accept": "application/json"}
        )

    def test_unwraps_rolenames_key(self):
        with patch.object(
            self.client, "_request", return_value=_response(200, json_data={"roleNames": ["ROLE_A"]})
        ):
            self.assertEqual(self.client.get_roles(), ["ROLE_A"])

    def test_accepts_bare_list(self):
        with patch.object(
            self.client, "_request", return_value=_response(200, json_data=["ROLE_A", "ROLE_B"])
        ):
            self.assertEqual(self.client.get_roles(), ["ROLE_A", "ROLE_B"])

    def test_raises_on_non_200(self):
        from tosca_api.apps.geodata_providers.exceptions import GeoServerPublishError

        with patch.object(self.client, "_request", return_value=_response(500)):
            with self.assertRaises(GeoServerPublishError):
                self.client.get_roles()


class CreateRoleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_posts_to_role_path(self):
        with patch.object(self.client, "_request", return_value=_response(201)) as mock_request:
            result = self.client.create_role("ROLE_DCS_READER")

        mock_request.assert_called_once_with(
            "post", "/rest/security/roles/role/ROLE_DCS_READER"
        )
        self.assertTrue(result.success)

    def test_url_encodes_name(self):
        with patch.object(self.client, "_request", return_value=_response(201)) as mock_request:
            self.client.create_role("ROLE_A B")

        mock_request.assert_called_once_with(
            "post", "/rest/security/roles/role/ROLE_A%20B"
        )

    def test_non_2xx_is_failure(self):
        with patch.object(self.client, "_request", return_value=_response(500, text="boom")):
            result = self.client.create_role("ROLE_DCS_READER")

        self.assertFalse(result.success)
        self.assertIn("boom", result.error)


class SetRoleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_existing_role_is_noop(self):
        with patch.object(self.client, "get_roles", return_value=["ROLE_DCS_READER"]), \
             patch.object(self.client, "create_role") as mock_create:
            result = self.client.set_role("ROLE_DCS_READER")

        mock_create.assert_not_called()
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("already_exists"))

    def test_missing_role_is_created(self):
        with patch.object(self.client, "get_roles", return_value=[]), \
             patch.object(self.client, "create_role", return_value=None) as mock_create:
            self.client.set_role("ROLE_DCS_READER")

        mock_create.assert_called_once_with("ROLE_DCS_READER")


class DeleteRoleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_deletes_with_encoded_name(self):
        with patch.object(self.client, "_request", return_value=_response(200)) as mock_request:
            result = self.client.delete_role("ROLE_DCS_READER")

        mock_request.assert_called_once_with(
            "delete", "/rest/security/roles/role/ROLE_DCS_READER"
        )
        self.assertTrue(result.success)

    def test_404_is_already_deleted(self):
        with patch.object(self.client, "_request", return_value=_response(404)):
            result = self.client.delete_role("ROLE_DCS_READER")

        self.assertTrue(result.success)
        self.assertTrue(result.data.get("already_deleted"))
