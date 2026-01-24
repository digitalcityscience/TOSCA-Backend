.PHONY: help which-env build up down restart logs rebuild rmvolumes \
	django-restart django-logs django-cmd django-shell django-migrate django-makemigrations \
	django-test django-createsuperuser uv-sync uv-install uv-add uv-lock ps clean

# -------------------------------------------------
# ENV selection (DEFAULT = dev) or prod
# -------------------------------------------------
ENV ?= dev

ENV_FILE := .env.$(ENV)
COMPOSE_FILE := docker-compose-$(ENV).yml

# Safety: allow only dev / prod
ifeq ($(ENV),dev)
  COMPOSE_FILE := docker-compose-dev.yml
endif

ifeq ($(ENV),prod)
  COMPOSE_FILE := docker-compose-prod.yml
endif

# -------------------------------------------------
# Load env file
# -------------------------------------------------
ifneq ("$(wildcard $(ENV_FILE))","")
  include $(ENV_FILE)
  export $(shell sed -n 's/^\s*\([A-Za-z_][A-Za-z0-9_]*\)\s*=.*/\1/p' $(ENV_FILE))
else
  $(error ❌ Missing $(ENV_FILE))
endif

# -------------------------------------------------
# Check compose file
# -------------------------------------------------
ifneq ("$(wildcard $(COMPOSE_FILE))","")
else
  $(error ❌ Missing $(COMPOSE_FILE))
endif

