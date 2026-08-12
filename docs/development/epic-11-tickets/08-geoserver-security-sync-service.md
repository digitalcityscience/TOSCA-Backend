# 08 — `GeoServerSecuritySyncService` + Workspace `post_save` signal

**Track:** C · **Canonical:** §5(c), §8 ("en kritik, hiç izi yok"), §9, §11 C2

**What to build:** Bir Workspace kaydedilince (oluşturma veya `visibility` değişimi) org rollerini GeoServer Data Security ACL'ine **senkron push** eden servis. Django → GeoServer tek yön; Keycloak'a yazma **yok**. Issue 51'in `KeycloakSyncService` yarısı **silindi** (§9) — geriye yalnız bu kalır.

**Blocked by:** 01, 02, 07.

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

`search_symbols "GeoServerSecuritySyncService"` → **yok**. Var olan `sync_service.py::GeoServerSyncService` ve `sync/*_syncer.py` katalog senkronu (workspace/store/layer/style) — **security ACL değil**. Bu servis sıfırdan.

## ACL kural şablonu (canonical §5c)

Workspace `<ws>` = `Workspace` adı (GeoServer workspace adı), `<SLUG>` = sahip org slug (upper):
```
PRIVATE (default):
  <ws>.*.r = ROLE_<SLUG>_READER
  <ws>.*.w = ROLE_<SLUG>_WRITER
PUBLIC:
  <ws>.*.r = *                       # anonymous read
  <ws>.*.w = ROLE_<SLUG>_WRITER      # write asla genişlemez (§6)
```
> `<layer>=*` → workspace-geneli kural. WRITER=ADMIN=`.w` (GeoServer silmeyi ayıramaz, §7 bilinçli trade-off). Break-glass `*.*.w = GROUP_ADMIN,ADMIN` global kuralı değişmez (§3).

## Adımlar

1. **Servis** — `apps/geodata_providers/` altında `GeoServerSecuritySyncService` (mevcut sync katmanının yanına). Girdi: bir `Workspace` örneği. Görev:
   - Sahip org slug'ından `ROLE_<SLUG>_READER/WRITER` üret (`Organization.reader_role`/`writer_role` helper'ları, ticket 01).
   - `visibility`'ye göre yukarıdaki iki (PRIVATE) / iki (PUBLIC) kuralı hesapla.
   - Ticket 07'nin `set_layer_rule(key, roles)` ile **idempotent** push (yeni→POST, varsa→PUT).
   - PUBLIC→PRIVATE geçişte eski `*` read kuralını doğru role çevir (PUT); gerekiyorsa artık geçersiz kuralları temizle (`delete_layer_rule`).
2. **Signal** — `post_save` (Workspace) → servisi **senkron** çağır (canonical §11 Zemin: "ACL push = model save senkron"). App `apps.py::ready()` içinde bağla. Signal'ı yalnız ilgili alanlar (`organization`, `visibility`, ilk create) değiştiğinde çalıştıracak koruma ekle (gereksiz push yok).
3. **Hata yolu bu ticket'ta DEĞİL** — push başarısızsa ne olacağı ticket 09'da (`sync_status=dirty` + retry command). Burada: başarısızlıkta exception'ı yut**ma**, 09'un yakalayabilmesi için sinyalle/işaretle (09 ile arayüzü hizala). Basit tutmak için: bu ticket happy-path push + hata durumunda log; 09 dirty-marking + command ekler.
4. GeoServer bağlantısı: hangi `GeodataEngine`/`GeoServerClient` örneğinin kullanılacağını mevcut engine factory'den çöz (`engine_factory.py::EngineClientFactory`).

## Acceptance criteria

- [x] `GeoServerSecuritySyncService` bir Workspace alır, §5c şablonuna göre ACL kurallarını hesaplar.
- [x] PRIVATE: `<ws>.*.r=ROLE_<SLUG>_READER`, `<ws>.*.w=ROLE_<SLUG>_WRITER`.
- [x] PUBLIC: `<ws>.*.r=*`, `<ws>.*.w=ROLE_<SLUG>_WRITER`.
- [x] Push **idempotent** (aynı Workspace iki kez save → aynı sonuç, hata yok) — `set_layer_rule` (07) üzerinden.
- [x] PUBLIC↔PRIVATE geçişi read kuralını doğru günceller (PUT), artık kuralı bırakmaz — key sabit (`<ws>.*.r`), değer değişir, ayrı delete gerekmez.
- [x] `post_save` signal servisi senkron çağırır; yalnız ilgili alan değişiminde (`organization`/`visibility`/ilk create).
- [x] Global break-glass `*.*.w = GROUP_ADMIN,ADMIN` kuralına dokunulmaz (servis yalnız `<ws>.*.r/w` key'lerine yazar).
- [x] Django Keycloak'a **hiçbir** yazma yapmaz.
- [x] Birim test (GeoServerClient mock'lu): PRIVATE/PUBLIC/geçiş için doğru `set_layer_rule` çağrıları — `test_security_sync_service.py` (8 test) + `test_security_sync_signal.py` (4 test).

**Status: done** (2026-08-12). `GeoServerSecuritySyncService` in `apps/geodata_providers/security_sync.py` (named to avoid the `*sync_service` import-boundary glob in `test_service_layer_boundary.py`, which flags anything importing `sync_service`/`geoserver.client` outside the factory — this service instead goes through `GeodataEngine.get_client()` → `EngineClientFactory`). Signal wiring in `apps/geodata_providers/signals.py`, connected via `GeodataProvidersConfig.ready()`. Failure path (dirty-marking, retry command) deliberately left to ticket 09 — this ticket logs and returns `False` on ACL push failure without raising, so a GeoServer outage never blocks a Workspace save.

## Doğrulama

```
make django-test-unit          # mock'lu servis + signal testleri
```
Gerçek GeoServer uçtan uca → ticket 10.

## Canonical atıfları
§5(c) şablon + canlı doğrulama · §6 (public read'i genişletir, write asla) · §7 direct-write trade-off · §9 KeycloakSyncService silindi · §11 C2.
