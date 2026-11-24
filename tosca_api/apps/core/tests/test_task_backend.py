"""``TOSCA_TASK_BACKEND``: default database mode, the Celery switch and its system checks."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from tosca_api.apps.core import task_backend
from tosca_api.apps.core.checks import check_task_backend
from tosca_api.apps.external_catalog import harvest, health


def test_default_is_the_database_backend():
    assert settings.TOSCA_TASK_BACKEND == "database"
    assert not task_backend.uses_celery()


def test_database_mode_starts_a_daemon_thread():
    with patch.object(task_backend.threading, "Thread") as thread:
        harvest._spawn("service-pk")
        health._spawn([1, 2])

    calls = thread.call_args_list
    assert calls[0].kwargs == {
        "target": harvest._background, "args": ("service-pk",), "name": "external-catalog-load", "daemon": True,
    }
    assert calls[1].kwargs["target"] is health._background and calls[1].kwargs["args"] == ([1, 2],)
    assert thread.return_value.start.call_count == 2


@override_settings(TOSCA_TASK_BACKEND="celery")
def test_celery_mode_runs_the_catalog_through_celery_tasks():
    pytest.importorskip("celery")
    with (
        patch.object(task_backend.threading, "Thread") as thread,
        patch.object(harvest, "run_by_pk") as run_by_pk,
        patch.object(health, "run_for_ids") as run_for_ids,
    ):
        harvest._spawn("service-pk")
        health._spawn([1, 2])

    thread.assert_not_called()
    run_by_pk.assert_called_once_with("service-pk")
    run_for_ids.assert_called_once_with([1, 2])


class TaskBackendCheckTests(SimpleTestCase):
    def ids(self):
        return [error.id for error in check_task_backend(None)]

    def test_database_mode_needs_nothing(self):
        with override_settings(TOSCA_TASK_BACKEND="database", REDIS_URL=""):
            self.assertEqual(self.ids(), [])

    def test_unknown_backend_is_an_error(self):
        with override_settings(TOSCA_TASK_BACKEND="rabbit"):
            self.assertEqual(self.ids(), ["tosca.settings.E020"])

    def test_celery_needs_redis_url(self):
        pytest.importorskip("celery")
        with override_settings(TOSCA_TASK_BACKEND="celery", REDIS_URL=""):
            self.assertEqual(self.ids(), ["tosca.settings.E022"])

    def test_celery_is_ok_with_celery_and_redis_url(self):
        pytest.importorskip("celery")
        with override_settings(TOSCA_TASK_BACKEND="celery", REDIS_URL="redis://:pw@redis:6379/0"):
            self.assertEqual(self.ids(), [])

    def test_celery_needs_the_celery_package(self):
        with (
            override_settings(TOSCA_TASK_BACKEND="celery", REDIS_URL="redis://:pw@redis:6379/0"),
            patch("importlib.util.find_spec", return_value=None),
        ):
            self.assertEqual(self.ids(), ["tosca.settings.E023", "tosca.settings.E021"])

    def test_redis_url_needs_the_redis_package(self):
        with (
            override_settings(REDIS_URL="redis://:pw@redis:6379/0"),
            patch("importlib.util.find_spec", return_value=None),
        ):
            self.assertEqual(self.ids(), ["tosca.settings.E023"])


def test_celery_settings_are_json_only_without_results():
    assert settings.CELERY_ACCEPT_CONTENT == ["json"]
    assert settings.CELERY_TASK_SERIALIZER == "json"
    assert settings.CELERY_TASK_IGNORE_RESULT is True
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert (
        settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] > settings.CELERY_TASK_TIME_LIMIT
    )
    drain = settings.CELERY_BEAT_SCHEDULE["geosim-drain"]
    poll = settings.GEOSIMULATIONS_WORKER["poll_seconds"]
    assert (drain["task"], drain["schedule"], drain["options"]["expires"]) == ("geosim.drain", poll, poll)
    assert settings.CELERY_BEAT_SCHEDULE["geosim-purge"]["schedule"] == 3600
