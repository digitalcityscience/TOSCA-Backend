"""Initialize database with default data."""

from django.core.management import call_command


def run() -> None:
    call_command("migrate")
    call_command("loaddata", "initial_data", verbosity=1)


if __name__ == "__main__":
    run()
