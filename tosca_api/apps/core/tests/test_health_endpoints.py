from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import reverse


class HealthzTests(SimpleTestCase):
    def test_healthz_returns_200(self):
        response = self.client.get(reverse('healthz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    @patch('tosca_api.views.connections')
    def test_healthz_ignores_database_state(self, mock_connections):
        # healthz must not touch the DB at all — a broken connection mock
        # should have zero effect on its response.
        mock_connections.__getitem__.side_effect = AssertionError('healthz must not query connections')

        response = self.client.get(reverse('healthz'))

        self.assertEqual(response.status_code, 200)


class ReadyzTests(SimpleTestCase):
    databases = {'default'}  # readyz opens a real DB connection on the happy path

    def test_readyz_returns_200_when_db_reachable(self):
        response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    @patch('tosca_api.views.connections')
    def test_readyz_returns_503_when_db_unreachable(self, mock_connections):
        mock_connections['default'].ensure_connection.side_effect = OperationalError('connection refused')

        response = self.client.get(reverse('readyz'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unavailable')
