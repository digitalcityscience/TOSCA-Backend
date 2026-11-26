"""Prepare object storage for this deployment. Safe to run on every start.

Creates the media buckets that do not exist yet, using the application's own
S3 key, and then applies the browser CORS rules. Against Garage this needs a
key that may create buckets, which is what ``garage server --single-node
--default-access-key`` configures; a bucket created through the S3 API is owned
by the key that created it. No Garage admin token is used.

With filesystem storage there is nothing to prepare and the command exits 0.
"""
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

ALIASES = ("default", "media_public", "media_archive")
MISSING = {"404", "NoSuchBucket", "NotFound"}
ALREADY_OURS = {"BucketAlreadyOwnedByYou"}


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--skip-cors", action="store_true", help="Only create buckets.")

    def handle(self, *args, **options):
        buckets = 0
        for alias in ALIASES:
            if alias not in settings.STORAGES:
                continue
            storage = storages[alias]
            bucket = getattr(storage, "bucket_name", None)
            if not bucket:
                self.stdout.write(f"{alias}: not an S3 bucket, skipped")
                continue
            buckets += 1
            client = storage.connection.meta.client
            try:
                client.head_bucket(Bucket=bucket)
                self.stdout.write(f"{alias} ({bucket}): exists")
                continue
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code not in MISSING:
                    # 403 here means the bucket exists but this key has no
                    # access to it: creating it again would not help.
                    raise CommandError(f"{alias} ({bucket}): cannot access bucket: {exc}") from exc
            try:
                client.create_bucket(Bucket=bucket)
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ALREADY_OURS:
                    raise CommandError(f"{alias} ({bucket}): cannot create bucket: {exc}") from exc
            self.stdout.write(f"{alias} ({bucket}): created")

        if not buckets:
            self.stdout.write("No S3 buckets configured; nothing to do.")
            return
        if options["skip_cors"]:
            return
        if not settings.CORS_ALLOWED_ORIGINS:
            self.stdout.write(self.style.WARNING("DJANGO_CORS_ALLOWED_ORIGINS is empty; storage CORS not configured."))
            return
        call_command("configure_storage_cors", stdout=self.stdout)
