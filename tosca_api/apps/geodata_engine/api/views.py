from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404

from ..models import GeodataEngine, Workspace, Store, Layer
from ..sync_service import GeoServerSyncService
from .serializers import (
    GeodataEngineSerializer, WorkspaceSerializer, 
    StoreSerializer, LayerSerializer
)

import logging

logger = logging.getLogger(__name__)


class GeodataEngineViewSet(viewsets.ModelViewSet):
    """API ViewSet for managing GeodataEngine instances"""
    
    queryset = GeodataEngine.objects.all()
    serializer_class = GeodataEngineSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        """Set created_by field to current user"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """Sync specific engine with GeoServer"""
        engine = get_object_or_404(GeodataEngine, pk=pk)
        
        try:
            # Use existing sync service
            sync_service = GeoServerSyncService(engine)
            results = sync_service.sync_all_resources(request.user)
            
            if results.get('success', False):
                return Response({
                    'status': 'success',
                    'message': 'Engine synced successfully',
                    'results': results
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    'status': 'error', 
                    'message': 'Sync failed',
                    'results': results
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"Engine sync failed for {engine.name}: {e}")
            return Response({
                'status': 'error',
                'message': f'Sync failed: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'])
    def sync_all(self, request):
        """Sync all active engines"""
        engines = GeodataEngine.objects.filter(is_active=True)
        results = []
        
        for engine in engines:
            try:
                sync_service = GeoServerSyncService(engine)
                engine_results = sync_service.sync_all_resources(request.user)
                results.append({
                    'engine': engine.name,
                    'success': engine_results.get('success', False),
                    'results': engine_results
                })
            except Exception as e:
                logger.error(f"Engine sync failed for {engine.name}: {e}")
                results.append({
                    'engine': engine.name,
                    'success': False,
                    'error': str(e)
                })
        
        success_count = sum(1 for r in results if r['success'])
        return Response({
            'status': 'completed',
            'message': f'{success_count}/{len(results)} engines synced successfully',
            'results': results
        }, status=status.HTTP_200_OK)


class WorkspaceViewSet(viewsets.ModelViewSet):
    """API ViewSet for Workspace instances"""
    
    queryset = Workspace.objects.select_related('geodata_engine')
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]


class StoreViewSet(viewsets.ModelViewSet):
    """API ViewSet for Store instances"""
    
    queryset = Store.objects.select_related('workspace')
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated]


class LayerViewSet(viewsets.ModelViewSet):
    """API ViewSet for Layer instances"""
    
    queryset = Layer.objects.select_related('store')
    serializer_class = LayerSerializer
    permission_classes = [permissions.IsAuthenticated]