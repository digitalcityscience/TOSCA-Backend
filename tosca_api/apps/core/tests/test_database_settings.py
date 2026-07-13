"""
Regression tests for issue 25: DB connection pooling and statement timeout.
"""
from django.db import OperationalError, connection
from django.test import SimpleTestCase


class ConnMaxAgeSettingsTests(SimpleTestCase):
    def test_conn_max_age_is_configured(self):
        settings_dict = connection.settings_dict
        self.assertGreater(settings_dict['CONN_MAX_AGE'], 0)

    def test_conn_health_checks_enabled(self):
        # Paired with CONN_MAX_AGE: a reused connection is pinged before use
        # so a connection that died server-side surfaces as a clean retry
        # instead of a request-time error.
        self.assertTrue(connection.settings_dict['CONN_HEALTH_CHECKS'])


class StatementTimeoutTests(SimpleTestCase):
    databases = {'default'}

    def test_statement_timeout_is_configured(self):
        options = connection.settings_dict['OPTIONS'].get('options', '')
        self.assertIn('statement_timeout=', options)

    def test_statement_timeout_actually_cuts_off_a_long_query(self):
        """Proves the mechanism, not just the config string: with
        statement_timeout set, a query that runs past it is killed by
        Postgres rather than allowed to hang. Uses a short SET LOCAL
        override (rather than waiting out the real ~30s production
        default) so the test runs fast.
        """
        with self.assertRaises(OperationalError):
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = 100;")
                cursor.execute("SELECT pg_sleep(1);")

        # Connection is now aborted by Postgres — start a fresh one so
        # Django's own post-test cleanup doesn't trip over it.
        connection.close()
