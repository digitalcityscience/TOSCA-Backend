docker exec tosca-geoserver mkdir -p \
/opt/geoserver/data_dir/security/filter/keycloak-auth

docker cp \
/Users/hsadmin/Desktop/coding/dcs-django-api/docker/geoserver-init/localhostDeployment/keycloakv2.xml \
tosca-geoserver:/opt/geoserver/data_dir/security/filter/keycloak-auth/config.xml

docker cp \
/Users/hsadmin/Desktop/coding/dcs-django-api/docker/geoserver-init/localhostDeployment/tomcat-web.xml \
tosca-geoserver:/usr/local/tomcat/conf/web.xml

docker cp \
/Users/hsadmin/Desktop/coding/dcs-django-api/docker/geoserver-init/localhostDeployment/geoserver-security-config.xml \
tosca-geoserver:/opt/geoserver/data_dir/security/config.xml

realm url : https://auth.dcs.hcu-hamburg.de/realms/geoserver-realm/.well-known/openid-configuration
