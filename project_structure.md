# TOSCA-Web API - Proje Yapısı

```
tosca-web-api/
│
├── .github/
│   ├── workflows/
│   ├── ISSUE_TEMPLATE/
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── CONTRIBUTING.md
│
├── docs/
│   ├── api/
│   ├── deployment/
│   └── development/
│
├── tosca_api/
│   ├── __init__.py
│   ├── asgi.py
│   ├── urls.py
│   ├── wsgi.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── development.py
│   │   ├── production.py
│   │   └── test.py
│   └── apps/
│       ├── __init__.py
│       ├── core/
│       │   ├── apps.py
│       │   ├── models.py
│       │   ├── pagination.py
│       │   ├── permissions.py
│       │   └── utils.py
│       ├── authentication/
│       │   ├── apps.py
│       │   ├── backends.py
│       │   ├── middleware.py
│       │   ├── permissions.py
│       │   ├── urls.py
│       │   ├── views.py
│       │   └── tests/
│       ├── users/
│       │   ├── apps.py
│       │   ├── models.py
│       │   ├── serializers.py
│       │   ├── urls.py
│       │   ├── views.py
│       │   └── tests/
│       └── tosca_web/
│           ├── apps.py
│           ├── urls.py
│           ├── layers/
│           │   ├── models.py
│           │   ├── serializers.py
│           │   ├── urls.py
│           │   ├── views.py
│           │   └── tests/
│           ├── participation/
│           │   ├── models.py
│           │   ├── serializers.py
│           │   ├── urls.py
│           │   ├── views.py
│           │   └── tests/
│           ├── projects/
│           │   ├── models.py
│           │   ├── serializers.py
│           │   ├── urls.py
│           │   ├── views.py
│           │   └── tests/
│           └── geojson/
│               ├── urls.py
│               ├── views.py
│               └── tests/
│
├── docs/
│
├── scripts/
│   ├── init_db.py
│   └── load_sample_data.py
│
├── tests/
│   ├── conftest.py
│   └── factories.py
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
├── pytest.ini
├── manage.py
├── README.md
└── LICENSE
```

## Ana Yapı Mantığı

```
apps/
├── core/           → Herkesin kullandığı ortak kod
├── authentication/ → Keycloak JWT doğrulama
├── users/         → Kullanıcı profilleri
├── tosca_web/     → 🎯 Frontend için tüm endpoint'ler
│   ├── layers/
│   ├── participation/
│   ├── projects/
│   └── geojson/
```

## API Endpoint Yapısı

```
/api/v1/
├── auth/                              # Keycloak login/logout
├── users/                             # Kullanıcı yönetimi
│
├── tosca-web/                         # 🎯 TOSCA-Web endpoints
│   ├── layers/                        # Layer yönetimi
│   ├── participation/                 # Vatandaş katılımı
│   ├── projects/project-x/            # Proje-X özel endpoint'leri
│   └── geojson/                       # PostGIS direkt sorgular
│
```

## pyproject.toml (UV Configuration)

```toml
[project]
name = "tosca-web-api"
version = "0.1.0"
description = "TOSCA-Web API - Backend for geospatial web applications"
requires-python = ">=3.12"

dependencies = [
    "django>=5.1",
    "djangorestframework>=3.15.0",
    "django-environ>=0.11.0",
    "python-keycloak>=4.0.0",
    "pyjwt[crypto]>=2.8.0",
    "psycopg[binary]>=3.2.0",
    "redis>=5.0.0",
    "celery>=5.4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-django>=4.8.0",
    "ruff>=0.5.0",
    "mypy>=1.11.0",
]

[tool.ruff]
line-length = 100
target-version = "py312"
```

## Quick Start

```bash
# UV ile kurulum
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo>
cd tosca-web-api

# Virtual environment + dependencies
uv venv dcs-api
source dcs-api/bin/activate
uv sync

# Database setup
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py runserver
```
