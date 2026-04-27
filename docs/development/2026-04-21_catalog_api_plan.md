# Catalog API Plan

Tarih: 2026-04-21

## Amaç

Web GIS uygulamasına provider/workspace/layer/style verisini sunacak ayrı bir
read/query API tasarlamak.

Bu API yönetim CRUD yüzü değildir. Consumer-facing katalog yüzüdür.

## App Adı

- `catalog_api`

## Rol

`catalog_api` app'inin sorumluluğu:

- provider list
- workspace detail
- layer detail
- style list/detail
- frontend için sadeleştirilmiş response shape

`catalog_api` app'i şunları yapmaz:

- create/update/delete
- publish/unpublish
- GeoServer mutate logic
- command/orchestration logic

## Domain Bağımlılığı

`catalog_api`, `geodata_providers` app'ine HTTP/DRF ile çağrı yapmaz.

Onun yerine:

- `geodata_providers/services/queries/` içindeki ortak query service'leri import eder

## Query Service Katmanı

`catalog_api`dan önce `geodata_providers` içinde şu query service'ler tanımlanmalı:

- `geodata_providers/services/queries/provider_query_service.py`
- `geodata_providers/services/queries/workspace_query_service.py`
- `geodata_providers/services/queries/layer_query_service.py`
- `geodata_providers/services/queries/style_query_service.py`

## İlk Endpoint'ler

1. `GET /api/catalog/providers/`
2. `GET /api/catalog/providers/{provider_id}/workspaces/{workspace_id}/`
3. `GET /api/catalog/styles/`
4. `GET /api/catalog/styles/{style_id}/`

## Response İlkeleri

GeoServer ham response'u doğrudan frontend'e verilmemeli.

Temizlenecek alanlar:

- iç `href` alanları
- gereksiz nested objeler
- frontend'in kullanmayacağı kabarık metadata

Korunacak alanlar:

- `id`
- `name`
- `title`
- `type`
- `table_name`
- `styles`
- `attributes`
- bbox / crs / geometry gibi anlamlı metadata

## Permission Soruları

Karar verilmesi gerekenler:

- authenticated mı
- public mi
- layer visibility filtresi var mı

İlk versiyon için öneri:

- authenticated read

## Uygulama Sırası

1. `geodata_providers` command service refactor bitsin
2. `geodata_providers/services/queries/` service'leri tanımlansın
3. `catalog_api` app'i açılsın
4. DRF serializer + viewset/APIView katmanı yazılsın
5. web GIS payload shape netleştirilsin

## Not

`catalog_api`, `geodata_providers` refactor'ından sonra gelmelidir.

Çünkü önce domain/service boundary netleşmeli, sonra read API onun üstüne
oturmalıdır.
