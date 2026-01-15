"""Load sample data useful for local development."""

from django.core.management import call_command


def run() -> None:
    call_command("loaddata", "sample_data", verbosity=1)


if __name__ == "__main__":
    run()
