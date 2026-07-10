"""
Thin, controlled wrapper around geoserver-rest
"""
import sys
import os
import logging
from typing import Callable, Dict, Optional
import requests

# Add geoserver-rest to Python path
geoserver_rest_path = os.path.join(os.path.dirname(__file__), 'geoserver-rest')
if geoserver_rest_path not in sys.path:
    sys.path.insert(0, geoserver_rest_path)

from geo.Geoserver import Geoserver as GeoServerRestClient
from ..exceptions import GeoServerConnectionError, GeoServerPublishError

logger = logging.getLogger(__name__)


class GeoServerClient:
    """
    Thin, controlled wrapper around geoserver-rest
    Provides normalized responses and error handling
    """

    def __init__(self, url: str, username: str, password: str):
        """
        Initialize GeoServer client

        Args:
            url: GeoServer base URL (e.g., 'http://localhost:8080/geoserver')
            username: GeoServer admin username
            password: GeoServer admin password
        """
        self.url = url
        self.username = username
        self.password = password
        try:
            self._client = GeoServerRestClient(url, username=username, password=password)
            logger.info(f"GeoServer client initialized for {url}")
        except Exception as e:
            logger.error(f"Failed to initialize GeoServer client: {e}")
            raise GeoServerConnectionError(f"Failed to connect to GeoServer: {e}")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.url.rstrip('/')}{path}"
        try:
            response = requests.request(
                method=method,
                url=url,
                auth=(self.username, self.password),
                timeout=30,
                **kwargs,
            )
            logger.info(
                "GeoServer %s %s -> HTTP %s",
                method.upper(),
                url,
                response.status_code,
            )
            return response
        except Exception as e:
            logger.error("GeoServer request failed: %s %s -> %s", method, url, e)
            raise GeoServerConnectionError(f"GeoServer request failed for {url}: {e}")

    @staticmethod
    def _exception_status(exc) -> Optional[int]:
        status = getattr(exc, 'status', None)
        if isinstance(status, int):
            return status
        message = str(exc)
        if 'Status : 404' in message or 'HTTP 404' in message:
            return 404
        if 'Status : 403' in message or 'HTTP 403' in message:
            return 403
        return None

    @classmethod
    def _is_not_found_error(cls, exc) -> bool:
        return cls._exception_status(exc) == 404

    @staticmethod
    def _file_path_from_url(value: str) -> str:
        """Normalize GeoServer file URLs to the path Django stores locally."""
        if not value:
            return ""
        if value.startswith("file://"):
            return value.removeprefix("file://")
        if value.startswith("file:"):
            return value.removeprefix("file:")
        return value

    def _create_resource(
        self,
        *,
        exists_check: Callable[[], bool],
        already_exists_result: Callable[[], Dict],
        perform_create: Callable[[], Dict],
        validate_operation_label: str,
        validation_failure_prefix: str,
        perform_verify: Callable[[], Dict],
        verify_failure_message: str,
        success_result: Callable[[Dict, Dict], Dict],
        error_result: Callable[[Exception], Dict],
        error_log_message: Callable[[Exception], str],
    ) -> Dict:
        """
        Shared GeoServer-first create cycle: pre-check -> operation ->
        response validation -> post-verify -> final response.

        Every create_* method (create_workspace, create_store, ...) used to
        repeat this exact five-step control flow with its own copy of the
        try/except and validation/verification checks. This centralizes the
        control flow; callers supply only what's resource-specific (the
        actual GeoServer call, and the shape of each returned dict) via the
        callables above. See create_workspace/create_store for usage.
        """
        try:
            if exists_check():
                return already_exists_result()

            raw_result = perform_create()
            validated_result = self.validate_response(raw_result, validate_operation_label)

            if not validated_result.get('success', False):
                raise GeoServerPublishError(
                    f"{validation_failure_prefix}: {validated_result.get('message')}"
                )

            verification = perform_verify()
            if not verification.get('verified', False):
                raise GeoServerPublishError(
                    verification.get('message') or verify_failure_message
                )

            return success_result(validated_result, verification)

        except Exception as e:
            logger.error(error_log_message(e))
            return error_result(e)

    def _delete_resource(
        self,
        *,
        exists_check: Callable[[], bool],
        already_deleted_result: Callable[[], Dict],
        perform_delete: Callable[[], Optional[Dict]],
        validate_operation_label: Optional[str],
        validation_failure_prefix: str,
        perform_verify: Callable[[], Dict],
        verify_failure_message: str,
        success_result: Callable[[Optional[Dict], Dict], Dict],
        error_result: Callable[[Exception], Dict],
        error_log_message: Callable[[Exception], str],
    ) -> Dict:
        """
        Shared GeoServer-first delete cycle: pre-check -> operation ->
        optional response validation -> post-verify -> final response.

        `perform_delete` may return None when the underlying call has
        nothing to validate (e.g. geoserver-rest's delete_featurestore()
        returns a plain string on success and raises on failure) — in that
        case the validation step is skipped, matching the original
        per-method behavior exactly.
        """
        try:
            if not exists_check():
                return already_deleted_result()

            raw_result = perform_delete()
            validated_result = None
            if raw_result is not None:
                validated_result = self.validate_response(raw_result, validate_operation_label)
                if not validated_result.get('success', False):
                    raise GeoServerPublishError(
                        f"{validation_failure_prefix}: {validated_result.get('message')}"
                    )

            verification = perform_verify()
            if not verification.get('verified', False):
                raise GeoServerPublishError(
                    verification.get('message') or verify_failure_message
                )

            return success_result(validated_result, verification)

        except Exception as e:
            logger.error(error_log_message(e))
            return error_result(e)

    # Workspace operations

    def create_workspace(self, name: str) -> Dict:
        """
        Create workspace in GeoServer using GeoServer-first pattern.
        Implements: Pre-Check -> Operation + Validation -> Post-Check

        Args:
            name: Workspace name

        Returns:
            Dict with success status and details
        """
        logger.info(f"Starting workspace creation with GeoServer-first pattern: {name}")

        def _perform_create() -> Dict:
            logger.info(f"Creating workspace in GeoServer: {name}")
            payload = f"<workspace><name>{name}</name></workspace>"
            logger.info("GeoServer create_workspace payload for '%s': %s", name, payload)
            response = self._request(
                "post",
                "/rest/workspaces",
                data=payload,
                headers={"content-type": "text/xml"},
            )
            logger.info(
                "GeoServer create_workspace response for '%s': status=%s body=%r",
                name,
                response.status_code,
                response.text[:500],
            )
            return {
                'success': response.status_code == 201,
                'validated': response.status_code == 201,
                'status_code': response.status_code,
                'message': response.text.strip() or f"HTTP {response.status_code}",
            }

        def _perform_verify() -> Dict:
            verification = self.post_verify_workspace(name, expected_exists=True)
            if not verification.get('verified', False):
                logger.error(f"Post-verification failed for workspace {name}: {verification['message']}")
            return verification

        def _already_exists_result() -> Dict:
            logger.info(f"Workspace {name} already exists (pre-check)")
            return {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' already exists",
                'created': False,
                'pre_existed': True,
            }

        result = self._create_resource(
            exists_check=lambda: self.workspace_exists(name),
            already_exists_result=_already_exists_result,
            perform_create=_perform_create,
            validate_operation_label=f"create_workspace({name})",
            validation_failure_prefix="Workspace creation failed validation",
            perform_verify=_perform_verify,
            verify_failure_message=f"Workspace '{name}' could not be verified in GeoServer after create.",
            success_result=lambda validated, verification: {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' created successfully",
                'created': True,
                'pre_existed': False,
                'validated': validated.get('validated', False),
                'verified': True,
                'geoserver_response': validated,
            },
            error_result=lambda e: {
                'success': False,
                'workspace': name,
                'error': str(e),
                'message': f"Failed to create workspace '{name}': {e}",
                'created': False,
                'recovery_needed': True,
            },
            error_log_message=lambda e: f"Failed to create workspace {name}: {e}",
        )

        if result.get('created'):
            logger.info(f"Workspace creation completed: {name} (verified: {result.get('verified')})")
        return result

    def delete_workspace(self, name: str) -> Dict:
        """
        Delete workspace from GeoServer

        Args:
            name: Workspace name

        Returns:
            Dict with success status and details
        """
        logger.info(f"Deleting workspace from GeoServer: {name}")

        def _already_deleted_result() -> Dict:
            logger.info(f"Workspace {name} does not exist, nothing to delete")
            return {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' does not exist",
                'deleted': False,
                'already_deleted': True,
            }

        def _perform_delete() -> Dict:
            response = self._request("delete", f"/rest/workspaces/{name}", params={"recurse": "true"})
            return {
                'success': response.status_code == 200,
                'validated': response.status_code == 200,
                'status_code': response.status_code,
                'message': response.text.strip() or f"HTTP {response.status_code}",
            }

        def _perform_verify() -> Dict:
            # Soft post-check: log a warning but never fail the deletion on
            # this — matches the original behavior exactly.
            if self.workspace_exists(name):
                logger.warning(f"Workspace {name} still exists after deletion attempt")
            return {'verified': True}

        def _success_result(validated: Dict, verification: Dict) -> Dict:
            logger.info(f"Workspace deleted successfully: {name}")
            return {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' deleted successfully",
                'deleted': True,
                'geoserver_response': validated,
            }

        return self._delete_resource(
            exists_check=lambda: self.workspace_exists(name),
            already_deleted_result=_already_deleted_result,
            perform_delete=_perform_delete,
            validate_operation_label=f"delete_workspace({name})",
            validation_failure_prefix="Workspace deletion failed validation",
            perform_verify=_perform_verify,
            verify_failure_message=f"Workspace '{name}' could not be verified as deleted.",
            success_result=_success_result,
            error_result=lambda e: {
                'success': False,
                'workspace': name,
                'error': str(e),
                'message': f"Failed to delete workspace '{name}': {e}",
                'deleted': False,
            },
            error_log_message=lambda e: f"Failed to delete workspace {name}: {e}",
        )

    def workspace_exists(self, workspace: str) -> bool:
        """
        Check if workspace exists in GeoServer.

        Args:
            workspace: Workspace name

        Returns:
            True if workspace exists
        """
        try:
            self.get_workspace(workspace)
            return True
        except Exception as e:
            logger.warning(f"Failed to check workspace {workspace}: {e}")
            return False

    def get_workspace(self, workspace: str) -> Dict:
        """
        Get a single workspace from GeoServer.
        Raises GeoServerConnectionError on any network or HTTP failure.
        """
        try:
            response = self._request("get", f"/rest/workspaces/{workspace}.json", params={"recurse": "true"})
            if response.status_code != 200:
                raise GeoServerConnectionError(
                    f"Workspace detail failed with HTTP {response.status_code}: {response.text}"
                )
            result = response.json()
            workspace_data = result.get('workspace', {})
            if workspace_data.get('name') != workspace:
                raise GeoServerConnectionError(
                    f"Workspace detail response mismatch for '{workspace}' at {self.url}"
                )
            return workspace_data
        except Exception as e:
            logger.error(f"Failed to get workspace '{workspace}' from GeoServer: {e}")
            raise GeoServerConnectionError(
                f"Failed to get workspace '{workspace}' from GeoServer at {self.url}: {e}"
            )

    def get_workspaces(self) -> list:
        """
        Get list of workspace names from GeoServer.
        Raises GeoServerConnectionError on any network or HTTP failure.
        """
        try:
            response = self._request("get", "/rest/workspaces")
            if response.status_code != 200:
                raise GeoServerConnectionError(
                    f"Workspace list failed with HTTP {response.status_code}: {response.text}"
                )
            workspaces = response.json()
            logger.info(f"GeoServer workspaces response: {workspaces}")

            if workspaces and 'workspaces' in workspaces:
                workspace_list = workspaces['workspaces'].get('workspace', [])
                if isinstance(workspace_list, dict):  # Single workspace response
                    workspace_list = [workspace_list]
                return [ws.get('name') for ws in workspace_list if isinstance(ws, dict) and 'name' in ws]
            return []
        except GeoServerConnectionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get workspaces from GeoServer: {e}")
            raise GeoServerConnectionError(f"Failed to get workspaces from GeoServer at {self.url}: {e}")

    def post_verify_workspace(self, workspace_name: str, expected_exists: bool = True) -> Dict:
        """
        Post-operation verification for workspace.

        Args:
            workspace_name: Workspace name to verify
            expected_exists: Whether workspace should exist after operation

        Returns:
            Verification result dict
        """
        try:
            detail_exists = False
            list_exists = False

            try:
                self.get_workspace(workspace_name)
                detail_exists = True
            except GeoServerConnectionError:
                detail_exists = False

            try:
                list_exists = workspace_name in self.get_workspaces()
            except GeoServerConnectionError:
                list_exists = False

            actual_exists = detail_exists and list_exists
            logger.info(
                "Workspace verify '%s': expected_exists=%s detail_exists=%s list_exists=%s actual_exists=%s",
                workspace_name,
                expected_exists,
                detail_exists,
                list_exists,
                actual_exists,
            )

            if actual_exists == expected_exists:
                return {
                    'success': True,
                    'verified': True,
                    'workspace': workspace_name,
                    'exists': actual_exists,
                    'message': (
                        f'Workspace verification passed: detail_exists={detail_exists}, '
                        f'list_exists={list_exists}'
                    ),
                }
            return {
                'success': False,
                'verified': False,
                'workspace': workspace_name,
                'exists': actual_exists,
                'expected': expected_exists,
                'message': (
                    f'Workspace verification failed: expected={expected_exists}, '
                    f'detail_exists={detail_exists}, list_exists={list_exists}'
                ),
            }

        except Exception as e:
            logger.error(f"Post-verification failed for workspace {workspace_name}: {e}")
            return {
                'success': False,
                'verified': False,
                'error': str(e),
                'message': 'Post-verification error',
            }

    # Store operations

    def create_postgis_store(
        self,
        name: str,
        workspace: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str = "public",
    ) -> Dict:
        """
        Create PostGIS datastore in GeoServer using strict create + verify flow.
        """
        return self.create_store(
            workspace=workspace,
            store_data={
                'name': name,
                'host': host,
                'port': port,
                'database': database,
                'user': username,
                'passwd': password,
                'schema': schema or 'public',
            },
        )

    def update_postgis_store(
        self,
        name: str,
        workspace: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str = "public",
    ) -> Dict:
        """Update an existing PostGIS datastore in GeoServer and verify details."""
        logger.info(f"Updating PostGIS store '{name}' in workspace '{workspace}'")

        try:
            pre_check = self.pre_check_store(workspace, name)
            if not pre_check.get('exists', False):
                return {
                    'success': False,
                    'store': name,
                    'workspace': workspace,
                    'message': f"Store '{name}' does not exist in GeoServer.",
                    'updated': False,
                }

            raw_result = self._client.create_featurestore(
                store_name=name,
                workspace=workspace,
                db=database,
                host=host,
                port=port,
                schema=schema or 'public',
                pg_user=username,
                pg_password=password,
                overwrite=True,
            )
            validated_result = self.validate_response(raw_result, f"update_featurestore({name})")
            if not validated_result.get('success', False):
                raise GeoServerPublishError(
                    f"Store update failed validation: {validated_result.get('message')}"
                )

            verification = self.post_verify_store(
                workspace,
                name,
                expected_exists=True,
                expected_details={
                    'host': host,
                    'port': port or 5432,
                    'database': database,
                    'username': username,
                    'schema': schema or 'public',
                },
            )
            if not verification.get('verified', False):
                raise GeoServerPublishError(
                    verification.get('message')
                    or f"Store '{name}' could not be verified in GeoServer after update."
                )

            return {
                'success': True,
                'store': name,
                'workspace': workspace,
                'message': f"Store '{name}' updated successfully",
                'updated': True,
                'validated': validated_result.get('validated', False),
                'verified': verification.get('verified', False),
                'geoserver_response': validated_result,
            }
        except Exception as e:
            logger.error(f"Failed to update store '{name}': {e}")
            return {
                'success': False,
                'store': name,
                'workspace': workspace,
                'error': str(e),
                'message': f"Store update failed: {e}",
                'updated': False,
            }

    def store_exists(self, workspace: str, store_name: str) -> bool:
        """
        Check if datastore exists in GeoServer.

        Args:
            workspace: Workspace name
            store_name: Store name

        Returns:
            True if store exists
        """
        try:
            stores = self.get_datastores(workspace)
            return any(ds.get('name') == store_name for ds in stores)
        except Exception as e:
            logger.warning(f"Failed to check store {workspace}/{store_name}: {e}")
            return False

    def get_datastores(self, workspace: str) -> list:
        """
        Get list of vector/raster stores from a GeoServer workspace.

        Vector stores come from the datastore endpoint. Raster GeoTIFF stores
        come from the coverage-store endpoint and are normalized into the same
        store dict shape with store_type='geotiff'.

        For datastores, calls individual store detail endpoint for each store so
        the returned dicts contain real host/port/database/username/schema values
        suitable for directly populating Django Store model fields.

        Args:
            workspace: Workspace name

        Returns:
            List of normalised datastore dicts:
              name, host, port, database, username, schema, store_type, file_path
        """
        try:
            stores_resp = self._client.get_datastores(workspace)
            logger.info(f"GeoServer datastores list for {workspace}: {stores_resp}")

            result = []
            if not stores_resp or 'dataStores' not in stores_resp:
                stores_resp = None

            if stores_resp:
                store_data = stores_resp['dataStores']
                if store_data != '' and store_data is not None:
                    store_list = store_data.get('dataStore', [])
                    if isinstance(store_list, dict):
                        store_list = [store_list]

                    for ds in store_list:
                        if not isinstance(ds, dict):
                            continue
                        name = ds.get('name', '')
                        if not name:
                            continue
                        detail = self.get_datastore_detail(workspace, name)
                        result.append(detail)

            result.extend(self.get_coverage_stores(workspace))
            return result

        except GeoServerConnectionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get datastores from workspace {workspace}: {e}")
            raise GeoServerConnectionError(
                f"Failed to get datastores for workspace '{workspace}' from GeoServer at {self.url}: {e}"
            )

    def get_datastore_detail(self, workspace: str, store_name: str) -> dict:
        """
        Fetch full connection parameters for a single datastore.
        GeoServer stores connection params as a list of {"@key": k, "$": v} entries.

        Returns:
            Normalised dict: name, host, port, database, username, schema, store_type
        """
        try:
            raw = self._client.get_datastore(store_name, workspace=workspace)
            ds = raw.get('dataStore', {})

            # Parse connectionParameters → flat dict
            params: dict = {}
            entries = ds.get('connectionParameters', {}).get('entry', [])
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                if isinstance(entry, dict):
                    key = entry.get('@key', '')
                    val = entry.get('$', '')
                    if key:
                        params[key] = val

            dbtype = params.get('dbtype', '').lower()
            store_type = 'postgis' if dbtype in ('postgis', 'postgis_jndi', 'postgis ng') else 'file'
            file_path = ""
            if store_type == "file":
                file_path = self._file_path_from_url(
                    params.get("url", "") or params.get("database", "")
                )

            try:
                port = int(params.get('port', 5432))
            except (ValueError, TypeError):
                port = 5432

            raw_name = ds.get('name', store_name)
            
            # Check for workspace prefix (same issue as layers)
            if ':' in raw_name:
                clean_name = raw_name.split(':', 1)[1]
                logger.warning(
                    f"GeoServer returned store name with workspace prefix: '{raw_name}' → '{clean_name}'"
                )
                name = clean_name
            else:
                name = raw_name

            return {
                'name': name,
                'host': params.get('host', ''),
                'port': port,
                'database': params.get('database', ''),
                'username': params.get('user', ''),
                'schema': params.get('schema', 'public'),
                'store_type': store_type,
                'file_path': file_path,
            }
        except Exception as e:
            logger.warning(
                f"Could not fetch detail for store {workspace}/{store_name}: {e} — using defaults."
            )
            return {
                'name': store_name,
                'host': '',
                'port': 5432,
                'database': '',
                'username': '',
                'schema': 'public',
                'store_type': 'postgis',
                'file_path': '',
            }

    def get_coverage_stores(self, workspace: str) -> list:
        """Return GeoServer coverage stores normalized as Django geotiff stores."""
        try:
            response = self._request(
                "get",
                f"/rest/workspaces/{workspace}/coveragestores.json",
            )
            if response.status_code == 404:
                return []
            if response.status_code != 200:
                raise GeoServerConnectionError(
                    f"Coverage-store list failed with HTTP {response.status_code}: {response.text}"
                )

            payload = response.json()
            coverage_stores = payload.get("coverageStores", {}) if isinstance(payload, dict) else {}
            store_list = coverage_stores.get("coverageStore", []) if isinstance(coverage_stores, dict) else []
            if isinstance(store_list, dict):
                store_list = [store_list]
            if not isinstance(store_list, list):
                return []

            result = []
            for item in store_list:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                result.append(self.get_coverage_store_detail(workspace, item["name"]))
            return result
        except GeoServerConnectionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get coverage stores from workspace {workspace}: {e}")
            raise GeoServerConnectionError(
                f"Failed to get coverage stores for workspace '{workspace}' from GeoServer at {self.url}: {e}"
            )

    def get_coverage_store_detail(self, workspace: str, store_name: str) -> dict:
        """Fetch details for a single coverage store."""
        response = self._request(
            "get",
            f"/rest/workspaces/{workspace}/coveragestores/{store_name}.json",
        )
        if response.status_code != 200:
            raise GeoServerConnectionError(
                f"Coverage-store detail failed with HTTP {response.status_code}: {response.text}"
            )

        payload = response.json()
        coverage_store = payload.get("coverageStore", {}) if isinstance(payload, dict) else {}
        raw_name = coverage_store.get("name", store_name)
        name = raw_name.split(":", 1)[1] if ":" in raw_name else raw_name
        file_path = self._file_path_from_url(coverage_store.get("url", ""))
        return {
            "name": name,
            "host": "",
            "port": 5432,
            "database": "",
            "username": "",
            "schema": "public",
            "store_type": "geotiff",
            "file_path": file_path,
        }

    def probe_store_access(self, workspace: str, store_name: str) -> Dict:
        """
        Probe whether GeoServer can read a store and enumerate its feature types.

        This is a better operational signal than checking whether Django stores
        the DB password locally.
        """
        detail_path = f"/rest/workspaces/{workspace}/datastores/{store_name}.json"
        featuretypes_path = f"/rest/workspaces/{workspace}/datastores/{store_name}/featuretypes.json"

        try:
            detail_response = self._request("get", detail_path)
            if detail_response.status_code != 200:
                return {
                    'success': False,
                    'status': 'error',
                    'workspace': workspace,
                    'store': store_name,
                    'message': f"GeoServer datastore detail returned HTTP {detail_response.status_code}.",
                }

            featuretypes_response = self._request("get", featuretypes_path)
            if featuretypes_response.status_code != 200:
                return {
                    'success': False,
                    'status': 'error',
                    'workspace': workspace,
                    'store': store_name,
                    'message': f"GeoServer featuretypes probe returned HTTP {featuretypes_response.status_code}.",
                }

            payload = featuretypes_response.json()
            feature_types = payload.get('featureTypes') if isinstance(payload, dict) else None
            items = []
            if isinstance(feature_types, dict):
                items = feature_types.get('featureType', []) or []
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                items = []

            return {
                'success': True,
                'status': 'usable',
                'workspace': workspace,
                'store': store_name,
                'featuretype_count': len(items),
                'message': f"GeoServer store probe succeeded with {len(items)} feature type(s).",
            }
        except Exception as exc:
            logger.warning(
                "GeoServer store probe failed for %s/%s: %s",
                workspace,
                store_name,
                exc,
            )
            return {
                'success': False,
                'status': 'error',
                'workspace': workspace,
                'store': store_name,
                'message': str(exc),
            }

    def get_available_featuretypes(self, workspace: str, store_name: str) -> list:
        """
        Return publishable featuretypes GeoServer sees under a datastore.

        GeoServer commonly exposes unpublished resources via
        `?list=available_with_geom`. Some deployments only support
        `?list=available`, so we try both and normalize the payload.
        """
        path = f"/rest/workspaces/{workspace}/datastores/{store_name}/featuretypes.json"

        for list_mode in ("available_with_geom", "available"):
            try:
                response = self._request("get", path, params={"list": list_mode})
            except Exception as exc:
                logger.warning(
                    "GeoServer available featuretypes request failed for %s/%s (%s): %s",
                    workspace,
                    store_name,
                    list_mode,
                    exc,
                )
                continue

            if response.status_code != 200:
                logger.info(
                    "GeoServer available featuretypes returned HTTP %s for %s/%s (%s)",
                    response.status_code,
                    workspace,
                    store_name,
                    list_mode,
                )
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                logger.warning(
                    "GeoServer available featuretypes JSON parse failed for %s/%s (%s): %s",
                    workspace,
                    store_name,
                    list_mode,
                    exc,
                )
                continue

            names = []
            raw_list = payload.get("list") if isinstance(payload, dict) else None
            if isinstance(raw_list, dict):
                raw_items = raw_list.get("string", []) or []
                if isinstance(raw_items, str):
                    raw_items = [raw_items]
                if isinstance(raw_items, list):
                    names = [item for item in raw_items if isinstance(item, str) and item]

            if not names:
                feature_types = payload.get("featureTypes") if isinstance(payload, dict) else None
                if isinstance(feature_types, dict):
                    raw_items = feature_types.get("featureType", []) or []
                    if isinstance(raw_items, dict):
                        raw_items = [raw_items]
                    if isinstance(raw_items, list):
                        names = [
                            item.get("name")
                            for item in raw_items
                            if isinstance(item, dict) and item.get("name")
                        ]

            if names:
                logger.info(
                    "GeoServer available featuretypes for %s/%s (%s): %s",
                    workspace,
                    store_name,
                    list_mode,
                    names,
                )
                return names

        return []

    def create_store(self, workspace: str, store_data: dict) -> Dict:
        """
        Create PostGIS datastore in GeoServer using GeoServer-first pattern.

        Args:
            workspace: Target workspace name
            store_data: Store connection details

        Returns:
            Dict with creation results and verification status
        """
        store_name = store_data.get('name')
        logger.info(f"Creating store '{store_name}' in workspace '{workspace}'")

        def _perform_create() -> Dict:
            logger.info(f"Creating PostGIS store in GeoServer: {store_name}")
            return self._client.create_featurestore(
                store_name=store_name,
                workspace=workspace,
                db=store_data['database'],
                host=store_data['host'],
                port=store_data['port'],
                schema=store_data.get('schema', 'public'),
                pg_user=store_data['user'],
                pg_password=store_data['passwd'],
            )

        def _perform_verify() -> Dict:
            return self.post_verify_store(
                workspace,
                store_name,
                expected_exists=True,
                expected_details={
                    'host': store_data.get('host', ''),
                    'port': store_data.get('port', 5432),
                    'database': store_data.get('database', ''),
                    'username': store_data.get('user', ''),
                    'schema': store_data.get('schema', 'public'),
                },
            )

        def _success_result(validated: Dict, verification: Dict) -> Dict:
            logger.info(f"Store creation completed: {store_name} (verified: {verification.get('verified')})")
            return {
                'success': True,
                'store': store_name,
                'workspace': workspace,
                'message': f"Store '{store_name}' created successfully",
                'created': True,
                'pre_existed': False,
                'validated': validated.get('validated', False),
                'verified': verification.get('verified', False),
                'geoserver_response': validated,
            }

        return self._create_resource(
            exists_check=lambda: self.pre_check_store(workspace, store_name).get('exists', False),
            already_exists_result=lambda: {
                'success': True,
                'store': store_name,
                'workspace': workspace,
                'message': f"Store '{store_name}' already exists",
                'created': False,
                'pre_existed': True,
            },
            perform_create=_perform_create,
            validate_operation_label=f"create_featurestore({store_name})",
            validation_failure_prefix="Store creation failed validation",
            perform_verify=_perform_verify,
            verify_failure_message=f"Store '{store_name}' could not be verified in GeoServer after create.",
            success_result=_success_result,
            error_result=lambda e: {
                'success': False,
                'store': store_name,
                'workspace': workspace,
                'error': str(e),
                'message': f"Store creation failed: {e}",
            },
            error_log_message=lambda e: f"Failed to create store '{store_name}': {e}",
        )

    def delete_store(self, workspace: str, store: str) -> Dict:
        """
        Delete PostGIS datastore from GeoServer

        Args:
            workspace: Workspace name
            store: Store name

        Returns:
            Dict with success status and details
        """
        logger.info(f"Deleting store '{store}' from workspace '{workspace}'")

        def _already_deleted_result() -> Dict:
            logger.info(f"Store {store} does not exist in workspace {workspace}, nothing to delete")
            return {
                'success': True,
                'store': store,
                'workspace': workspace,
                'message': f"Store '{store}' does not exist",
                'deleted': False,
                'already_deleted': True,
            }

        def _perform_delete() -> Optional[Dict]:
            # delete_featurestore() returns a plain string on success and
            # raises GeoserverException on failure — no dict to validate.
            self._client.delete_featurestore(featurestore_name=store, workspace=workspace)
            return None

        def _success_result(validated: Optional[Dict], verification: Dict) -> Dict:
            logger.info(f"Store deleted successfully: {store} from workspace {workspace}")
            return {
                'success': True,
                'store': store,
                'workspace': workspace,
                'message': f"Store '{store}' deleted successfully",
                'deleted': True,
                'verified': verification.get('verified', False),
            }

        return self._delete_resource(
            exists_check=lambda: self.pre_check_store(workspace, store).get('exists', False),
            already_deleted_result=_already_deleted_result,
            perform_delete=_perform_delete,
            validate_operation_label=None,
            validation_failure_prefix="Store deletion failed validation",
            perform_verify=lambda: self.post_verify_store(workspace, store, expected_exists=False),
            verify_failure_message=f"Store '{store}' could not be verified as deleted in GeoServer.",
            success_result=_success_result,
            error_result=lambda e: {
                'success': False,
                'store': store,
                'workspace': workspace,
                'error': str(e),
                'message': f"Failed to delete store '{store}': {e}",
                'deleted': False,
            },
            error_log_message=lambda e: f"Failed to delete store '{store}' from workspace '{workspace}': {e}",
        )

    def pre_check_store(self, workspace: str, store_name: str) -> Dict:
        """Check if store already exists."""
        try:
            stores = self._client.get_datastores(workspace)
            store_exists = store_name in [
                store['name'] for store in stores.get('dataStores', {}).get('dataStore', [])
            ]
            return {
                'success': True,
                'exists': store_exists,
                'message': f'Store exists check: {store_exists}',
            }
        except Exception as e:
            return {'success': False, 'error': str(e), 'exists': False}

    def post_verify_store(
        self,
        workspace: str,
        store_name: str,
        expected_exists: bool = True,
        expected_details: Optional[dict] = None,
    ) -> Dict:
        """Verify store creation/deletion and optionally its resolved connection details."""
        try:
            stores = self.get_datastores(workspace)
            actual_store = next((store for store in stores if store.get('name') == store_name), None)
            actual_exists = actual_store is not None

            if (expected_exists and actual_exists) or (not expected_exists and not actual_exists):
                mismatch_fields = {}
                if expected_exists and actual_exists and expected_details:
                    actual_detail = actual_store or self.get_datastore_detail(workspace, store_name)
                    for field, expected_value in expected_details.items():
                        actual_value = actual_detail.get(field)
                        if field == 'port':
                            try:
                                actual_value = int(actual_value)
                            except (ValueError, TypeError):
                                actual_value = 5432
                            try:
                                expected_value = int(expected_value)
                            except (ValueError, TypeError):
                                expected_value = 5432
                        else:
                            actual_value = (actual_value or '').strip() if isinstance(actual_value, str) else actual_value
                            expected_value = (expected_value or '').strip() if isinstance(expected_value, str) else expected_value

                        if actual_value != expected_value:
                            mismatch_fields[field] = {
                                'expected': expected_value,
                                'actual': actual_value,
                            }

                if mismatch_fields:
                    return {
                        'success': False,
                        'verified': False,
                        'store': store_name,
                        'workspace': workspace,
                        'exists': actual_exists,
                        'mismatch_fields': mismatch_fields,
                        'message': f"Store verification failed: detail mismatch for {', '.join(sorted(mismatch_fields))}",
                    }

                return {
                    'success': True,
                    'verified': True,
                    'store': store_name,
                    'workspace': workspace,
                    'exists': actual_exists,
                    'message': f'Store verification passed: exists={actual_exists}',
                }
            return {
                'success': False,
                'verified': False,
                'store': store_name,
                'workspace': workspace,
                'exists': actual_exists,
                'expected': expected_exists,
                'message': f'Store verification failed: expected={expected_exists}, actual={actual_exists}',
            }

        except Exception as e:
            logger.error(f"Post-verification failed for store {store_name}: {e}")
            return {
                'success': False,
                'verified': False,
                'error': str(e),
                'message': 'Store post-verification error',
            }

    # Layer operations

    def publish_featuretype(
        self,
        store_name: str,
        workspace: str,
        pg_table: str,
        srid: int = 4326,
        geometry_type: str = "Point",
        layer_name: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict:
        """
        Publish FeatureType from PostGIS table

        Args:
            store_name: Datastore name
            workspace: Workspace name
            pg_table: PostGIS table name
            srid: Spatial Reference System ID (default: 4326)
            geometry_type: Geometry type (default: 'Point')
            layer_name: Layer name (defaults to pg_table)

        Returns:
            Dict with success status and layer details
        """
        if layer_name is None:
            layer_name = pg_table
        if title is None:
            title = layer_name

        try:
            featuretype_url = "{}/rest/workspaces/{}/datastores/{}/featuretypes".format(
                self.url.rstrip('/'),
                workspace,
                store_name,
            )
            featuretype_xml = """<featureType>
                    <name>{name}</name>
                    <nativeName>{native_name}</nativeName>
                    <title>{title}</title>
                    <srs>EPSG:{srid}</srs>
                    <advertised>true</advertised>
                </featureType>""".format(
                name=layer_name,
                native_name=pg_table,
                title=title,
                srid=srid,
            )
            response = self._client._requests(
                "post",
                featuretype_url,
                data=featuretype_xml,
                headers={"content-type": "text/xml"},
            )
            if response.status_code not in (200, 201):
                raise GeoServerPublishError(
                    f"publish featuretype returned unexpected status {response.status_code}: {response.text}"
                )

            # Trigger GeoServer to recalculate native + lat/lon bounding box.
            try:
                self._client.edit_featuretype(
                    store_name=store_name,
                    workspace=workspace,
                    pg_table=layer_name,
                    name=layer_name,
                    title=title,
                    recalculate='nativebbox,latlonbbox',
                )
            except Exception as bbox_exc:
                # bbox recalc failure is non-fatal — log and continue
                logger.warning(
                    f"bbox recalculation failed for {workspace}/{layer_name}: {bbox_exc}"
                )

            logger.info(
                "Published featuretype: %s/%s (native table: %s, title: %s)",
                workspace,
                layer_name,
                pg_table,
                title,
            )
            return {
                'success': True,
                'workspace': workspace,
                'layer': layer_name,
                'title': title,
                'store': store_name,
                'table': pg_table,
                'message': f"Layer '{layer_name}' published successfully in workspace '{workspace}'",
            }
        except Exception as e:
            logger.error(f"Failed to publish featuretype {workspace}/{layer_name}: {e}")
            raise GeoServerPublishError(f"Failed to publish layer '{layer_name}': {e}")

    def delete_layer(self, workspace: str, layer_name: str) -> Dict:
        """
        Delete layer from GeoServer

        Args:
            workspace: Workspace name
            layer_name: Layer name

        Returns:
            Dict with success status
        """
        try:
            self._client.delete_layer(layer_name, workspace)
            logger.info(f"Deleted layer: {workspace}/{layer_name}")
            return {
                'success': True,
                'workspace': workspace,
                'layer': layer_name,
                'message': f"Layer '{layer_name}' deleted successfully",
            }
        except Exception as e:
            if self._is_not_found_error(e):
                logger.info(
                    "Layer already absent in GeoServer, treating delete as idempotent success: %s/%s",
                    workspace,
                    layer_name,
                )
                return {
                    'success': True,
                    'workspace': workspace,
                    'layer': layer_name,
                    'deleted': False,
                    'already_deleted': True,
                    'message': f"Layer '{layer_name}' was already absent in GeoServer.",
                }
            logger.error(f"Failed to delete layer {workspace}/{layer_name}: {e}")
            return {
                'success': False,
                'workspace': workspace,
                'layer': layer_name,
                'deleted': False,
                'error': str(e),
                'message': f"Failed to delete layer '{layer_name}': {e}",
            }

    def create_layer(
        self,
        workspace: str,
        layer_name: str,
        featuretype_name: str,
        store_name: str,
    ) -> Dict:
        """
        Create a layer from an existing featuretype

        Args:
            workspace: Workspace name
            layer_name: Layer name to create
            featuretype_name: Existing featuretype name
            store_name: Store name

        Returns:
            Dict with success status
        """
        try:
            # Create layer by PUT to /rest/workspaces/{workspace}/layers/{layer_name}
            layer_xml = f"""<layer>
  <name>{layer_name}</name>
  <type>VECTOR</type>
  <defaultStyle>
    <name>point</name>
  </defaultStyle>
  <resource class="featureType">
    <name>{featuretype_name}</name>
    <workspace>{workspace}</workspace>
  </resource>
</layer>"""
            layer_url = f"{self.url.rstrip('/')}/rest/workspaces/{workspace}/layers/{layer_name}"
            response = self._client._requests(
                'put',
                layer_url,
                data=layer_xml,
                headers={"Content-Type": "application/xml"}
            )
            if response.status_code not in [200, 201]:
                raise GeoServerPublishError(
                    f"Failed to create layer '{layer_name}': Status {response.status_code} - {response.text}"
                )
            logger.info(f"Created layer: {workspace}/{layer_name} from featuretype {featuretype_name}")
            return {
                'success': True,
                'workspace': workspace,
                'layer': layer_name,
                'featuretype': featuretype_name,
                'message': f"Layer '{layer_name}' created successfully",
            }
        except Exception as e:
            logger.error(f"Failed to create layer {workspace}/{layer_name}: {e}")
            raise GeoServerPublishError(f"Failed to create layer '{layer_name}': {e}")

    def get_layer_info(self, workspace: str, layer_name: str) -> Optional[Dict]:
        """
        Get layer information from GeoServer

        Args:
            workspace: Workspace name
            layer_name: Layer name

        Returns:
            Dict with layer info or None if not found
        """
        try:
            result = self._client.get_layer(layer_name, workspace)
            if result:
                return {
                    'workspace': workspace,
                    'layer': layer_name,
                    'exists': True,
                    'details': result,
                }
            return None
        except Exception as e:
            logger.warning(f"Layer {workspace}/{layer_name} not found or error: {e}")
            return None

    def get_layer_settings(self, workspace: str, layer_name: str) -> Dict:
        """
        Return GeoServer layer settings used by the Publishing tab:
        queryable, opaque, defaultStyle, and selected/additional styles.
        """
        try:
            result = self._client.get_layer(layer_name, workspace)
            layer = result.get('layer', {}) if isinstance(result, dict) else {}
            default_style = layer.get('defaultStyle') or {}
            styles_payload = layer.get('styles') or {}
            style_items = styles_payload.get('style', []) if isinstance(styles_payload, dict) else []
            if isinstance(style_items, dict):
                style_items = [style_items]
            if not isinstance(style_items, list):
                style_items = []

            default_style_name = default_style.get('name') if isinstance(default_style, dict) else ''
            additional_style_names = [
                item.get('name')
                for item in style_items
                if isinstance(item, dict) and item.get('name')
            ]

            return {
                'queryable': self._as_bool(layer.get('queryable'), default=True),
                'opaque': self._as_bool(layer.get('opaque'), default=False),
                'default_style_name': default_style_name or '',
                'additional_style_names': additional_style_names,
                'selected_style_names': [
                    name for name in [default_style_name, *additional_style_names] if name
                ],
            }
        except Exception as exc:
            logger.warning(
                'get_layer_settings failed for %s/%s: %s',
                workspace, layer_name, exc,
            )
            return {
                'queryable': True,
                'opaque': False,
                'default_style_name': '',
                'additional_style_names': [],
                'selected_style_names': [],
            }

    def update_featuretype(
        self,
        workspace: str,
        store_name: str,
        featuretype_name: str,
        title: str,
        abstract: Optional[str] = None,
    ) -> Dict:
        """
        Update title and/or abstract of an existing featuretype in GeoServer.

        Wraps geoserver-rest edit_featuretype PUT.
        `featuretype_name` is both the URL path param and the immutable <name>
        element — we never rename the resource identifier via this method.

        Returns:
            {'success': True, ...} on success.
        Raises:
            GeoServerPublishError on failure.
        """
        try:
            status_code = self._client.edit_featuretype(
                store_name=store_name,
                workspace=workspace,
                pg_table=featuretype_name,
                name=featuretype_name,
                title=title,
                abstract=abstract or None,
            )
            if status_code != 200:
                raise GeoServerPublishError(
                    f"edit_featuretype returned unexpected status {status_code}"
                )
            logger.info(
                'update_featuretype: %s/%s/%s updated (title=%r)',
                workspace, store_name, featuretype_name, title,
            )
            return {
                'success': True,
                'workspace': workspace,
                'store': store_name,
                'featuretype': featuretype_name,
                'title': title,
                'message': f"Featuretype '{featuretype_name}' updated in GeoServer.",
            }
        except GeoServerPublishError:
            raise
        except Exception as exc:
            logger.error(
                'update_featuretype failed for %s/%s/%s: %s',
                workspace, store_name, featuretype_name, exc,
            )
            raise GeoServerPublishError(
                f"Failed to update featuretype '{featuretype_name}': {exc}"
            )

    def set_layer_advertised(
        self,
        workspace: str,
        layer_name: str,
        advertised: bool,
        store_name: Optional[str] = None,
        featuretype_name: Optional[str] = None,
    ) -> Dict:
        """
        Set the advertised flag on a GeoServer layer.

        GeoServer stores `advertised` on TWO separate resources:
          1. Layer   → PUT /rest/workspaces/{ws}/layers/{name}.xml
          2. FeatureType → PUT /rest/workspaces/{ws}/datastores/{store}/featuretypes/{ft}.xml

        We always write to (1). When store_name + featuretype_name are provided we
        also write to (2), which is where _get_featuretype_advertised reads from.
        Both must agree to ensure the UI reflects the real GeoServer state.

        advertised=True  → layer visible in WMS/WFS GetCapabilities
        advertised=False → layer hidden from capabilities

        Returns:
            {'success': True, ...} on success.
        Raises:
            GeoServerPublishError on failure.
        """
        adv_str = 'true' if advertised else 'false'
        headers = {'content-type': 'text/xml'}
        try:
            # 1) Update the Layer resource
            layer_url = "{}/rest/workspaces/{}/layers/{}.xml".format(
                self.url.rstrip('/'), workspace, layer_name
            )
            layer_body = "<layer><advertised>{}</advertised></layer>".format(adv_str)
            r = self._client._requests('put', layer_url, data=layer_body, headers=headers)
            if r.status_code not in (200, 201):
                raise GeoServerPublishError(
                    f"set_layer_advertised (layer) returned HTTP {r.status_code}: {r.content}"
                )
            logger.info(
                'set_layer_advertised layer resource: %s/%s advertised=%s',
                workspace, layer_name, advertised,
            )

            # 2) Also update the FeatureType resource when store info is available.
            #    _get_featuretype_advertised reads from here, so both must match.
            if store_name and featuretype_name:
                ft_url = "{}/rest/workspaces/{}/datastores/{}/featuretypes/{}.xml".format(
                    self.url.rstrip('/'), workspace, store_name, featuretype_name
                )
                ft_body = "<featureType><advertised>{}</advertised></featureType>".format(adv_str)
                r2 = self._client._requests('put', ft_url, data=ft_body, headers=headers)
                if r2.status_code not in (200, 201):
                    raise GeoServerPublishError(
                        f"set_layer_advertised (featuretype) returned HTTP {r2.status_code}: {r2.content}"
                    )
                logger.info(
                    'set_layer_advertised featuretype resource: %s/%s/%s advertised=%s',
                    workspace, store_name, featuretype_name, advertised,
                )

            return {
                'success': True,
                'workspace': workspace,
                'layer': layer_name,
                'advertised': advertised,
                'message': f"Layer '{layer_name}' advertised set to {advertised}.",
            }
        except GeoServerPublishError:
            raise
        except Exception as exc:
            logger.error(
                'set_layer_advertised failed for %s/%s: %s',
                workspace, layer_name, exc,
            )
            raise GeoServerPublishError(
                f"Failed to set advertised on layer '{layer_name}': {exc}"
            )

    def verify_featuretype(self, workspace: str, store_name: str, featuretype_name: str) -> bool:
        """
        Confirm that a featuretype really exists in GeoServer after publishing.

        Uses the datastore's featuretypes endpoint — the same resource that
        publish_featurestore creates.  This is the authoritative check; the
        global /layers/ endpoint is unreliable for unadvertised layers.

        Returns True if the featuretype is present, False otherwise.
        Never raises — failures are logged as warnings.
        """
        try:
            ft_names = self._client.get_featuretypes(
                workspace=workspace, store_name=store_name
            )
            exists = featuretype_name in ft_names
            if exists:
                logger.info(
                    'verify_featuretype: %s/%s/%s — FOUND',
                    workspace, store_name, featuretype_name,
                )
            else:
                logger.warning(
                    'verify_featuretype: %s/%s/%s — NOT FOUND (featuretypes: %s)',
                    workspace, store_name, featuretype_name, ft_names,
                )
            return exists
        except Exception as exc:
            logger.warning(
                'verify_featuretype: could not read featuretypes for %s/%s: %s',
                workspace, store_name, exc,
            )
            return False

    def get_layers(self, workspace: str) -> list:
        """
        Get all layers for a workspace, grouped by their source datastore.

        Strategy: iterate stores → featuretypes so we know which store each
        layer belongs to without a separate per-layer REST call.
        Layer names are stored WITHOUT the 'workspace:' prefix.

        Args:
            workspace: Workspace name

        Returns:
            List of layer dicts: {name, store_name}
        """
        try:
            # Get store names (list endpoint only — we just need names here)
            stores_resp = self._client.get_datastores(workspace)
            logger.info(f"GeoServer stores for layer traversal in {workspace}: {stores_resp}")

            result = []

            if stores_resp and 'dataStores' in stores_resp:
                store_data = stores_resp['dataStores']
                if store_data and store_data != '':
                    store_list = store_data.get('dataStore', [])
                    if isinstance(store_list, dict):
                        store_list = [store_list]

                    for ds in store_list:
                        if not isinstance(ds, dict):
                            continue
                        store_name = ds.get('name', '')
                        if not store_name:
                            continue
                        try:
                            ft_names = self._client.get_featuretypes(
                                workspace=workspace, store_name=store_name
                            )
                            for ft_name in ft_names:
                                clean = (
                                    ft_name.split(':', 1)[1]
                                    if ':' in ft_name
                                    else ft_name
                                )
                                featuretype_detail = self.get_featuretype_detail(
                                    workspace, store_name, clean
                                )
                                layer_settings = self.get_layer_settings(workspace, clean)
                                result.append({
                                    'name': clean,
                                    'store_name': store_name,
                                    'advertised': featuretype_detail.get('advertised', True),
                                    'title': featuretype_detail.get('title', clean),
                                    'table_name': featuretype_detail.get('native_name', clean),
                                    'queryable': layer_settings.get('queryable', True),
                                    'opaque': layer_settings.get('opaque', False),
                                    'default_style_name': layer_settings.get('default_style_name', ''),
                                    'additional_style_names': layer_settings.get('additional_style_names', []),
                                })
                                logger.debug(
                                    f"Layer discovered: {workspace}/{store_name}/{clean} "
                                    f"(advertised={featuretype_detail.get('advertised', True)})"
                                )
                        except Exception as ft_err:
                            logger.warning(
                                f"Could not list featuretypes for store "
                                f"{workspace}/{store_name}: {ft_err}"
                            )

            result.extend(self.get_coverage_layers(workspace))

            logger.info(
                f"get_layers({workspace}) found {len(result)} layers across all stores"
            )
            return result

        except GeoServerConnectionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get layers from workspace {workspace}: {e}")
            raise GeoServerConnectionError(
                f"Failed to get layers for workspace '{workspace}' from GeoServer at {self.url}: {e}"
            )

    def get_coverage_layers(self, workspace: str) -> list:
        """Return coverage layers for all coverage stores in a workspace."""
        result = []
        for store in self.get_coverage_stores(workspace):
            store_name = store.get("name", "")
            if not store_name:
                continue
            try:
                response = self._request(
                    "get",
                    f"/rest/workspaces/{workspace}/coveragestores/{store_name}/coverages.json",
                )
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    raise GeoServerConnectionError(
                        f"Coverage list failed with HTTP {response.status_code}: {response.text}"
                    )

                payload = response.json()
                coverages = payload.get("coverages", {}) if isinstance(payload, dict) else {}
                coverage_items = coverages.get("coverage", []) if isinstance(coverages, dict) else []
                if isinstance(coverage_items, dict):
                    coverage_items = [coverage_items]
                if not isinstance(coverage_items, list):
                    continue

                for item in coverage_items:
                    if not isinstance(item, dict):
                        continue
                    raw_name = item.get("name", "")
                    if not raw_name:
                        continue
                    clean = raw_name.split(":", 1)[1] if ":" in raw_name else raw_name
                    detail = self.get_coverage_detail(workspace, store_name, clean)
                    layer_settings = self.get_layer_settings(workspace, clean)
                    result.append({
                        "name": clean,
                        "store_name": store_name,
                        "advertised": detail.get("advertised", True),
                        "title": detail.get("title", clean),
                        "table_name": detail.get("native_name", clean),
                        "geometry_column": "rast",
                        "geometry_type": "Point",
                        "srid": detail.get("srid", 4326),
                        "queryable": layer_settings.get("queryable", True),
                        "opaque": layer_settings.get("opaque", False),
                        "default_style_name": layer_settings.get("default_style_name", ""),
                        "additional_style_names": layer_settings.get("additional_style_names", []),
                    })
            except Exception as coverage_err:
                logger.warning(
                    "Could not list coverages for store %s/%s: %s",
                    workspace,
                    store_name,
                    coverage_err,
                )
        return result

    def get_coverage_detail(self, workspace: str, store_name: str, coverage_name: str) -> dict:
        """Fetch normalized metadata for a GeoServer coverage."""
        response = self._request(
            "get",
            f"/rest/workspaces/{workspace}/coveragestores/{store_name}/coverages/{coverage_name}.json",
        )
        if response.status_code != 200:
            logger.warning(
                "get_coverage_detail: HTTP %s for %s/%s/%s — using fallback values",
                response.status_code,
                workspace,
                store_name,
                coverage_name,
            )
            return {
                "title": coverage_name,
                "native_name": coverage_name,
                "advertised": True,
                "srid": 4326,
            }

        payload = response.json()
        coverage = payload.get("coverage", {}) if isinstance(payload, dict) else {}
        srs = coverage.get("srs", "") or coverage.get("nativeCRS", "")
        srid = 4326
        if isinstance(srs, str) and "EPSG:" in srs.upper():
            try:
                srid = int(srs.upper().rsplit("EPSG:", 1)[1].split()[0])
            except (ValueError, IndexError):
                srid = 4326
        return {
            "title": coverage.get("title") or coverage_name,
            "native_name": coverage.get("nativeName") or coverage_name,
            "advertised": coverage.get("advertised", True),
            "srid": srid,
        }

    def get_styles(self, workspace: Optional[str] = None) -> list:
        """Return GeoServer styles normalized as {name, href, workspace} records."""
        try:
            response = self._client.get_styles(workspace)
            styles_payload = response.get('styles', {}) if isinstance(response, dict) else {}
            style_items = styles_payload.get('style', []) if isinstance(styles_payload, dict) else []
            if isinstance(style_items, dict):
                style_items = [style_items]
            if not isinstance(style_items, list):
                style_items = []
            return [
                {
                    'name': item.get('name', ''),
                    'href': item.get('href', ''),
                    'workspace': workspace,
                }
                for item in style_items
                if isinstance(item, dict) and item.get('name')
            ]
        except GeoServerConnectionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get styles from GeoServer workspace={workspace!r}: {e}")
            raise GeoServerConnectionError(
                f"Failed to get styles for workspace '{workspace or 'global'}' from GeoServer at {self.url}: {e}"
            )

    def upload_style(
        self,
        *,
        name: str,
        content: str,
        style_format: str,
        workspace: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict:
        """Upload style content to GeoServer and verify the style exists."""
        try:
            if style_format == "sld":
                if overwrite:
                    self.delete_style(name=name, workspace=workspace, ignore_missing=True)
                status_code = self._client.upload_style(content, name=name, workspace=workspace)
                if status_code != 200:
                    raise GeoServerPublishError(f"SLD upload returned status {status_code}")
            elif style_format == "mbstyle":
                self._upload_mbstyle(
                    name=name,
                    content=content,
                    workspace=workspace,
                    overwrite=overwrite,
                )
            else:
                raise GeoServerPublishError(f"Unsupported style format: {style_format}")

            remote = self.get_style(name=name, workspace=workspace)
            return {
                "success": True,
                "name": name,
                "workspace": workspace,
                "remote": remote,
                "message": f"Style '{name}' uploaded and verified.",
            }
        except Exception as exc:
            logger.error("upload_style failed for %s/%s: %s", workspace or "global", name, exc)
            return {
                "success": False,
                "name": name,
                "workspace": workspace,
                "error": str(exc),
                "message": f"Style '{name}' upload failed: {exc}",
            }

    def get_style(self, *, name: str, workspace: Optional[str] = None) -> Optional[Dict]:
        try:
            return self._client.get_style(name, workspace=workspace)
        except Exception:
            return None

    def get_style_content(self, *, name: str, workspace: Optional[str] = None) -> Optional[Dict]:
        """
        Fetch raw style content from GeoServer and normalize it for local DB storage.
        Preference order:
        1. MBStyle raw endpoint
        2. SLD raw endpoint
        """
        candidates = [
            (
                self._style_path(name=name, workspace=workspace, extension="mbstyle"),
                {"Accept": "application/vnd.geoserver.mbstyle+json, application/json"},
                "mbstyle",
                f"{name}.mbstyle",
            ),
            (
                self._style_path(name=name, workspace=workspace, extension="sld"),
                {"Accept": "application/vnd.ogc.sld+xml, application/xml, text/xml"},
                "sld",
                f"{name}.sld",
            ),
        ]
        for path, headers, style_format, file_name in candidates:
            try:
                response = self._request("get", path, headers=headers)
            except Exception:
                continue
            if response.status_code != 200:
                continue
            content = response.text or ""
            if not content.strip():
                continue
            return {
                "content": content,
                "format": style_format,
                "file_name": file_name,
            }
        return None

    def delete_style(
        self,
        *,
        name: str,
        workspace: Optional[str] = None,
        ignore_missing: bool = False,
    ) -> Dict:
        try:
            result = self._client.delete_style(style_name=name, workspace=workspace)
            return {"success": True, "result": result}
        except Exception as exc:
            if ignore_missing or self._is_not_found_error(exc):
                return {"success": True, "already_deleted": True}
            return {"success": False, "error": str(exc)}

    def _upload_mbstyle(
        self,
        *,
        name: str,
        content: str,
        workspace: Optional[str],
        overwrite: bool,
    ) -> None:
        if overwrite:
            self.delete_style(name=name, workspace=workspace, ignore_missing=True)
        base_url = self.url.rstrip('/')
        if workspace:
            url = f"{base_url}/rest/workspaces/{workspace}/styles"
        else:
            url = f"{base_url}/rest/styles"

        metadata = f"<style><name>{name}</name><filename>{name}.mbstyle</filename></style>"
        response = self._client._requests(
            "post",
            url,
            data=metadata,
            headers={"content-type": "text/xml"},
        )
        if response.status_code not in {200, 201, 409}:
            raise GeoServerPublishError(
                f"MBStyle metadata create returned HTTP {response.status_code}: {response.content}"
            )
        put_response = self._client._requests(
            "put",
            f"{url}/{name}",
            data=content,
            headers={"content-type": "application/vnd.geoserver.mbstyle+json"},
        )
        if put_response.status_code not in {200, 201}:
            raise GeoServerPublishError(
                f"MBStyle content upload returned HTTP {put_response.status_code}: {put_response.content}"
            )

    @staticmethod
    def _style_path(*, name: str, workspace: Optional[str], extension: str) -> str:
        if workspace:
            return f"/rest/workspaces/{workspace}/styles/{name}.{extension}"
        return f"/rest/styles/{name}.{extension}"

    @staticmethod
    def _as_bool(value, *, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'true', '1', 'yes'}
        return bool(value)

    def get_featuretype_detail(self, workspace: str, store_name: str, ft_name: str) -> Dict:
        """
        Fetch detail for a single featuretype from GeoServer.

        GET /rest/workspaces/{ws}/datastores/{store}/featuretypes/{name}.json
        Returns normalized detail dict.
        Defaults advertised=True and native_name=name on any error.
        """
        url = "{}/rest/workspaces/{}/datastores/{}/featuretypes/{}.json".format(
            self.url.rstrip('/'), workspace, store_name, ft_name
        )
        try:
            r = self._client._requests('get', url)
            if r.status_code != 200:
                logger.warning(
                    'get_featuretype_detail: HTTP %s for %s/%s/%s — using fallback values',
                    r.status_code, workspace, store_name, ft_name,
                )
                return {
                    'name': ft_name,
                    'title': ft_name,
                    'native_name': ft_name,
                    'abstract': '',
                    'advertised': True,
                }
            data = r.json()
            featuretype = data.get('featureType', {}) if isinstance(data, dict) else {}
            return {
                'name': featuretype.get('name', ft_name),
                'title': featuretype.get('title', featuretype.get('name', ft_name)),
                'native_name': featuretype.get('nativeName', featuretype.get('name', ft_name)),
                'abstract': featuretype.get('abstract', '') or '',
                'advertised': bool(featuretype.get('advertised', True)),
            }
        except Exception as exc:
            logger.warning(
                'get_featuretype_detail: error for %s/%s/%s: %s — using fallback values',
                workspace, store_name, ft_name, exc,
            )
            return {
                'name': ft_name,
                'title': ft_name,
                'native_name': ft_name,
                'abstract': '',
                'advertised': True,
            }

    def verify_featuretype_metadata(
        self,
        workspace: str,
        store_name: str,
        featuretype_name: str,
        expected_title: Optional[str] = None,
        expected_abstract: Optional[str] = None,
    ) -> Dict:
        """
        Verify metadata fields for a featuretype after a remote update.

        Returns:
            {
                'verified': bool,
                'mismatches': {...},
                'actual': {...},
            }
        """
        actual = self.get_featuretype_detail(workspace, store_name, featuretype_name)
        mismatches = {}

        if expected_title is not None and actual.get('title', '') != expected_title:
            mismatches['title'] = {
                'expected': expected_title,
                'actual': actual.get('title', ''),
            }

        if expected_abstract is not None and (actual.get('abstract', '') or '') != (expected_abstract or ''):
            mismatches['abstract'] = {
                'expected': expected_abstract or '',
                'actual': actual.get('abstract', '') or '',
            }

        return {
            'verified': not mismatches,
            'mismatches': mismatches,
            'actual': actual,
        }

    def validate_connection(self) -> Dict:
        """
        Verify that GeoServer is reachable and responding correctly.
        Uses /rest/about/version.json which raises a real exception on failure
        (unlike get_workspaces which silently returns []).

        Returns:
            Dict with success=True and version string on success.
        Raises:
            GeoServerConnectionError on any network or HTTP error.
        """
        try:
            result = self._client.get_version()
            # result is {'about': {'resource': [{'@name': 'GeoServer', 'Version': '2.x.x', ...}]}}
            version = None
            try:
                resources = result.get('about', {}).get('resource', [])
                if isinstance(resources, dict):
                    resources = [resources]
                for r in resources:
                    if r.get('@name') == 'GeoServer':
                        version = r.get('Version')
                        break
            except Exception:
                pass
            return {'success': True, 'version': version, 'message': 'Connection validated'}
        except Exception as e:
            logger.error(f"GeoServer validate_connection failed for {self.url}: {e}")
            raise GeoServerConnectionError(f"GeoServer unreachable at {self.url}: {e}")

    # Shared helpers

    def validate_response(self, response, operation: str) -> Dict:
        """
        Validate GeoServer REST response

        Args:
            response: GeoServer response
            operation: Operation name for logging

        Returns:
            Validated response dict
        """
        try:
            if response is None:
                raise GeoServerPublishError(f"{operation} returned None response")

            # If response is already a dict with success info, return it directly.
            if isinstance(response, dict) and 'success' in response:
                return response

            # If response is True (common for geoserver-rest), assume success.
            if response is True:
                return {
                    'success': True,
                    'message': f'{operation} completed successfully',
                    'validated': True,
                }

            # For string/other formats, wrap into normalized success response.
            return {
                'success': True,
                'message': str(response),
                'raw_response': response,
                'validated': True,
            }

        except Exception as e:
            logger.error(f"Response validation failed for {operation}: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'{operation} validation failed',
                'validated': False,
            }
