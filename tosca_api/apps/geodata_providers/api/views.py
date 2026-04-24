"""
API endpoints for the geodata_providers app.

Purpose:
- expose CRUD APIs for engines, workspaces, stores, and layers
- wrap GeoServer-aware operations behind Django REST endpoints
- enforce the app's sync contract: mutate remote state first when needed,
  verify the result, then persist or update Django state
- provide operational actions such as sync, connection test, PostGIS table
  inspection, layer publish, and layer unpublish

This file exists so the frontend and admin-adjacent tools can use a stable
HTTP API instead of calling GeoServer directly or duplicating orchestration
logic in multiple places.
"""

import logging

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..geoserver.client import GeoServerClient
from ..models import GeodataEngine, Layer, Store, Workspace
from ..postgis_inspector import PostGISInspectorError, get_geometry_tables, get_table_bbox
from ..services.commands.geodata_engine_service import GeodataEngineService
from ..services.commands.layer_service import LayerService
from ..services.commands.store_service import StoreService
from ..services.commands.workspace_service import WorkspaceService
from .serializers import GeodataEngineSerializer, LayerSerializer, StoreSerializer, WorkspaceSerializer

logger = logging.getLogger(__name__)


def _api_result(result: dict) -> dict:
    if not isinstance(result, dict):
        return result
    return {key: value for key, value in result.items() if key != 'resource'}


class GeodataEngineViewSet(viewsets.ModelViewSet):
    queryset = GeodataEngine.objects.all()
    serializer_class = GeodataEngineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = GeodataEngine.objects.all()
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=str(is_active).strip().lower() == 'true')
        return qs

    def perform_create(self, serializer):
        engine, sync_result = GeodataEngineService.create_engine(
            user=self.request.user,
            **serializer.validated_data,
        )
        self._last_engine = engine
        self._last_sync_result = sync_result

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except (GeoServerConnectionError, GeodataEngineError) as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        engine = self._last_engine
        payload = self.get_serializer(engine).data
        headers = self.get_success_headers(payload)
        return Response(
            {'engine': payload, 'initial_sync': self._last_sync_result},
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_update(serializer)
        except (GeoServerConnectionError, GeodataEngineError) as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        engine = self._last_engine
        return Response({'engine': self.get_serializer(engine).data, 'sync': self._last_sync_result})

    def perform_update(self, serializer):
        engine, sync_result = GeodataEngineService.update_engine(
            serializer.instance,
            user=self.request.user,
            **serializer.validated_data,
        )
        self._last_engine = engine
        self._last_sync_result = sync_result

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def sync(self, request, pk=None):
        engine = get_object_or_404(GeodataEngine, pk=pk)
        result = GeodataEngineService.sync_engine(engine, user=request.user)
        code = status.HTTP_200_OK if result.get('success', False) else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def sync_all(self, request):
        engines = GeodataEngine.objects.filter(is_active=True)
        results = []

        for engine in engines:
            engine_result = GeodataEngineService.sync_engine(engine, user=request.user)
            results.append(
                {
                    'engine': engine.name,
                    'success': engine_result.get('success', False),
                    'results': engine_result,
                }
            )

        success_count = sum(1 for item in results if item['success'])
        return Response(
            {
                'status': 'completed',
                'message': f'{success_count}/{len(results)} engines synced successfully',
                'results': results,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def validate(self, request, pk=None):
        engine = get_object_or_404(GeodataEngine, pk=pk)
        try:
            result = GeodataEngineService.validate_engine_connection(engine=engine)
            return Response(
                {'success': True, 'message': result.get('message', 'Connection validated'), 'version': result.get('version')},
                status=status.HTTP_200_OK,
            )
        except (GeoServerConnectionError, GeodataEngineError) as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='test_connection', permission_classes=[permissions.IsAuthenticated])
    def test_connection(self, request):
        """
        POST /api/v1/providers/provider/engines/test_connection/
        Stateless connection test — no saved engine required.
        Body: { base_url, admin_username, admin_password, engine_type }
        Used by the create engine form before the engine is saved.
        """
        base_url = request.data.get('base_url', '').strip()
        admin_username = request.data.get('admin_username', '').strip()
        admin_password = request.data.get('admin_password', '')
        engine_type = request.data.get('engine_type', 'geoserver')

        if not base_url:
            return Response(
                {'success': False, 'error': 'base_url is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = GeodataEngineService.validate_engine_connection(
                config={
                    'base_url': base_url,
                    'admin_username': admin_username,
                    'admin_password': admin_password,
                    'engine_type': engine_type,
                }
            )
            return Response(
                {
                    'success': True,
                    'message': result.get('message', 'Connection validated'),
                    'version': result.get('version'),
                },
                status=status.HTTP_200_OK,
            )
        except (GeoServerConnectionError, GeodataEngineError) as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def push(self, request, pk=None):
        """
        POST /api/v1/providers/provider/engines/{id}/push/
        Push Django metadata intent → GeoServer (workspaces for now).
        Sync rule: check exists → create if missing → verify → report.
        Does NOT modify Django state.
        """
        engine = get_object_or_404(GeodataEngine, pk=pk)
        from ..sync_service import GeoServerSyncService

        sync_service = GeoServerSyncService(engine)
        result = sync_service.push_all_workspaces(created_by=request.user)
        code = status.HTTP_200_OK if result.get('success', False) else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)


class WorkspaceViewSet(viewsets.ModelViewSet):
    queryset = Workspace.objects.select_related('geodata_engine')
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Workspace.objects.select_related('geodata_engine')
        engine_id = self.request.query_params.get('geodata_engine')
        if engine_id:
            qs = qs.filter(geodata_engine__id=engine_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = WorkspaceService.create_workspace(
            engine=data.get('geodata_engine'),
            name=data['name'],
            description=data.get('description', ''),
            user=request.user,
        )
        if not result.get('success'):
            return Response(_api_result(result), status=status.HTTP_400_BAD_REQUEST)

        workspace = result['resource']
        payload = self.get_serializer(workspace).data
        response_status = status.HTTP_200_OK if result.get('already_exists') else status.HTTP_201_CREATED
        return Response({'workspace': payload, 'result': _api_result(result)}, status=response_status)

    def destroy(self, request, *args, **kwargs):
        workspace = self.get_object()
        result = WorkspaceService.delete_workspace_safe(workspace)
        code = status.HTTP_200_OK if result.get('success', False) else status.HTTP_400_BAD_REQUEST
        return Response(_api_result(result), status=code)


class StoreViewSet(viewsets.ModelViewSet):
    queryset = Store.objects.select_related('workspace', 'geodata_engine')
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Store.objects.select_related('workspace', 'geodata_engine')
        engine_id = self.request.query_params.get('geodata_engine')
        workspace_id = self.request.query_params.get('workspace')
        if engine_id:
            qs = qs.filter(geodata_engine__id=engine_id)
        if workspace_id:
            qs = qs.filter(workspace__id=workspace_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        workspace = data['workspace']
        result = StoreService.create_postgis_store(
            workspace=workspace,
            name=data['name'],
            user=request.user,
            store_type=data.get('store_type', 'postgis'),
            description=data.get('description', ''),
            host=data.get('host', ''),
            port=data.get('port', 5432),
            database=data.get('database', ''),
            username=data.get('username', ''),
            password=data.get('password', ''),
            schema=data.get('schema', 'public'),
            file_path=data.get('file_path', ''),
            charset=data.get('charset', 'UTF-8'),
        )

        store = result.get('resource')
        if result.get('already_exists'):
            payload = self.get_serializer(store).data
            return Response(
                {'store': payload, 'result': _api_result(result)},
                status=status.HTTP_200_OK,
            )

        if not result.get('success'):
            return Response(_api_result(result), status=status.HTTP_400_BAD_REQUEST)

        payload = self.get_serializer(store).data
        return Response({'store': payload, 'result': _api_result(result)}, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        store = self.get_object()
        result = StoreService.delete_store_safe(store)
        if not result.get('success', False):
            return Response(
                {
                    'success': False,
                    'detail': result.get('error', result.get('message', 'Engine failed to delete the store.')),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_api_result(result), status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def test_connection(self, request, pk=None):
        """
        POST /api/v1/providers/provider/stores/{id}/test_connection/
        Verifies the store is reachable in GeoServer by fetching its detail.
        Returns success + store detail on success, error on failure.
        """
        store = self.get_object()
        if not store.workspace or not store.workspace.geodata_engine:
            return Response(
                {'success': False, 'error': 'Store has no engine attached.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        client = EngineClientFactory.create_client(store.workspace.geodata_engine)
        try:
            detail = client.get_datastore_detail(workspace=store.workspace.name, store_name=store.name)
            return Response({'success': True, 'message': 'Store reachable in GeoServer.', 'detail': detail})
        except GeoServerConnectionError as exc:
            return Response({'success': False, 'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({'success': False, 'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def postgis_tables(self, request, pk=None):
        """
        GET /api/v1/providers/provider/stores/{id}/postgis_tables/

        Returns all PostGIS tables with geometry metadata from the store's
        target database schema.  Uses SQLAlchemy (psycopg v3) — connects
        directly to the store's PostGIS DB, not Django's own database.

        Response:
            {
              "tables": [
                {"table_name": str, "geometry_column": str,
                 "geometry_type": str, "srid": int},
                ...
              ]
            }
        """
        store = self.get_object()
        if store.store_type != 'postgis':
            return Response(
                {'success': False, 'error': 'Only PostGIS stores support table inspection.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not store.host or not store.database or not store.username:
            return Response(
                {'success': False, 'error': 'Store is missing connection details (host, database, username).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        db_password = store.decrypted_password
        if not db_password:
            return Response(
                {
                    'success': False,
                    'error': (
                        'Store has no saved database password. '
                        'If this store was synced from GeoServer, Django never received the password '
                        'because GeoServer does not expose credentials via its REST API. '
                        'Edit the store and enter the PostgreSQL password to enable table inspection.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            tables = get_geometry_tables(
                host=store.host,
                port=store.port or 5432,
                database=store.database,
                username=store.username,
                password=db_password,
                schema=store.schema or 'public',
            )
            return Response({'tables': tables, 'schema': store.schema, 'store': store.name})
        except PostGISInspectorError as exc:
            return Response({'success': False, 'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error('postgis_tables error for store %s: %s', store.name, exc)
            return Response({'success': False, 'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LayerViewSet(viewsets.ModelViewSet):
    queryset = Layer.objects.select_related('workspace', 'store', 'workspace__geodata_engine')
    serializer_class = LayerSerializer

    def get_permissions(self):
        if self.action in {'list', 'retrieve'}:
            return [permissions.AllowAny()]
        if self.action in {'publish', 'unpublish', 'preview'}:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = getattr(self.request, 'user', None)
        if not (user and user.is_authenticated):
            qs = qs.filter(is_public=True)
        engine_id = self.request.query_params.get('geodata_engine')
        workspace_id = self.request.query_params.get('workspace')
        if engine_id:
            qs = qs.filter(workspace__geodata_engine__id=engine_id)
        if workspace_id:
            qs = qs.filter(workspace__id=workspace_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        layer, created = Layer.objects.get_or_create(
            workspace=data['workspace'],
            name=data['name'],
            defaults={
                'store': data['store'],
                'title': data.get('title', data['name']),
                'description': data.get('description', ''),
                'table_name': data.get('table_name', self._sanitize_table_name(data['name'])),
                'geometry_column': data.get('geometry_column', 'geom'),
                'geometry_type': data.get('geometry_type', 'Point'),
                'srid': data.get('srid', 4326),
                'is_public': data.get('is_public', False),
                'publishing_state': data.get('publishing_state', 'DRAFT'),
                'created_by': request.user,
            },
        )

        if not created:
            payload = self.get_serializer(layer).data
            return Response(
                {'layer': payload, 'result': {'success': True, 'idempotent': True, 'message': 'Layer already exists'}},
                status=status.HTTP_200_OK,
            )

        payload = self.get_serializer(layer).data
        return Response({'layer': payload, 'result': {'success': True, 'message': 'Layer created'}}, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        """
        PATCH/PUT /api/v1/providers/provider/layers/<id>/

        Allowed editable fields:
            title, description  — synced to GeoServer (if PUBLISHED) then Django
            srid                — Django-only

        All other fields (name, table_name, workspace, store, geometry_*)
        are silently ignored — they cannot be changed via this endpoint.
        """
        partial = kwargs.pop('partial', True)  # always treat as partial
        layer = self.get_object()

        # Extract only the fields we allow to be changed
        ALLOWED = {'title', 'description', 'srid'}
        incoming = {k: v for k, v in request.data.items() if k in ALLOWED}

        if not incoming:
            return Response(
                {'detail': 'No editable fields provided. Allowed: title, description, srid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if layer.publishing_state == 'PUBLISHED' and {'title', 'description'} & set(incoming):
            try:
                LayerService.update_published_metadata(
                    layer=layer,
                    title=incoming.get('title', layer.title),
                    description=incoming.get('description', layer.description),
                )
            except Exception as exc:
                logger.error(
                    'LayerViewSet.update: featuretype update failed for %s/%s: %s',
                    layer.workspace.name, layer.name, exc,
                )
                return Response(
                    {'success': False, 'error': f'GeoServer featuretype update failed: {exc}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            incoming = {key: value for key, value in incoming.items() if key not in {'title', 'description'}}
            layer.refresh_from_db()

        if incoming:
            serializer = self.get_serializer(layer, data=incoming, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)

        return Response(self.get_serializer(layer).data)

    def destroy(self, request, *args, **kwargs):
        layer = self.get_object()

        result = LayerService.delete_layer_safe(layer)
        if not result.get('success', False):
            return Response(
                {
                    'success': False,
                    'error': result.get('error', result.get('message', 'Engine unpublish failed')),
                    'detail': 'Layer was NOT deleted from Django — engine unpublish must succeed first.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'success': True, 'message': 'Layer unpublished and deleted.'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        layer = self.get_object()
        result = LayerService.publish_existing_layer(layer)
        if result.get('success', True):
            return Response(result, status=status.HTTP_200_OK)
        return Response(result, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def unpublish(self, request, pk=None):
        layer = self.get_object()
        result = LayerService.unpublish_layer(layer)
        code = status.HTTP_200_OK if result.get('success', False) else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=False, methods=['post'])
    def publish_postgis(self, request):
        """
        POST /api/v1/providers/provider/layers/publish_postgis/

        Publishes an existing PostGIS table as a GeoServer FeatureType and
        registers a Layer object in Django.  All operations follow the
        GeoServer-first sync pattern:
            1. Check exists → 2. Create in GeoServer → 3. Verify → 4. Persist Django

        Expected request body:
            {
              "store_id":        "<uuid>",
              "workspace_id":    "<uuid>",
              "table_name":      "buildings",
              "layer_name":      "buildings",          # user-defined, defaults to table_name
              "geometry_column": "geom",
              "geometry_type":   "Polygon",
              "srid":            4326,
              "title":           "Buildings",          # optional
              "description":     "...",               # optional
              "advertised":      false,               # default false per spec
            }

        Returns:
            {"layer": <LayerSerializer>, "result": {"success": True, ...}}
        """
        data = request.data
        store_id = data.get('store_id')
        workspace_id = data.get('workspace_id')
        table_name = data.get('table_name', '').strip()
        layer_name = data.get('layer_name', table_name).strip()

        if not store_id or not workspace_id or not table_name or not layer_name:
            return Response(
                {'success': False, 'error': 'store_id, workspace_id, table_name, and layer_name are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        store = get_object_or_404(Store, pk=store_id)
        workspace = get_object_or_404(Workspace, pk=workspace_id)

        if store.workspace != workspace:
            return Response(
                {'success': False, 'error': 'Store does not belong to the selected workspace.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        geometry_column = data.get('geometry_column', 'geom')
        geometry_type = data.get('geometry_type', 'Point')
        srid = int(data.get('srid', 4326))
        title = data.get('title', layer_name)
        description = data.get('description', '')

        try:
            result = LayerService.publish_postgis(
                workspace=workspace,
                store=store,
                table_name=table_name,
                layer_name=layer_name,
                title=title,
                description=description,
                geometry_column=geometry_column,
                geometry_type=geometry_type,
                srid=srid,
                user=request.user,
            )
        except Exception as exc:
            logger.error('publish_postgis failed for %s/%s: %s', workspace.name, layer_name, exc)
            return Response(
                {'success': False, 'error': f'GeoServer publish failed: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not result.get('success'):
            code = status.HTTP_409_CONFLICT if result.get('error_code') == 'LAYER_ALREADY_EXISTS' else status.HTTP_400_BAD_REQUEST
            return Response(
                {
                    'success': False,
                    'error': result.get('error', result.get('message', 'Layer publish failed')),
                    'error_code': result.get('error_code'),
                },
                status=code,
            )

        layer = result['resource']
        payload = self.get_serializer(layer).data
        return Response(
            {
                'layer': payload,
                'result': {
                    'success': True,
                    'created': result.get('created', False),
                    'message': result.get('message'),
                    'bbox': result.get('bbox'),
                },
            },
            status=status.HTTP_201_CREATED if result.get('created') else status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'])
    def preview(self, request):
        payload = dict(request.data)
        file_name = payload.get('file_name', '')
        ext = os.path.splitext(file_name)[1].lower() if file_name else ''
        result = {
            'success': True,
            'preview': {
                'file_name': file_name,
                'detected_type': ext.replace('.', '') or 'unknown',
                'supported': ext in {'.shp', '.geojson', '.gpkg', '.tif', '.tiff'},
                'message': 'POC preview only returns detected file metadata',
            },
        }
        return Response(result, status=status.HTTP_200_OK)

    @staticmethod
    def _sanitize_table_name(name: str):
        import re

        sanitized = re.sub(r'[^a-z0-9_]', '_', name.lower())
        if sanitized and sanitized[0].isdigit():
            sanitized = f'layer_{sanitized}'
        return sanitized or 'layer'
