# TOSCA-Web API

Django REST backend powering the TOSCA-Web geospatial platform.

## Quick Start

```bash
uv venv dcs-api
source dcs-api/bin/activate
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py runserver
```

See `project_structure.md` for a detailed overview of the repository layout.
