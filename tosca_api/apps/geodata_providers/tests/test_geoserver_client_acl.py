"""Tests for GeoServerClient's Data Security ACL layer-rule methods
(epic-11 ticket 07). `_request`/`requests` are mocked -- no real GeoServer.
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient


def make_client():
    with patch(
        "tosca_api.apps.geodata_providers.geoserver.client.GeoServerRestClient"
    ) as mock_rest_client_cls:
        client = GeoServerClient("http://geoserver.example.com/geoserver", "admin", "secret")
        client._client = mock_rest_client_cls.return_value
    return client


def _response(status_code, text="", json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    return response


class AddLayerRuleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_posts_new_rule_with_expected_verb_path_and_body(self):
        with patch.object(self.client, "_request", return_value=_response(201)) as mock_request:
            result = self.client.add_layer_rule("hamburg.bezirke.r", "ROLE_DCS_READER")

        mock_request.assert_called_once_with(
            "post", "/rest/security/acl/layers", json={"hamburg.bezirke.r": "ROLE_DCS_READER"}
        )
        self.assertTrue(result.success)

    def test_non_2xx_status_is_a_failure(self):
        with patch.object(self.client, "_request", return_value=_response(403, text="already exists")):
            result = self.client.add_layer_rule("hamburg.bezirke.r", "ROLE_DCS_READER")

        self.assertFalse(result.success)
        self.assertIn("already exists", result.error)


class UpdateLayerRuleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_puts_existing_rule_with_expected_verb_path_and_body(self):
        with patch.object(self.client, "_request", return_value=_response(200)) as mock_request:
            result = self.client.update_layer_rule("hamburg.bezirke.r", "ROLE_A,ROLE_B")

        mock_request.assert_called_once_with(
            "put", "/rest/security/acl/layers", json={"hamburg.bezirke.r": "ROLE_A,ROLE_B"}
        )
        self.assertTrue(result.success)


class DeleteLayerRuleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_deletes_with_key_in_path(self):
        with patch.object(self.client, "_request", return_value=_response(200)) as mock_request:
            result = self.client.delete_layer_rule("hamburg.bezirke.r")

        mock_request.assert_called_once_with("delete", "/rest/security/acl/layers/hamburg.bezirke.r")
        self.assertTrue(result.success)

    def test_missing_key_404_is_treated_as_already_deleted(self):
        with patch.object(self.client, "_request", return_value=_response(404)):
            result = self.client.delete_layer_rule("hamburg.bezirke.r")

        self.assertTrue(result.success)
        self.assertTrue(result.data.get("already_deleted"))

    def test_key_is_url_encoded_for_special_characters(self):
        with patch.object(self.client, "_request", return_value=_response(200)) as mock_request:
            self.client.delete_layer_rule("hamburg.wor kspace.r")

        mock_request.assert_called_once_with(
            "delete", "/rest/security/acl/layers/hamburg.wor%20kspace.r"
        )


class SetLayerRuleTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_new_key_posts(self):
        with patch.object(self.client, "get_layer_rules", return_value={}), \
             patch.object(self.client, "add_layer_rule") as mock_add, \
             patch.object(self.client, "update_layer_rule") as mock_update:
            self.client.set_layer_rule("hamburg.bezirke.r", "ROLE_DCS_READER")

        mock_add.assert_called_once_with("hamburg.bezirke.r", "ROLE_DCS_READER")
        mock_update.assert_not_called()

    def test_existing_key_puts(self):
        with patch.object(
            self.client, "get_layer_rules", return_value={"hamburg.bezirke.r": "ROLE_OLD"}
        ), patch.object(self.client, "add_layer_rule") as mock_add, \
           patch.object(self.client, "update_layer_rule") as mock_update:
            self.client.set_layer_rule("hamburg.bezirke.r", "ROLE_DCS_READER")

        mock_update.assert_called_once_with("hamburg.bezirke.r", "ROLE_DCS_READER")
        mock_add.assert_not_called()

    def test_repeated_calls_are_idempotent(self):
        """Calling set_layer_rule twice never raises even though the second
        call's GeoServer state now has the key (canonical §8 idempotent push)."""
        state = {}

        def fake_get_layer_rules():
            return dict(state)

        def fake_add(key, roles):
            state[key] = roles
            return MagicMock(success=True)

        def fake_update(key, roles):
            state[key] = roles
            return MagicMock(success=True)

        with patch.object(self.client, "get_layer_rules", side_effect=fake_get_layer_rules), \
             patch.object(self.client, "add_layer_rule", side_effect=fake_add), \
             patch.object(self.client, "update_layer_rule", side_effect=fake_update):
            first = self.client.set_layer_rule("hamburg.bezirke.r", "ROLE_DCS_READER")
            second = self.client.set_layer_rule("hamburg.bezirke.r", "ROLE_DCS_READER")

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(state["hamburg.bezirke.r"], "ROLE_DCS_READER")


class GetLayerRulesTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_returns_json_body_on_200(self):
        with patch.object(
            self.client, "_request", return_value=_response(200, json_data={"a.b.r": "ROLE_A"})
        ):
            rules = self.client.get_layer_rules()

        self.assertEqual(rules, {"a.b.r": "ROLE_A"})

    def test_raises_on_non_200(self):
        from tosca_api.apps.geodata_providers.exceptions import GeoServerPublishError

        with patch.object(self.client, "_request", return_value=_response(500)):
            with self.assertRaises(GeoServerPublishError):
                self.client.get_layer_rules()
