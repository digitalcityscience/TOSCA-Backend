# 10 — GeoServer ACL integration test (gerçek GeoServer)

**Track:** C · **Canonical:** §5(c) canlı doğrulama, §11 C4

**What to build:** Gerçek çalışan GeoServer'a karşı uçtan uca doğrulama: Django bir Workspace kaydeder → `GeoServerSecuritySyncService` ACL kuralını yazar → GeoServer REST'te kural gerçekten görünür ve beklenen enforcement'ı yapar. `make django-test-integration` altında koşar (`-m integration`).

**Blocked by:** 08, 09.

**Status:** ✅ done (2026-08-12)

---

## Mevcut durum (kod incelendi 2026-08-12)

Integration test altyapısı var (`make django-test-integration`, `-m integration` marker). ACL'e özel integration testi **yok** (servis henüz yok). Canonical §5c'deki manuel testin **otomatikleştirilmiş** hali.

## Senaryolar

1. **PRIVATE workspace:** save → `GET /rest/security/acl/layers` içinde `<ws>.*.r = ROLE_<SLUG>_READER` ve `<ws>.*.w = ROLE_<SLUG>_WRITER` görünür.
2. **PUBLIC workspace:** save → `<ws>.*.r = *`, `<ws>.*.w = ROLE_<SLUG>_WRITER`.
3. **PRIVATE→PUBLIC geçiş:** `visibility` değiştir + save → read kuralı `*`'a döner (PUT), write değişmez.
4. **Idempotency:** aynı workspace iki kez save → tek kural seti, çakışma/hata yok.
5. **Break-glass korunur:** global `*.*.w = GROUP_ADMIN,ADMIN` kuralına dokunulmadığı doğrulanır.
6. **Hata yolu (09 ile — güncellendi):** ~~GeoServer erişilemezken save → Workspace `dirty`; GeoServer dönünce `sync_acl` command → `synced`~~ → ticket 09 hard-fail'e geçti. Gerçek GeoServer'a yanlış kimlik bilgisiyle push (401) → `GeoServerACLSyncError` fırlar, Workspace DB'de **hiç oluşmaz** (rollback doğrulanır).

## Adımlar

1. `-m integration` işaretli test modülü ekle (`apps/geodata_providers/tests/`).
2. Gerçek GeoServer bağlantısını test config'inden çöz (mevcut integration testlerin engine fixture desenini yeniden kullan).
3. Her senaryo sonunda REST'ten kural setini oku, assert et. Test sonunda oluşturulan kuralları **temizle** (tear-down `delete_layer_rule`).

## Acceptance criteria

- [x] 6 senaryonun her biri `-m integration` testiyle kapsanır ve yeşil.
- [x] Testler gerçek GeoServer'a REST push yapar ve `GET`'le doğrular.
- [x] Tear-down oluşturulan kuralları siler (idempotent tekrar koşulabilir).
- [x] `make django-test-unit` (integration hariç) hâlâ yeşil; bu testler yanlışlıkla unit koşusuna sızmaz.

**Status: done** (2026-08-12). `tosca_api/apps/geodata_providers/tests/test_geoserver_acl_integration.py`, `pytestmark = pytest.mark.integration`, 6 test — real GeoServer container (`docker-compose-dev.yml` service `geoserver`), `GEOSERVER_HOST`/`PORT`/`ADMIN_USER`/`ADMIN_PASSWORD` env (same pattern as `test_integration.py`). Each test uses a random per-test workspace name and tears down its two `.r`/`.w` rule keys in `tearDown`.

## Doğrulama

```
make django-test-integration
```

## Canonical atıfları
§5(c) canlı doğrulama (manuel testin otomatik hali) · §11 C4.
