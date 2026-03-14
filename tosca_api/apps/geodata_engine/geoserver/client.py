"""
Thin, controlled wrapper around geoserver-rest
"""
import sys
import os
import logging
from typing import Dict, Optional

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
        try:
            self._client = GeoServerRestClient(url, username=username, password=password)
            logger.info(f"GeoServer client initialized for {url}")
        except Exception as e:
            logger.error(f"Failed to initialize GeoServer client: {e}")
            raise GeoServerConnectionError(f"Failed to connect to GeoServer: {e}")

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

        try:
            # 1) PRE-CHECK: Does the workspace already exist?
            if self.workspace_exists(name):
                logger.info(f"Workspace {name} already exists (pre-check)")
                return {
                    'success': True,
                    'workspace': name,
                    'message': f"Workspace '{name}' already exists",
                    'created': False,
                    'pre_existed': True,
                }

            # 2) OPERATION: Create workspace in GeoServer.
            logger.info(f"Creating workspace in GeoServer: {name}")
            raw_result = self._client.create_workspace(workspace=name)

            # 3) RESPONSE VALIDATION: Validate GeoServer response.
            validated_result = self.validate_response(raw_result, f"create_workspace({name})")

            if not validated_result.get('success', False):
                raise GeoServerPublishError(
                    f"Workspace creation failed validation: {validated_result.get('message')}"
                )

            # 4) POST-CHECK: Verify workspace creation.
            verification = self.post_verify_workspace(name, expected_exists=True)

            if not verification.get('verified', False):
                logger.error(f"Post-verification failed for workspace {name}: {verification['message']}")
                # Rollback logic can be added here.

            # 5) FINAL RESPONSE: Return aggregated result details.
            final_result = {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' created successfully",
                'created': True,
                'pre_existed': False,
                'validated': validated_result.get('validated', False),
                'verified': verification.get('verified', False),
                'geoserver_response': validated_result,
            }

            logger.info(f"Workspace creation completed: {name} (verified: {verification.get('verified')})")
            return final_result

        except Exception as e:
            logger.error(f"Failed to create workspace {name}: {e}")
            # ERROR RECOVERY: Return recovery-needed signal.
            return {
                'success': False,
                'workspace': name,
                'error': str(e),
                'message': f"Failed to create workspace '{name}': {e}",
                'created': False,
                'recovery_needed': True,
            }

    def delete_workspace(self, name: str) -> Dict:
        """
        Delete workspace from GeoServer

        Args:
            name: Workspace name

        Returns:
            Dict with success status and details
        """
        logger.info(f"Deleting workspace from GeoServer: {name}")

        try:
            # Check if workspace exists before deletion.
            if not self.workspace_exists(name):
                logger.info(f"Workspace {name} does not exist, nothing to delete")
                return {
                    'success': True,
                    'workspace': name,
                    'message': f"Workspace '{name}' does not exist",
                    'deleted': False,
                    'already_deleted': True,
                }

            # Delete workspace from GeoServer.
            result = self._client.delete_workspace(workspace=name)

            # Validate response.
            validated_result = self.validate_response(result, f"delete_workspace({name})")

            if not validated_result.get('success', False):
                raise GeoServerPublishError(
                    f"Workspace deletion failed validation: {validated_result.get('message')}"
                )

            # Post-check: verify deletion.
            if self.workspace_exists(name):
                logger.warning(f"Workspace {name} still exists after deletion attempt")

            logger.info(f"Workspace deleted successfully: {name}")
            return {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' deleted successfully",
                'deleted': True,
                'geoserver_response': validated_result,
            }

        except Exception as e:
            logger.error(f"Failed to delete workspace {name}: {e}")
            return {
                'success': False,
                'workspace': name,
                'error': str(e),
                'message': f"Failed to delete workspace '{name}': {e}",
                'deleted': False,
            }

    def workspace_exists(self, workspace: str) -> bool:
        """
        Check if workspace exists in GeoServer.

        Args:
            workspace: Workspace name

        Returns:
            True if workspace exists
        """
        try:
            workspaces = self.get_workspaces()
            return workspace in workspaces
        except Exception as e:
            logger.warning(f"Failed to check workspace {workspace}: {e}")
            return False

    def get_workspaces(self) -> list:
        """
        Get list of workspace names from GeoServer.
        Raises GeoServerConnectionError on any network or HTTP failure.
        """
        try:
            workspaces = self._client.get_workspaces()
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
            actual_exists = self.workspace_exists(workspace_name)

            if actual_exists == expected_exists:
                return {
                    'success': True,
                    'verified': True,
                    'workspace': workspace_name,
                    'exists': actual_exists,
                    'message': f'Workspace verification passed: exists={actual_exists}',
                }
            return {
                'success': False,
                'verified': False,
                'workspace': workspace_name,
                'exists': actual_exists,
                'expected': expected_exists,
                'message': f'Workspace verification failed: expected={expected_exists}, actual={actual_exists}',
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
        Create PostGIS datastore in GeoServer

        Args:
            name: Store name
            workspace: Workspace name
            host: PostGIS host
            port: PostGIS port
            database: PostGIS database name
            username: PostGIS username
            password: PostGIS password
            schema: PostGIS schema (default: 'public')

        Returns:
            Dict with success status and details
        """
        try:
            self._client.create_featurestore(
                store_name=name,
                workspace=workspace,
                db=database,
                host=host,
                port=port,
                schema=schema,
                pg_user=username,
                pg_password=password,
            )
            logger.info(f"Created PostGIS store: {workspace}/{name}")
            return {
                'success': True,
                'store': name,
                'workspace': workspace,
                'message': f"PostGIS store '{name}' created successfully in workspace '{workspace}'",
            }
        except Exception as e:
            logger.error(f"Failed to create PostGIS store {workspace}/{name}: {e}")
            raise GeoServerPublishError(f"Failed to create PostGIS store '{name}': {e}")

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
        Get list of datastores from GeoServer workspace with full connection details.
        Calls individual store detail endpoint for each store so the returned dicts
        contain real host/port/database/username/schema values suitable for
        directly populating Django Store model fields.

        Args:
            workspace: Workspace name

        Returns:
            List of normalised datastore dicts:
              name, host, port, database, username, schema, store_type
        """
        try:
            stores_resp = self._client.get_datastores(workspace)
            logger.info(f"GeoServer datastores list for {workspace}: {stores_resp}")

            if not stores_resp or 'dataStores' not in stores_resp:
                return []

            store_data = stores_resp['dataStores']
            if store_data == '' or store_data is None:
                return []

            store_list = store_data.get('dataStore', [])
            if isinstance(store_list, dict):
                store_list = [store_list]

            result = []
            for ds in store_list:
                if not isinstance(ds, dict):
                    continue
                name = ds.get('name', '')
                if not name:
                    continue
                detail = self.get_datastore_detail(workspace, name)
                result.append(detail)
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

            try:
                port = int(params.get('port', 5432))
            except (ValueError, TypeError):
                port = 5432

            return {
                'name': ds.get('name', store_name),
                'host': params.get('host', ''),
                'port': port,
                'database': params.get('database', ''),
                'username': params.get('user', ''),
                'schema': params.get('schema', 'public'),
                'store_type': store_type,
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
            }

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

        try:
            # 1) PRE-CHECK: Does the store already exist?
            pre_check = self.pre_check_store(workspace, store_name)
            if pre_check.get('exists', False):
                return {
                    'success': True,
                    'store': store_name,
                    'workspace': workspace,
                    'message': f"Store '{store_name}' already exists",
                    'created': False,
                    'pre_existed': True,
                }

            # 2) OPERATION: Create datastore in GeoServer.
            logger.info(f"Creating PostGIS store in GeoServer: {store_name}")
            raw_result = self._client.create_datastore(
                store_name,
                workspace=workspace,
                host=store_data['host'],
                port=store_data['port'],
                database=store_data['database'],
                user=store_data['user'],
                passwd=store_data['passwd'],
                schema=store_data.get('schema', 'public'),
            )

            # 3) RESPONSE VALIDATION.
            validated_result = self.validate_response(raw_result, f"create_datastore({store_name})")

            if not validated_result.get('success', False):
                raise GeoServerPublishError(
                    f"Store creation failed validation: {validated_result.get('message')}"
                )

            # 4) POST-CHECK: Verify datastore creation.
            verification = self.post_verify_store(workspace, store_name, expected_exists=True)

            # 5) FINAL RESPONSE.
            final_result = {
                'success': True,
                'store': store_name,
                'workspace': workspace,
                'message': f"Store '{store_name}' created successfully",
                'created': True,
                'pre_existed': False,
                'validated': validated_result.get('validated', False),
                'verified': verification.get('verified', False),
                'geoserver_response': validated_result,
            }

            logger.info(f"Store creation completed: {store_name} (verified: {verification.get('verified')})")
            return final_result

        except Exception as e:
            logger.error(f"Failed to create store '{store_name}': {e}")
            return {
                'success': False,
                'store': store_name,
                'workspace': workspace,
                'error': str(e),
                'message': f"Store creation failed: {e}",
            }

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

        try:
            # Check if store exists before deletion.
            pre_check = self.pre_check_store(workspace, store)
            if not pre_check.get('exists', False):
                logger.info(f"Store {store} does not exist in workspace {workspace}, nothing to delete")
                return {
                    'success': True,
                    'store': store,
                    'workspace': workspace,
                    'message': f"Store '{store}' does not exist",
                    'deleted': False,
                    'already_deleted': True,
                }

            # Delete datastore from GeoServer.
            result = self._client.delete_datastore(datastore=store, workspace=workspace)

            # Validate response.
            validated_result = self.validate_response(result, f"delete_datastore({store})")

            if not validated_result.get('success', False):
                raise GeoServerPublishError(
                    f"Store deletion failed validation: {validated_result.get('message')}"
                )

            # Post-check: verify deletion.
            verification = self.post_verify_store(workspace, store, expected_exists=False)

            logger.info(f"Store deleted successfully: {store} from workspace {workspace}")
            return {
                'success': True,
                'store': store,
                'workspace': workspace,
                'message': f"Store '{store}' deleted successfully",
                'deleted': True,
                'verified': verification.get('verified', False),
                'geoserver_response': validated_result,
            }

        except Exception as e:
            logger.error(f"Failed to delete store '{store}' from workspace '{workspace}': {e}")
            return {
                'success': False,
                'store': store,
                'workspace': workspace,
                'error': str(e),
                'message': f"Failed to delete store '{store}': {e}",
                'deleted': False,
            }

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

    def post_verify_store(self, workspace: str, store_name: str, expected_exists: bool = True) -> Dict:
        """Verify store creation/deletion."""
        try:
            stores = self._client.get_datastores(workspace)
            actual_exists = store_name in [
                store['name'] for store in stores.get('dataStores', {}).get('dataStore', [])
            ]

            if (expected_exists and actual_exists) or (not expected_exists and not actual_exists):
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

        try:
            self._client.create_featurestore(
                store_name=store_name,
                workspace=workspace,
                pg_table=pg_table,
                srid=srid,
                geometry_type=geometry_type,
            )

            # Generate service URLs.
            base_url = self.url.rstrip('/')
            wfs_url = (
                f"{base_url}/ows?service=WFS&version=1.0.0&request=GetFeature"
                f"&typeName={workspace}:{layer_name}"
            )
            wms_url = (
                f"{base_url}/{workspace}/wms?service=WMS&version=1.1.0&request=GetMap"
                f"&layers={workspace}:{layer_name}"
            )

            logger.info(f"Published layer: {workspace}/{layer_name}")
            return {
                'success': True,
                'workspace': workspace,
                'layer': layer_name,
                'store': store_name,
                'table': pg_table,
                'wfs_url': wfs_url,
                'wms_url': wms_url,
                'message': f"Layer '{layer_name}' published successfully in workspace '{workspace}'",
            }
        except Exception as e:
            logger.error(f"Failed to publish layer {workspace}/{layer_name}: {e}")
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
            logger.error(f"Failed to delete layer {workspace}/{layer_name}: {e}")
            raise GeoServerPublishError(f"Failed to delete layer '{layer_name}': {e}")

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
                                result.append({'name': clean, 'store_name': store_name})
                                logger.debug(
                                    f"Layer discovered: {workspace}/{store_name}/{clean}"
                                )
                        except Exception as ft_err:
                            logger.warning(
                                f"Could not list featuretypes for store "
                                f"{workspace}/{store_name}: {ft_err}"
                            )

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
