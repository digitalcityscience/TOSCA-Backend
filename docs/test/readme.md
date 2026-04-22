docker exec tosca-django sh -lc '.venv/bin/python manage.py test tosca_api.apps.geodata_providers.tests.test_geodata_engine_service --settings=tosca_api.settings.test --keepdb'

