# 09 — ACL hata yolu: `sync_status=dirty` + `sync_acl` retry command

**Track:** C · **Canonical:** §5(c) HTTP semantiği, §10a (basit dirty+retry), §11 C3

**What to build:** ACL push başarısız olursa Workspace `dirty` işaretlenir (istek başarısız olsa da veri kaybı yok), bir management command sonradan dirty Workspace'leri **idempotent** olarak yeniden push eder. v1'de dağıtık outbox/framework **YOK** — bilinçli olarak basit (§10a).

**Blocked by:** 08.

**Status:** ready (08 done, 2026-08-12)

---

## Mevcut durum (kod incelendi 2026-08-12)

- ✅ Var: `Workspace` zaten `SyncStateMixin`'den geliyor → `sync_status` alanı + `SyncState` TextChoices + `is_synced` mevcut. Katalog senkronu bunu kullanıyor.
- ❓ Doğrula: mevcut `SyncState` choices'ında `dirty`/`error` benzeri bir değer var mı? Yoksa ekle ya da ACL için ayrı bir durum alanı kullan. **ACL senkron durumunu katalog senkron durumundan karıştırma** — gerekiyorsa ayrı bir alan (`acl_sync_status`) düşün. Karar: mevcut `SyncStateMixin` semantiği ACL'i de kapsıyorsa onu kullan; kapsamıyorsa ACL'e özel `acl_sync_status` alanı + migration ekle.
- ❌ Yok: `sync_acl` management command.

## Adımlar

1. **Durum alanı** (yukarıdaki karara göre): push başarısızsa Workspace `dirty`; başarılıysa `synced`. Alanı ticket 08'in servisiyle hizala — servis push sonucunu bu alana yazsın.
2. **Servis entegrasyonu (08 ile bağ):** `GeoServerSecuritySyncService` push'unu try/except ile sar; hata → `dirty` + `logger.error`; başarı → `synced`. İstek (Workspace save) **bu yüzden patlamaz** (senkron ama best-effort; canonical §10a "revoke en fazla 30 dk açık kalabilir — bilinçli").
3. **Management command** — `sync_acl` (`apps/geodata_providers/management/commands/sync_acl.py`):
   - `dirty` tüm Workspace'leri bul, her biri için servisi tekrar çağır (idempotent push, §5c PUT/POST ayrımı 07'de çözüldü).
   - Başarılı → `synced`. Rapor: kaç workspace denendi/başarılı/başarısız.
   - `--workspace <name>` ile tek workspace hedefleme opsiyonu (debug).
4. Idempotency: command tekrar tekrar koşabilmeli; zaten synced olanları da güvenle re-push edebilmeli (07 `set_layer_rule` idempotent).

## Acceptance criteria

- [ ] ACL push hatası Workspace'i `dirty` işaretler, save/istek başarısız **olmaz**.
- [ ] Push başarısı `synced` işaretler.
- [ ] `sync_acl` management command dirty Workspace'leri bulur ve yeniden push eder.
- [ ] Command idempotent; özet rapor basar (denenen/başarılı/başarısız).
- [ ] `--workspace` ile tek hedef çalışır.
- [ ] Birim test (mock client): hata→dirty, retry→synced, command dirty'leri toplar.

## Doğrulama

```
make django-test-unit
python manage.py sync_acl --help    # command kayıtlı
```

## Canonical atıfları
§10a "ACL push başarısızlığı → sync_status=dirty + retry management command" · §5c idempotent push · §11 C3.
