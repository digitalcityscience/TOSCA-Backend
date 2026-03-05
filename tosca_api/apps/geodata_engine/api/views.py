import os

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..engine_factory import EngineClientFactory
from ..models import GeodataEngine, Layer, Store, Workspace
from .serializers import GeodataEngineSerializer, LayerSerializer, StoreSerializer, WorkspaceSerializer


class GeodataEngineViewSet(viewsets.ModelViewSet):
    queryset = GeodataEngine.objects.all()
    serializer_class = GeodataEngineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def sync(self, request, pk=None):
        engine = get_object_or_404(GeodataEngine, pk=pk)
        sync_service = EngineClientFactory.create_sync_service(engine)
        result = sync_service.sync_all_resources(created_by=request.user)
        code = status.HTTP_200_OK if result.get('success', False) else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def sync_all(self, request):
        engines = GeodataEngine.objects.filter(is_active=True)
        results = []

        for engine in engines:
            sync_service = EngineClientFactory.create_sync_service(engine)
            engine_result = sync_service.sync_all_resources(created_by=request.user)
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
        client = EngineClientFactory.create_client(engine)
        try:
            _ = client.get_workspaces()
            return Response({'success': True, 'message': 'Connection validated'}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WorkspaceViewSet(viewsets.ModelViewSet):
    queryset = Workspace.objects.select_related('geodata_engine')
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        workspace, created = Workspace.objects.get_or_create(
            geodata_engine=data.get('geodata_engine'),
            name=data['name'],
            defaults={
                'description': data.get('description', ''),
                'created_by': request.user,
            },
        )

        if not created:
            payload = self.get_serializer(workspace).data
            return Response(
                {'workspace': payload, 'result': {'success': True, 'idempotent': True, 'message': 'Workspace already exists'}},
                status=status.HTTP_200_OK,
            )

        engine_result = {'success': True, 'message': 'Created in DB'}
        if workspace.geodata_engine:
            client = EngineClientFactory.create_client(workspace.geodata_engine)
            engine_result = client.create_workspace(workspace.name)

        payload = self.get_serializer(workspace).data
        return Response({'workspace': payload, 'result': engine_result}, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        workspace = self.get_object()
        result = {'success': True, 'message': 'Workspace deleted from DB'}

        if workspace.geodata_engine:
            client = EngineClientFactory.create_client(workspace.geodata_engine)
            result = client.delete_workspace(workspace.name)

        workspace.delete()
        return Response(result, status=status.HTTP_200_OK)


class StoreViewSet(viewsets.ModelViewSet):
    queryset = Store.objects.select_related('workspace', 'geodata_engine')
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        workspace = data['workspace']
        geodata_engine = data.get('geodata_engine') or workspace.geodata_engine

        store, created = Store.objects.get_or_create(
            workspace=workspace,
            name=data['name'],
            defaults={
                'geodata_engine': geodata_engine,
                'store_type': data.get('store_type', 'postgis'),
                'host': data.get('host', ''),
                'port': data.get('port', 5432),
                'database': data.get('database', ''),
                'username': data.get('username', ''),
                'password': data.get('password', ''),
                'schema': data.get('schema', 'public'),
                'file_path': data.get('file_path', ''),
                'charset': data.get('charset', 'UTF-8'),
                'description': data.get('description', ''),
                'created_by': request.user,
            },
        )

        if not created:
            payload = self.get_serializer(store).data
            return Response(
                {'store': payload, 'result': {'success': True, 'idempotent': True, 'message': 'Store already exists'}},
                status=status.HTTP_200_OK,
            )

        engine_result = {'success': True, 'message': 'Created in DB'}
        if geodata_engine:
            client = EngineClientFactory.create_client(geodata_engine)
            engine_result = self._create_store_in_engine(client, store)

        payload = self.get_serializer(store).data
        return Response({'store': payload, 'result': engine_result}, status=status.HTTP_201_CREATED)

    def _create_store_in_engine(self, client, store: Store):
        if store.store_type == 'postgis':
            return client.create_postgis_store(
                name=store.name,
                workspace=store.workspace.name,
                host=store.host,
                port=store.port,
                database=store.database,
                username=store.username,
                password=store.decrypted_password,
                schema=store.schema,
            )

        if store.store_type == 'file':
            ext = os.path.splitext(store.file_path)[1].lower()
            base = {'name': store.name, 'url': f'file:{store.file_path}'}
            if ext == '.gpkg':
                return client.create_geopackage_store(workspace=store.workspace.name, store_data=base)
            if ext == '.geojson':
                return client.create_geojson_store(workspace=store.workspace.name, store_data=base)
            if ext == '.shp' or os.path.isdir(store.file_path):
                payload = {**base, 'charset': store.charset}
                if os.path.isdir(store.file_path):
                    return client.create_directory_store(workspace=store.workspace.name, store_data=payload)
                return client.create_shapefile_store(workspace=store.workspace.name, store_data=payload)
            return {'success': False, 'error': f'Unsupported file type: {ext}'}

        if store.store_type == 'geotiff':
            return client.create_geotiff_store(
                workspace=store.workspace.name,
                store_data={'name': store.name, 'url': f'file:{store.file_path}'},
            )

        return {'success': False, 'error': f'Unsupported store type: {store.store_type}'}


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
        if user and user.is_authenticated:
            return qs
        return qs.filter(is_public=True)

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

    def destroy(self, request, *args, **kwargs):
        layer = self.get_object()
        _ = self._unpublish_layer(layer)
        layer.delete()
        return Response({'success': True, 'message': 'Layer deleted'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        layer = self.get_object()
        if layer.publishing_state == 'PUBLISHED':
            return Response({'success': True, 'idempotent': True, 'message': 'Layer already published'}, status=status.HTTP_200_OK)

        client = EngineClientFactory.create_client(layer.workspace.geodata_engine)
        result = client.publish_featuretype(
            store_name=layer.store.name,
            workspace=layer.workspace.name,
            pg_table=layer.table_name,
            srid=layer.srid,
            geometry_type=layer.geometry_type,
            layer_name=layer.name,
        )

        if result.get('success', True):
            Layer.objects.filter(pk=layer.pk).update(
                publishing_state='PUBLISHED',
                publishing_error='',
                published_url=result.get('wms_url', ''),
                published_at=timezone.now(),
            )
            return Response(result, status=status.HTTP_200_OK)

        Layer.objects.filter(pk=layer.pk).update(
            publishing_state='FAILED',
            publishing_error=result.get('error', result.get('message', 'Unknown publish error')),
        )
        return Response(result, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def unpublish(self, request, pk=None):
        layer = self.get_object()
        result = self._unpublish_layer(layer)
        code = status.HTTP_200_OK if result.get('success', False) else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    def _unpublish_layer(self, layer: Layer):
        if layer.publishing_state in {'DRAFT', 'UNPUBLISHED'}:
            return {'success': True, 'idempotent': True, 'message': 'Layer already unpublished'}

        client = EngineClientFactory.create_client(layer.workspace.geodata_engine)
        result = client.delete_layer(workspace=layer.workspace.name, layer_name=layer.name)

        if result.get('success', True):
            Layer.objects.filter(pk=layer.pk).update(
                publishing_state='UNPUBLISHED',
                publishing_error='',
                published_url='',
                published_at=None,
            )

        return result

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
