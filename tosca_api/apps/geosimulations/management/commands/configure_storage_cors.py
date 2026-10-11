"""Allow the frontend to fetch() presigned files from object storage.

Map tiles (PMTiles, read with HTTP range requests) and replays are loaded with
fetch() from the frontend's origin, so every media bucket needs a CORS rule.
TOSCA's rules are merged by id prefix: other rules on the bucket are kept, and
running the command again replaces only TOSCA's rules. Run it once per
environment, and again when the allowed origins change.
"""

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

RULE_ID = "tosca-browser-reads"  # one rule per origin: RULE_ID-1, RULE_ID-2, ...
ALIASES = ("default", "media_public", "media_archive")


def tosca_rule(origin: str, number: int) -> dict:
    return {
        "ID": f"{RULE_ID}-{number}",
        # One origin per rule: with several origins in one rule, Garage echoes all of
        # them in a single Access-Control-Allow-Origin header, which browsers reject.
        "AllowedOrigins": [origin],
        "AllowedMethods": ["GET", "HEAD"],
        # "*" rather than a list: browsers send header names in lower case and
        # Garage compares them case-sensitively ("range" would not match "Range").
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["Content-Range", "Content-Length", "Accept-Ranges", "ETag", "Content-Encoding"],
        "MaxAgeSeconds": 3600,
    }


def is_tosca_rule(rule: dict) -> bool:
    return str(rule.get("ID", "")).startswith(RULE_ID)


def merged_rules(existing: list[dict], origins: list[str]) -> list[dict]:
    others = [rule for rule in existing if not is_tosca_rule(rule)]
    return others + [tosca_rule(origin, number) for number, origin in enumerate(origins, start=1)]


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            "--origin",
            action="append",
            dest="origins",
            help="Allowed origin (repeatable); defaults to CORS_ALLOWED_ORIGINS",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        origins = options["origins"] or list(settings.CORS_ALLOWED_ORIGINS)
        if not origins:
            raise CommandError("No origins: set DJANGO_CORS_ALLOWED_ORIGINS or pass --origin.")
        configured = 0
        for alias in ALIASES:
            if alias not in settings.STORAGES:
                continue
            storage = storages[alias]
            bucket = getattr(storage, "bucket_name", None)
            if not bucket:
                self.stdout.write(f"{alias}: not an S3 bucket, skipped")
                continue
            client = storage.connection.meta.client
            try:
                existing = client.get_bucket_cors(Bucket=bucket)["CORSRules"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "NoSuchCORSConfiguration":
                    raise CommandError(f"{alias} ({bucket}): {exc}") from exc
                existing = []
            rules = merged_rules(existing, origins)
            if not options["dry_run"]:
                client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
            configured += 1
            self.stdout.write(f"{alias} ({bucket}): {len(rules)} rule(s), origins {', '.join(origins)}")
        if not configured:
            self.stdout.write(self.style.WARNING("No S3 buckets configured; nothing to do."))
        elif options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: nothing was changed."))
        else:
            self.stdout.write(self.style.SUCCESS("Storage CORS configured."))