# -------------------------------------------------
# Colors for output
# -------------------------------------------------
COLOR_RESET = \033[0m
COLOR_BOLD = \033[1m
COLOR_GREEN = \033[32m
COLOR_BLUE = \033[34m
COLOR_YELLOW = \033[33m
COLOR_RED = \033[31m

# -------------------------------------------------
# Help
# -------------------------------------------------
help:
	@echo ""
	@echo "$(COLOR_BOLD)TOSCA Django API - Docker Management$(COLOR_RESET)"
	@echo ""
	@echo "$(COLOR_GREEN)General Commands:$(COLOR_RESET)"
	@echo "  make up              - Start all services (db, geoserver, django) and show django logs"
	@echo "  make down            - Stop all services"
	@echo "  make build           - Build all Docker images"
	@echo "  make rebuild         - Rebuild and restart all services"
	@echo "  make restart         - Restart all services"
	@echo "  make logs            - Follow logs for all services"
	@echo "  make ps              - Show running containers"
	@echo "  make clean           - Clean up stopped containers and dangling images"
	@echo "  make rmvolumes       - Remove all volumes (⚠️  DANGEROUS - deletes data)"
	@echo ""
	@echo "$(COLOR_BLUE)Django-specific Commands:$(COLOR_RESET)"
	@echo "  make django-restart  - Restart only Django service"
	@echo "  make django-logs     - Follow Django logs"
	@echo "  make django-cmd      - Open bash shell in Django container"
	@echo "  make django-shell    - Open Django shell (python manage.py shell)"
	@echo "  make django-migrate [APP=app] [MIGRATION=0003] - Run migrations"
	@echo "  make django-makemigrations [APP=app] - Create new migrations"
	@echo "  make django-test     - Run Django tests"
	@echo "  make django-createsuperuser - Create Django superuser"
	@echo ""
	@echo "$(COLOR_YELLOW)UV/Python Package Management:$(COLOR_RESET)"
	@echo "  make uv-sync         - Sync dependencies from pyproject.toml and uv.lock"
	@echo "  make uv-install      - Install all dependencies including dev"
	@echo "  make uv-add PKG=<package> - Add a new package (e.g., make uv-add PKG=requests)"
	@echo "  make uv-lock         - Update uv.lock file"
	@echo ""
	@echo "$(COLOR_GREEN)Project Initialization:$(COLOR_RESET)"
	@echo "  make initialize-project - Build, start all services, run migrations, restart Django"
	@echo "  make jdbc-settings-activation - Run GeoServer JDBC settings activation script"
	@echo "$(COLOR_GREEN)Environment:$(COLOR_RESET) ENV=$(ENV)"
	@echo ""
# -------------------------------------------------
# Project Initialization
# -------------------------------------------------
# Builds all Docker images, starts all services, runs Django migrations, and restarts only the Django container.
initialize-project: which-env
	@echo "$(COLOR_GREEN)🚀 Initializing project: build, up, migrate, restart Django...$(COLOR_RESET)"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) build
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d
	@echo "$(COLOR_BLUE)📦 Running Django migrations...$(COLOR_RESET)"
	$(MAKE) django-migrate
	@echo "$(COLOR_BLUE)🔄 Restarting Django service...$(COLOR_RESET)"
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) restart django
	@echo "$(COLOR_GREEN)✅ Project initialized!$(COLOR_RESET)"
	@echo ""
	@sleep 2
	@docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		logs -f django

# Run GeoServer JDBC settings activation script
jdbc-settings-activation: which-env
	@echo "Activating GeoServer JDBC settings with ENV_FILE=$(ENV_FILE)"
	@cd docker/geoserver_docker && \
	ENV_FILE="$(abspath $(ENV_FILE))" \
	./scripts/activate_jdbcS_settings.sh

# -------------------------------------------------
# Helpers
# -------------------------------------------------
which-env:
	@echo "🔧 ENV=$(ENV)"
	@echo "📄 ENV_FILE=$(ENV_FILE)"
	@echo "🐳 COMPOSE_FILE=$(COMPOSE_FILE)"

# -------------------------------------------------
# Build
# -------------------------------------------------
build: which-env
	@echo "$(COLOR_GREEN)🐳 Building all services ($(ENV))$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		build --no-cache

# -------------------------------------------------
# Up - Start all services and follow Django logs
# -------------------------------------------------
up: which-env
	@echo "$(COLOR_GREEN)🚀 Starting all services ($(ENV))$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		up -d
	@echo ""
	@echo "$(COLOR_BLUE)📋 Services started! Following Django logs...$(COLOR_RESET)"
	@echo "$(COLOR_YELLOW)Press Ctrl+C to stop following logs (services will keep running)$(COLOR_RESET)"
	@echo ""
	@sleep 2
	@docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		logs -f django

# -------------------------------------------------
# Down
# -------------------------------------------------
down: which-env
	@echo "$(COLOR_RED)🛑 Stopping all services ($(ENV))$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		down

# -------------------------------------------------
# Restart
# -------------------------------------------------
restart: down up

# -------------------------------------------------
# Logs
# -------------------------------------------------
logs: which-env
	@echo "$(COLOR_BLUE)📋 Following logs for all services...$(COLOR_RESET)"
	@if [ -z "$(filter-out $@,$(MAKECMDGOALS))" ]; then \
		docker compose \
			--env-file $(ENV_FILE) \
			-f $(COMPOSE_FILE) \
			logs -f; \
	else \
		docker compose \
			--env-file $(ENV_FILE) \
			-f $(COMPOSE_FILE) \
			logs -f $(filter-out $@,$(MAKECMDGOALS)); \
	fi

# -------------------------------------------------
# Rebuild
# -------------------------------------------------
rebuild: which-env
	@echo "$(COLOR_YELLOW)♻️  Rebuilding all services ($(ENV))$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		build --no-cache
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		up -d
	@echo ""
	@echo "$(COLOR_BLUE)📋 Following Django logs...$(COLOR_RESET)"
	@sleep 2
	@docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		logs -f django

# -------------------------------------------------
# Volume cleanup (DANGEROUS)
# -------------------------------------------------
rmvolumes: which-env
	@echo "$(COLOR_RED)⚠️  Removing all volumes ($(ENV)) - This will delete all data!$(COLOR_RESET)"
	@echo "Press Ctrl+C to cancel, or wait 5 seconds to continue..."
	@sleep 5
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		down -v
	@echo "$(COLOR_GREEN)✅ Volumes removed$(COLOR_RESET)"

# -------------------------------------------------
# Django-specific Commands
# -------------------------------------------------

# Restart only Django service
django-restart: which-env
	@echo "$(COLOR_BLUE)🔄 Restarting Django service...$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		restart django
	@echo ""
	@echo "$(COLOR_GREEN)✅ Django restarted! Following logs...$(COLOR_RESET)"
	@sleep 1
	@docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		logs -f django

# Follow Django logs
django-logs: which-env
	@echo "$(COLOR_BLUE)📋 Following Django logs...$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		logs -f django

# Open bash shell in Django container
django-cmd: which-env
	@echo "$(COLOR_BLUE)💻 Opening bash shell in Django container...$(COLOR_RESET)"
	docker exec -it tosca-django bash

# Open Django shell
django-shell: which-env
	@echo "$(COLOR_BLUE)🐍 Opening Django shell...$(COLOR_RESET)"
	docker exec -it tosca-django uv run python manage.py shell

# Run Django migrations
# Usage: make django-migrate
#        make django-migrate APP=myapp
#        make django-migrate APP=myapp MIGRATION=0003
django-migrate: which-env
	@echo "$(COLOR_BLUE)📦 Running Django migrations...$(COLOR_RESET)"
	@if [ -n "$(APP)" ] && [ -n "$(MIGRATION)" ]; then \
		echo "Running: migrate $(APP) $(MIGRATION)"; \
		docker exec -it tosca-django uv run python manage.py migrate $(APP) $(MIGRATION); \
	elif [ -n "$(APP)" ]; then \
		echo "Running: migrate $(APP)"; \
		docker exec -it tosca-django uv run python manage.py migrate $(APP); \
	else \
		echo "Running: migrate (all apps)"; \
		docker exec -it tosca-django uv run python manage.py migrate; \
	fi

# Create new Django migrations
# Usage: make django-makemigrations
#        make django-makemigrations APP=myapp
django-makemigrations: which-env
	@echo "$(COLOR_BLUE)📝 Creating new Django migrations...$(COLOR_RESET)"
	@if [ -n "$(APP)" ]; then \
		echo "Running: makemigrations $(APP)"; \
		docker exec -it tosca-django uv run python manage.py makemigrations $(APP); \
	else \
		echo "Running: makemigrations (all apps)"; \
		docker exec -it tosca-django uv run python manage.py makemigrations; \
	fi

# Run Django tests
django-test: which-env
	@echo "$(COLOR_BLUE)🧪 Running Django tests...$(COLOR_RESET)"
	docker exec -it tosca-django uv run pytest

# Create Django superuser
django-createsuperuser: which-env
	@echo "$(COLOR_BLUE)👤 Creating Django superuser...$(COLOR_RESET)"
	docker exec -it tosca-django uv run python manage.py createsuperuser

# -------------------------------------------------
# UV Package Management Commands
# -------------------------------------------------

# Sync dependencies from pyproject.toml and uv.lock
uv-sync: which-env
	@echo "$(COLOR_YELLOW)📦 Syncing dependencies with uv...$(COLOR_RESET)"
	docker exec -it tosca-django uv sync

# Install all dependencies including dev
uv-install: which-env
	@echo "$(COLOR_YELLOW)📦 Installing all dependencies (including dev)...$(COLOR_RESET)"
	docker exec -it tosca-django uv sync --all-extras

# Add a new package
uv-add: which-env
ifndef PKG
	@echo "$(COLOR_RED)❌ Error: PKG variable is required$(COLOR_RESET)"
	@echo "Usage: make uv-add PKG=<package-name>"
	@echo "Example: make uv-add PKG=requests"
else
	@echo "$(COLOR_YELLOW)➕ Adding package: $(PKG)$(COLOR_RESET)"
	docker exec -it tosca-django uv add $(PKG)
	@echo "$(COLOR_GREEN)✅ Package added! Don't forget to rebuild the image.$(COLOR_RESET)"
endif

# Update uv.lock file
uv-lock: which-env
	@echo "$(COLOR_YELLOW)🔒 Updating uv.lock file...$(COLOR_RESET)"
	docker exec -it tosca-django uv lock

# -------------------------------------------------
# Utility Commands
# -------------------------------------------------

# Show running containers
ps: which-env
	@echo "$(COLOR_BLUE)📊 Running containers:$(COLOR_RESET)"
	docker compose \
		--env-file $(ENV_FILE) \
		-f $(COMPOSE_FILE) \
		ps

# Clean up
clean:
	@echo "$(COLOR_YELLOW)🧹 Cleaning up stopped containers and dangling images...$(COLOR_RESET)"
	docker container prune -f
	docker image prune -f
	@echo "$(COLOR_GREEN)✅ Cleanup complete$(COLOR_RESET)"

# Allow arguments to be passed to certain commands
%:
	@:
