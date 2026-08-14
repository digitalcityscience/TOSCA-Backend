"""
Tests for GeoServerClient's shared create/delete resource helpers, plus the
four methods that use them (create_workspace, delete_workspace,
create_store, delete_store).

Before this refactor, each of these four methods repeated the same
five-step pre-check -> operate -> validate -> post-verify -> respond cycle
independently, with no direct unit test coverage (existing tests mock
GeoServerClient entirely at the service-layer boundary). These tests cover
the shared control flow once via the helpers, then confirm each of the four
methods still produces the exact same response shape as before.
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient


def make_client():
    """A GeoServerClient with the network-touching pieces mocked out."""
    with patch(
        "tosca_api.apps.geodata_providers.geoserver.client.GeoServerRestClient"
    ) as mock_rest_client_cls:
        client = GeoServerClient("http://geoserver.example.com/geoserver", "admin", "secret")
        client._client = mock_rest_client_cls.return_value
    return client


class FeatureTypeDetailTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_extracts_geometry_column_type_and_srid(self):
        self.client._client.get_featuretype.return_value = {
            "name": "districts",
            "nativeName": "districts_native",
            "title": "Districts",
            "srs": "EPSG:25832",
            "advertised": "true",
            "attributes": {
                "attribute": [
                    {
                        "name": "shape",
                        "binding": "org.locationtech.jts.geom.MultiPolygon",
                    },
                    {"name": "name", "binding": "java.lang.String"},
                ]
            },
        }

        detail = self.client.get_featuretype_detail("mobility", "gis", "districts")

        self.assertEqual(detail["geometry_type"], "MultiPolygon")
        self.assertEqual(detail["geometry_column"], "shape")
        self.assertEqual(detail["srid"], 25832)
        self.assertTrue(detail["advertised"])


class CreateResourceHelperTests(TestCase):
    """Direct tests of _create_resource — the five-step cycle, tested once."""

    def setUp(self):
        self.client = make_client()

    def test_short_circuits_when_resource_already_exists(self):
        perform_create = MagicMock()

        result = self.client._create_resource(
            exists_check=lambda: True,
            already_exists_result=lambda: {"success": True, "pre_existed": True},
            perform_create=perform_create,
            validate_operation_label="op",
            validation_failure_prefix="failed",
            perform_verify=MagicMock(),
            verify_failure_message="not verified",
            success_result=MagicMock(),
            error_result=MagicMock(),
            error_log_message=lambda e: str(e),
        )

        self.assertEqual(result, {"success": True, "pre_existed": True})
        perform_create.assert_not_called()

    def test_full_success_cycle_calls_every_step_in_order(self):
        calls = []

        def _create():
            calls.append("create")
            return {"success": True}

        def _verify():
            calls.append("verify")
            return {"verified": True}

        with patch.object(
            self.client, "validate_response", return_value={"success": True, "validated": True}
        ) as mock_validate:
            result = self.client._create_resource(
                exists_check=lambda: calls.append("exists_check") or False,
                already_exists_result=MagicMock(),
                perform_create=_create,
                validate_operation_label="op",
                validation_failure_prefix="failed",
                perform_verify=_verify,
                verify_failure_message="not verified",
                success_result=lambda validated, verification: {
                    "success": True,
                    "validated": validated["validated"],
                    "verified": verification["verified"],
                },
                error_result=MagicMock(),
                error_log_message=lambda e: str(e),
            )

        self.assertEqual(calls, ["exists_check", "create", "verify"])
        mock_validate.assert_called_once_with({"success": True}, "op")
        self.assertEqual(result, {"success": True, "validated": True, "verified": True})

    def test_validation_failure_routes_to_error_result(self):
        with patch.object(
            self.client, "validate_response", return_value={"success": False, "message": "bad"}
        ):
            result = self.client._create_resource(
                exists_check=lambda: False,
                already_exists_result=MagicMock(),
                perform_create=lambda: {"success": False},
                validate_operation_label="op",
                validation_failure_prefix="Creation failed",
                perform_verify=MagicMock(),
                verify_failure_message="not verified",
                success_result=MagicMock(),
                error_result=lambda e: {"success": False, "error": str(e)},
                error_log_message=lambda e: str(e),
            )

        self.assertFalse(result["success"])
        self.assertIn("Creation failed", result["error"])

    def test_verify_failure_routes_to_error_result(self):
        with patch.object(
            self.client, "validate_response", return_value={"success": True, "validated": True}
        ):
            result = self.client._create_resource(
                exists_check=lambda: False,
                already_exists_result=MagicMock(),
                perform_create=lambda: {"success": True},
                validate_operation_label="op",
                validation_failure_prefix="failed",
                perform_verify=lambda: {"verified": False, "message": "still missing"},
                verify_failure_message="fallback message",
                success_result=MagicMock(),
                error_result=lambda e: {"success": False, "error": str(e)},
                error_log_message=lambda e: str(e),
            )

        self.assertFalse(result["success"])
        self.assertIn("still missing", result["error"])

    def test_exception_in_exists_check_is_caught_by_the_same_handler(self):
        """A pre-check exception must be caught the same way an operation
        or verification exception is — regression guard for the refactor
        moving exists_check inside the try block, not evaluated eagerly
        before it.
        """

        def _boom():
            raise ValueError("pre-check exploded")

        result = self.client._create_resource(
            exists_check=_boom,
            already_exists_result=MagicMock(),
            perform_create=MagicMock(),
            validate_operation_label="op",
            validation_failure_prefix="failed",
            perform_verify=MagicMock(),
            verify_failure_message="not verified",
            success_result=MagicMock(),
            error_result=lambda e: {"success": False, "error": str(e)},
            error_log_message=lambda e: str(e),
        )

        self.assertFalse(result["success"])
        self.assertIn("pre-check exploded", result["error"])


class DeleteResourceHelperTests(TestCase):
    """Direct tests of _delete_resource — the delete cycle, tested once."""

    def setUp(self):
        self.client = make_client()

    def test_short_circuits_when_resource_already_absent(self):
        perform_delete = MagicMock()

        result = self.client._delete_resource(
            exists_check=lambda: False,
            already_deleted_result=lambda: {"success": True, "already_deleted": True},
            perform_delete=perform_delete,
            validate_operation_label="op",
            validation_failure_prefix="failed",
            perform_verify=MagicMock(),
            verify_failure_message="not verified",
            success_result=MagicMock(),
            error_result=MagicMock(),
            error_log_message=lambda e: str(e),
        )

        self.assertEqual(result, {"success": True, "already_deleted": True})
        perform_delete.assert_not_called()

    def test_perform_delete_returning_none_skips_validation(self):
        """Matches delete_store: the vendored client call returns nothing
        useful to validate — the validation step must be skipped entirely,
        not called with None.
        """
        with patch.object(self.client, "validate_response") as mock_validate:
            result = self.client._delete_resource(
                exists_check=lambda: True,
                already_deleted_result=MagicMock(),
                perform_delete=lambda: None,
                validate_operation_label=None,
                validation_failure_prefix="failed",
                perform_verify=lambda: {"verified": True},
                verify_failure_message="not verified",
                success_result=lambda validated, verification: {
                    "success": True,
                    "validated_was": validated,
                },
                error_result=MagicMock(),
                error_log_message=lambda e: str(e),
            )

        mock_validate.assert_not_called()
        self.assertEqual(result, {"success": True, "validated_was": None})

    def test_perform_delete_returning_dict_is_validated(self):
        with patch.object(
            self.client, "validate_response", return_value={"success": True, "validated": True}
        ) as mock_validate:
            self.client._delete_resource(
                exists_check=lambda: True,
                already_deleted_result=MagicMock(),
                perform_delete=lambda: {"success": True},
                validate_operation_label="op",
                validation_failure_prefix="failed",
                perform_verify=lambda: {"verified": True},
                verify_failure_message="not verified",
                success_result=lambda validated, verification: {"success": True},
                error_result=MagicMock(),
                error_log_message=lambda e: str(e),
            )

        mock_validate.assert_called_once_with({"success": True}, "op")


class CreateWorkspaceTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_already_exists_short_circuits(self):
        with patch.object(self.client, "workspace_exists", return_value=True):
            result = self.client.create_workspace("mobility")

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Workspace 'mobility' already exists")
        self.assertEqual(result["workspace"], "mobility")
        self.assertFalse(result["created"])
        self.assertTrue(result["pre_existed"])

    def test_success_cycle(self):
        response = MagicMock(status_code=201, text="")
        with patch.object(self.client, "workspace_exists", return_value=False), \
                patch.object(self.client, "_request", return_value=response), \
                patch.object(
                    self.client, "post_verify_workspace", return_value={"verified": True}
                ):
            result = self.client.create_workspace("mobility")

        self.assertTrue(result["success"])
        self.assertTrue(result["created"])
        self.assertFalse(result["pre_existed"])
        self.assertEqual(result["workspace"], "mobility")

    def test_verify_failure_returns_recovery_needed(self):
        response = MagicMock(status_code=201, text="")
        with patch.object(self.client, "workspace_exists", return_value=False), \
                patch.object(self.client, "_request", return_value=response), \
                patch.object(
                    self.client,
                    "post_verify_workspace",
                    return_value={"verified": False, "message": "gone"},
                ):
            result = self.client.create_workspace("mobility")

        self.assertFalse(result["success"])
        self.assertFalse(result["created"])
        self.assertTrue(result["recovery_needed"])


class DeleteWorkspaceTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_already_deleted_short_circuits(self):
        with patch.object(self.client, "workspace_exists", return_value=False):
            result = self.client.delete_workspace("mobility")

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Workspace 'mobility' does not exist")
        self.assertEqual(result["workspace"], "mobility")
        self.assertFalse(result["deleted"])
        self.assertTrue(result["already_deleted"])

    def test_success_ignores_lingering_post_check(self):
        """delete_workspace's post-check is soft: even if the workspace
        still appears to exist afterwards, it only logs a warning and still
        reports success — matches the pre-refactor behavior exactly.
        """
        response = MagicMock(status_code=200, text="")
        with patch.object(self.client, "workspace_exists", side_effect=[True, True]), \
                patch.object(self.client, "_request", return_value=response):
            result = self.client.delete_workspace("mobility")

        self.assertTrue(result["success"])
        self.assertTrue(result["deleted"])


class CreateStoreTests(TestCase):
    def setUp(self):
        self.client = make_client()
        self.store_data = {
            "name": "gis_store",
            "database": "gis",
            "host": "db",
            "port": 5432,
            "user": "postgres",
            "passwd": "secret",
            "schema": "public",
        }

    def test_already_exists_short_circuits(self):
        with patch.object(self.client, "pre_check_store", return_value={"exists": True}):
            result = self.client.create_store("mobility", self.store_data)

        self.assertTrue(result["pre_existed"])
        self.assertFalse(result["created"])
        self.client._client.create_featurestore.assert_not_called()

    def test_success_cycle(self):
        with patch.object(self.client, "pre_check_store", return_value={"exists": False}), \
                patch.object(
                    self.client, "post_verify_store", return_value={"verified": True}
                ):
            self.client._client.create_featurestore.return_value = {"success": True}
            result = self.client.create_store("mobility", self.store_data)

        self.assertTrue(result["success"])
        self.assertTrue(result["created"])
        self.assertEqual(result["store"], "gis_store")
        self.assertEqual(result["workspace"], "mobility")


class DeleteStoreTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_already_deleted_short_circuits(self):
        with patch.object(self.client, "pre_check_store", return_value={"exists": False}):
            result = self.client.delete_store("mobility", "gis_store")

        self.assertTrue(result["already_deleted"])
        self.client._client.delete_featurestore.assert_not_called()

    def test_success_cycle_skips_response_validation(self):
        """delete_featurestore() has nothing to validate — regression guard
        that this path doesn't call validate_response at all.
        """
        with patch.object(self.client, "pre_check_store", return_value={"exists": True}), \
                patch.object(
                    self.client, "post_verify_store", return_value={"verified": True}
                ), \
                patch.object(self.client, "validate_response") as mock_validate:
            result = self.client.delete_store("mobility", "gis_store")

        mock_validate.assert_not_called()
        self.assertTrue(result["success"])
        self.assertTrue(result["deleted"])

    def test_verify_failure_returns_error(self):
        with patch.object(self.client, "pre_check_store", return_value={"exists": True}), \
                patch.object(
                    self.client,
                    "post_verify_store",
                    return_value={"verified": False, "message": "still there"},
                ):
            result = self.client.delete_store("mobility", "gis_store")

        self.assertFalse(result["success"])
        self.assertIn("still there", result["error"])
