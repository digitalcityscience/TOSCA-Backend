"""Process GeoSimulations jobs: dispatch started runs to their runner, import finished packages.

Also applies run retention once an hour (``purge_simulation_runs``).

Runs until stopped (SIGTERM/SIGINT finish the current job first). This is the
database-queue worker; a Celery + Redis worker is planned to replace the loop,
keeping the job functions in ``geosimulations.jobs``.
"""

import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from tosca_api.apps.geosimulations import jobs, retention

PURGE_EVERY_SECONDS = 3600


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process the jobs that are due, then exit")
        parser.add_argument("--worker-id", help="Name recorded on claimed jobs (default: host name + random)")

    def handle(self, *args, **options):
        worker_id = options["worker_id"] or jobs.default_worker_id()
        poll_seconds = settings.GEOSIMULATIONS_WORKER["poll_seconds"]
        self._stopping = False
        last_purge = 0.0
        if not options["once"]:
            signal.signal(signal.SIGTERM, self._stop)
            signal.signal(signal.SIGINT, self._stop)
            self.stdout.write(f"Simulation worker {worker_id} polling every {poll_seconds}s.")
        while not self._stopping:
            # Drop broken or expired connections between jobs (a long-lived process);
            # not inside a transaction, e.g. when tests call the command.
            if not connection.in_atomic_block:
                close_old_connections()
            if not options["once"] and time.monotonic() - last_purge > PURGE_EVERY_SECONDS:
                last_purge = time.monotonic()
                purged = retention.purge()
                if purged.deleted_runs or purged.removed_uploads:
                    self.stdout.write(
                        f"Retention: deleted {len(purged.deleted_runs)} run(s), "
                        f"{purged.removed_uploads} upload(s)."
                    )
            failed = jobs.fail_stale_jobs()
            if failed:
                self.stdout.write(f"Failed {failed} job(s) that exceeded their runner timeout.")
            job = jobs.claim_next_job(worker_id)
            if job is not None:
                self.stdout.write(f"{job.pk}: {job.state} ({job.run.key})")
                jobs.process_job(job)
                job.refresh_from_db()
                self.stdout.write(f"{job.pk}: → {job.state}")
                continue
            if options["once"]:
                break
            time.sleep(poll_seconds)
        self.stdout.write("Simulation worker stopped.")

    def _stop(self, signum, frame):
        self._stopping = True
