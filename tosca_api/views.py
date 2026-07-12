import logging

from django.db import Error as DatabaseError
from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def base(request):
    return render(request, 'base.html')


def healthz(request):
    """
    Process liveness — 200 whenever Django itself can handle a request.
    Deliberately checks nothing else (no DB, no GeoServer): a dependency
    outage should surface via /readyz, not make the process look dead.
    """
    return JsonResponse({'status': 'ok'})


def readyz(request):
    """
    Readiness — DB connectivity only. Kept shallow and fast on purpose:
    no GeoServer sync or other slow dependency check belongs here.
    """
    try:
        connections['default'].ensure_connection()
    except DatabaseError as exc:
        logger.error('readyz: database unreachable: %s', exc)
        return JsonResponse({'status': 'unavailable', 'error': 'database unreachable'}, status=503)
    return JsonResponse({'status': 'ok'})
