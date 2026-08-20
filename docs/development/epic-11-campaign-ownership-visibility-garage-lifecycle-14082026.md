# Epic 11 — Campaign Ownership, Visibility & Garage Lifecycle

**Tarih:** 2026-08-14
**Branch:** `epic-11-s3-production-media`
**Durum:** PR1/PR2/PR3 implementasyonu ve hero-image lifecycle tamamlandı (2026-08-14). Ticket kapsamı bitti.

İlgili dokümanlar:
- `docs/development/epic-11-organization.md` (tenancy ön çalışması)
- `docs/development/epic-11-public-media-lifecycle-gc-14082026.md` (bu işin **ardılı** — reference-based GC, bu ticket'ın önkoşulunu bekliyor)
- `docs/development/epic-11-s3-implemtation-14082026.md` (storage foundation log)
- `docs/development/epic-11_s3-production-media-roadmap.md`
- `docs/development/epic-11-canonical.md`

---

## 1. Neden bu iş gerekli

`MediaAsset` şu an yalnızca `uploader` FK'si taşıyor — hangi organizasyona, hangi Campaign'e ait olduğu bilgisi yok. Storage path şeması flat ve tutarsız (`geostories/{id}/hero/...` vs `geocontext/editorjs/...`). Garage bucket yapısı yalnızca private/public ayrımı yapıyor, archive kavramı yok. Bu üç eksiklik, GC dokümanının (`epic-11-public-media-lifecycle-gc...`) açıkça beklediği önkoşul: "ownership → path şeması → archive lifecycle" bitmeden reference-based GC'ye başlanamaz.

Ayrıca `Event` modeli kendi bağımsız `visibility` alanını taşıyor — Campaign'in visibility kararıyla çelişebilir, tek bir authoritative visibility kaynağı yok.

Bu ticket, kod incelemesi + karar turu sonucu netleşen dört kararı uygular (bkz. §3).

---

## 2. Mevcut durum (kod incelemesi, 2026-08-14)

- `tosca_api/apps/campaigns/models.py::Campaign` — `organization` FK (PROTECT), `status` (draft/active/archived), `visibility` (public/private) zaten var. Ek alan gerekmiyor.
- `tosca_api/apps/geostories/models.py::GeoStory` — `campaign` FK (CASCADE), kendi `status` (draft/published/archived), **ayrı visibility yok** — status tek görünürlük ekseni. Doğru desen, dokunulmayacak.
- `tosca_api/apps/events/models.py::Event` — `campaign` FK (CASCADE), kendi `status` (draft/published/cancelled) **ve** kendi `visibility` (public/private, default PUBLIC). Bu ikinci visibility kaynağı, Campaign.visibility ile çelişiyor → deprecate edilecek (§3.2).
- `tosca_api/apps/core/models.py::MediaAsset` — yalnızca `uploader` FK var. `owner_org`, `campaign` yok. Content'e (GeoStory/GeoContext) bağlantı yok, yalnızca EditorJS JSON body'sine gömülü URL string'i üzerinden zayıf bağ var.
- `tosca_api/apps/organizations/permissions.py` — READER/WRITER/ADMIN rank sistemi (`LEVEL_RANK`), `OrgScopedPermission`, `org_scoped_queryset`, `OrgScopedAdminMixin`, `check_org_level` zaten generic olarak mevcut. Campaign-owned kaynaklara doğru bağlanması yeterli, yeni rol mekanizması gerekmiyor.
- Garage/S3: `docker-compose-dev.yml`, `docker-compose-prod.yml`, `docker/garage/bootstrap.sh` yalnızca `tosca-media-private` / `tosca-media-public` biliyor. Archive bucket yok.
- `GeoFeedback` bu ticket'ın **kapsamı dışında** — kullanıcı kararıyla tamamen yok sayılıyor.

---

## 3. Kararlar (2026-08-14 karar turu)

### 3.1 Campaign ownership + visibility root
```text
Organization → Campaign        ← ownership + visibility root
                ├── GeoStory
                └── Event
```
- `Campaign.organization` ownership kökü.
- GeoStory/Event, Campaign'den organization ownership'i miras alır.
- `Campaign.visibility` **tek** authoritative visibility kaynağı.
- Child status (draft/published/archived) kendi lifecycle ekseni — visibility değil.
- GeoDataProvider/Workspace/Layer authorization'ı **yeniden tasarlanmayacak** — geodata Campaign/GeoStory/Event media ownership'inden bağımsız bir güvenlik sınırı olarak kalır.

### 3.2 Event visibility deprecation
- Mevcut DB alanı (`Event.visibility`) **korunur**, silinmez.
- Authorization kaynağı olarak **kullanılmaz** — sadece Campaign.visibility yetkilendirir.
- API seviyesinde **read-only/non-writable** yapılır (yazma isteklerinde ignore edilir).
- Yeni computed alan: `effective_visibility` — `Campaign.visibility`'den türetilir, serializer'da expose edilir.
- Legacy alan docstring/help_text'te `deprecated` olarak işaretlenir.
- **AND-gate yok** — iki visibility kaynağı çelişkisi yaratmasın diye Campaign tek otorite.
- GeoStory'de ayrı visibility alanı olmadığı için `effective_visibility` = `campaign.visibility` (GeoStory'nin kendi `published` durumu ayrı bir okuma-görünürlüğü kısıtı olarak API'de belgelenir, ayrı alan eklenmez).

### 3.3 PR sıralaması
1. **PR1 — Ownership + scoping + effective visibility** ✅ (2026-08-14, implementasyon tamamlandı)
   - ✅ `MediaAsset.owner_org` (FK, Organization), `MediaAsset.campaign` (FK, Campaign, nullable) eklenir. — `tosca_api/apps/core/models.py`, migration `core/0002_mediaasset_campaign_mediaasset_owner_org_and_more.py`.
   - ✅ Migration: nullable → backfill (mevcut asset'leri mümkün olduğunca ilişkilendir) → gerekirse NOT NULL kararı (backfill sonucu tam çıkmazsa nullable kalabilir, ayrıca değerlendirilecek). — `core/0003_backfill_media_asset_ownership.py` + `core/media_ownership.py` (bkz. §6.1: backfill kısmi eşleşiyor, nullable kalması kararlaştırıldı).
   - ✅ Event API serializer: `visibility` read-only, `effective_visibility` computed alan eklenir. — `Event.effective_visibility` property (`events/models.py`), tüm Event serializer'larına (`List/Detail/Geo/MapOnline/Write`) eklendi.
   - ✅ Org-scoped permission/queryset zincirleri Campaign-owned kaynaklara (GeoStory, Event, MediaAsset) doğru uygulanır. — yeni `CampaignScopedPermission` + `validate_campaign_organization` (`organizations/permissions.py`), `EventViewSet`/`GeoStoryViewSet`'e bağlandı; admin tarafında `OrgScopedAdminMixin` genişletilerek dotted `org_lookup` (örn. `campaign__organization__slug`, `owner_org__slug`) destekleniyor — `EventAdmin`, `GeoStoryAdmin`, yeni `MediaAssetAdmin`.
2. **PR2 — Canonical Garage paths + backfill + archive altyapısı** ✅ (2026-08-14, implementasyon tamamlandı)
   - ✅ Canonical path şeması: `orgs/<org-slug>/campaigns/<campaign-id>/stories/<story-id>/<file>`, `.../events/<event-id>/<file>`, `.../misc/<file>` (campaign biliniyor ama tek bir story/event'e bağlanamayan asset'ler için fallback). — `core/media_paths.py`: `resolve_entity()` (hero-image → EditorJS content via GeoStory/Event → campaign-only misc fallback, aynı öncelik sırası PR1'in `media_ownership.py` backfill'iyle tutarlı), `canonical_storage_path()`. Campaign'i olmayan (orphan) asset'ler için canonical path yok — mevcut konumunda kalıyor (§6.1 kararıyla tutarlı).
   - ✅ **Backfill-all stratejisi** — `core/media_path_migration.py::MediaPathMigrator`: plan (read-only) / apply (copy→verify→DB update→delete) ayrımı. `--apply` olmadan varsayılan dry-run; `storage_path` unique constraint'i her satırın kendi transaction'ında güncellenerek race-safe; copy+verify önce, delete sonra (kesinti = eski path'te erişilebilir kalır, broken link yok); zaten kopyalanmış-ama-DB-güncellenmemiş hedefleri (kesintiye uğramış önceki koşu) idempotent olarak devam ettiriyor.
   - ✅ Management command `migrate_media_paths` (`core/management/commands/migrate_media_paths.py`): `--batch-size`/`--start-after` ile batch/resumable, `--limit` ile aşamalı rollout, `--report` ile JSON çıktı. Boş ve dolu `MediaAsset` tablosunda güvenli (test: `test_command_is_safe_on_empty_table`).
   - ✅ Archive bucket: `tosca-media-archive`, `GARAGE_ARCHIVE_BUCKET` (garage-bootstrap), `S3_ARCHIVE_BUCKET_NAME` (django) — `docker-compose-dev.yml`, `docker-compose-prod.yml`, `docker/garage/bootstrap.sh` (idempotent `ensure_bucket`, mevcut private/public deseniyle aynı), `.env.example`/`.env.dev`. `build_storage_config()`'e `media_archive` storage alias'ı eklendi (`tosca_api/settings/base.py`) — S3 backend'de `archive_bucket_name` boşsa alias hiç oluşturulmuyor (PR3'e kadar hiçbir şey buraya yazmıyor), filesystem backend'de arayüz parity için local'e düşüyor.
   - Not: PR3'ün restore/archive lifecycle'ı henüz bu path'lere yazmıyor — PR2 yalnızca şema + backfill script + bucket altyapısını kurdu.

3. **PR3 — Campaign/GeoStory archive & restore lifecycle** ✅ (2026-08-14, implementasyon tamamlandı)
   - ✅ `MediaAsset.storage_alias` (default/media_public/media_archive) eklendi — `core/models.py`, migration `core/0004_mediaasset_storage_alias.py` + backfill `core/0005_backfill_media_asset_storage_alias.py` (mevcut EditorJS satırlarını `media_public`'e taşır, PR2'nin `migrate_media_paths` prefix kuralıyla tutarlı).
   - ✅ `core/media_lifecycle.py::MediaLifecycleService` — `desired_alias_for_asset()` öncelik sırası: (1) Campaign archived → `media_archive`, (2) sahibi GeoStory archived (Campaign aktif olsa bile) → `media_archive`, (3) aksi halde `Campaign.visibility`'e göre `media_public`/`default`. `move_one()` ve `move_hero_image()` PR2'nin `MediaPathMigrator` ile aynı copy→verify→DB update→delete sırasını izliyor (kesintiye dayanıklı, aynı-boyut resume). `GeoStory.hero_image_storage_alias` ve dinamik `HeroImageField`, hero image'ın gerçek storage backend'ini alias'a göre çözüyor; hero image'ı olan ama `MediaAsset` satırı olmayan story'ler de lifecycle sweep'e dahil ediliyor. Eski `MediaAsset` satırları varsa alias metadata'sı da hero move ile birlikte güncelleniyor.
   - ✅ Signal wiring: `core/media_lifecycle_signals.py`, `CoreConfig.ready()`'e bağlı. `Campaign` `post_save` → yalnızca `status`/`visibility` değiştiğinde `sync_campaign_assets()` (geodata_providers/organizations signals'daki pre_save prior-state yakalama deseniyle aynı). `GeoStory` `post_save` → yalnızca `status` değiştiğinde `sync_story_assets()` (yalnızca o story'e resolve eden asset'leri taşır, kalan campaign asset'leri dokunulmadan kalır).
   - ✅ Restore davranışı: aynı signal — Campaign `archived → active` geçişinde `desired_alias_for_asset()` yeniden değerlendirilir ve **güncel** `Campaign.visibility`'e göre doğru konuma (`media_public`/`default`) taşınır, arşivlenmeden önceki konuma değil (visibility arşivdeyken değişmiş olabilir — açık nokta §6.3 bu şekilde çözüldü: tek yönlü "geri" değil, mevcut visibility otoritesi).
   - ✅ `core/admin.py::MediaAssetAdmin` — `storage_alias` list_display/list_filter/readonly_fields'e eklendi (operatör görünürlüğü, admin'den elle değiştirilemez — yalnızca lifecycle service yazıyor).
   - ✅ Hero image migration: `geostories/0006_geostory_hero_image_storage_alias_and_more.py` ile alias durumu persist ediliyor; yeni/replaced upload doğrudan güncel Campaign/GeoStory durumunun bucket'ına yazılıyor.
   - Not: Event'ler kendi archived durumuna sahip değil (`Event.Status`'ta `ARCHIVED` yok, yalnızca Campaign/GeoStory'de var — §3.1), bu yüzden Event-scoped asset'ler yalnızca Campaign arşivlendiğinde arşive taşınır.


### 3.4 Kapsam dışı
- Reference-based orphan GC (asset artık referans edilmiyorsa silme) — ayrı, sonraki iş (`epic-11-public-media-lifecycle-gc...`).
- GeoFeedback — bu ticket'ta tamamen dokunulmuyor.
- GeoDataProvider/Workspace/Layer authorization redesign — yapılmayacak.

---

## 4. PR2 path stratejisi detayı — backfill-all

- Mixed legacy/new path şeması archive lifecycle'ı (PR3) karmaşıklaştırır — bu yüzden **backfill-all** zorunlu, "yeni upload'lar için yeni şema" seçilmedi.
- Migration script gereksinimleri:
  - `--dry-run` zorunlu default (gerçek taşıma `--apply` ile).
  - Batch/resumable — büyük veri setinde tek transaction'da yapılmaz.
  - `storage_path` unique constraint'i olduğu için race condition'a dikkat (concurrent write ile çakışma).
  - S3 copy+delete sırasında kesinti/broken-link riskine karşı: önce copy + doğrulama, sonra delete (iki adımlı, atomik olmayan ama güvenli sıralama).
- Prod'da veri olup olmadığı **varsayılmayacak** — script hem boş hem dolu ortamda güvenli çalışmalı.

---

## 5. Archive bucket & Garage konfigürasyonu (PR2)

Eklenecek:
- `tosca-media-archive` bucket.
- `GARAGE_ARCHIVE_BUCKET` env (garage servisi).
- `S3_ARCHIVE_BUCKET_NAME` env (django servisi).
- `docker-compose-dev.yml` ve `docker-compose-prod.yml`'e karşılık gelen env girişleri.
- `docker/garage/bootstrap.sh`'a `ensure_bucket "$GARAGE_ARCHIVE_BUCKET"` + izin ataması (idempotent, mevcut private/public deseniyle aynı).

---

## 6. Açık noktalar (implementasyon sırasında netleşecek, ticket'ı bloklamıyor)

1. ✅ `MediaAsset.campaign` hiçbir campaign'e bağlanamayan orphan asset'ler için nullable mı kalacak, yoksa zorunlu mu olacak — backfill sonucuna göre PR1'de karara bağlanacak.
   **Karar (2026-08-14):** nullable kalıyor. Backfill (`core/media_ownership.py`) yalnızca hero-image FK'si veya EditorJS content'inde storage-path referansı bulunan asset'leri eşleştirebiliyor — bu, GeoContext/GeoStory/Event/EventSeries üzerinden geriye doğru izlenebilen her asset'i kapsıyor, ama tamamen orphan (hiçbir content'te referanslanmayan) upload'lar için mantıksal bir campaign yok. NOT NULL zorlamak bu satırları migration'da fail ettirir ya da rastgele bir campaign'e bağlar — ikisi de yanlış. Operatör MediaAssetAdmin üzerinden manuel düzeltme yapabilir.
2. ✅ GeoStory için `effective_visibility` API'de ayrı bir alan mı olacak yoksa yalnızca dokümante mi edilecek — PR1'de netleştirilecek.
   **Karar (2026-08-14):** yalnızca dokümante edildi, ayrı alan eklenmedi (§3.2'de zaten belirtildiği gibi GeoStory'nin kendi visibility alanı yok — `effective_visibility` = `campaign.visibility`, GeoStory serializer'ları zaten `campaign` id'sini expose ediyor, client bu ilişkiyi kendisi kurabilir). Event tarafında (ayrı, deprecated `visibility` alanı olduğu için) `effective_visibility` computed alanı gerçekten eklendi.
3. ✅ PR3 restore davranışı: Campaign archived'dan geri alınabiliyor mu (tek yönlü mü çift yönlü mü) — PR3 tasarımında netleştirilecek.
   **Karar (2026-08-14):** çift yönlü. `Campaign.status` `archived → active` olduğunda aynı `post_save` sinyali tetiklenir ve `desired_alias_for_asset()` yeniden çalışır: sonuç, arşivlenmeden önceki bucket değil, **güncel** `Campaign.visibility` (public/private) tarafından belirlenir. Bu, arşivdeyken visibility değişmiş olabileceği (örn. private→public) senaryosunu doğru şekilde ele alıyor — asset "eski konumuna" değil "doğru konumuna" döner.

---

## 7. Doğrulama planı

- PR1: ✅ migration testleri (backfill script'i sentetik veri üzerinde — `core/tests/test_media_ownership.py`, 8 test: hero-image eşleşmesi, EditorJS content eşleşmesi [GeoStory/Event/EventSeries.default_context üzerinden], unmatched fallback, apply_backfill'in yalnızca resolved entry'leri yazması, zaten-linked asset'lerin re-plan'da atlanması), serializer testleri (`visibility` read-only, `effective_visibility` doğru türetiliyor — `events/tests/test_event_visibility.py`'e eklendi: Campaign.visibility ile Event.visibility çelişirken effective_visibility'nin campaign'i takip etmesi, API'nin visibility yazma isteklerini yok sayması), permission testleri (org-scoped queryset Campaign-owned kaynaklara doğru uygulanıyor — yeni `organizations/tests/test_campaign_scoped_permission.py`, 11 test: SAFE_METHODS her zaman geçer, WRITER/ADMIN seviye kontrolü, cross-org obj-level red, create-time `validate_campaign_organization`). Mevcut events/geostories test suite'leri (`test_api.py`, `test_phase2/7/8`) yeni org-token gereksinimine göre güncellendi — hepsi geçiyor.
- PR2: ✅ path şeması testleri (`core/tests/test_media_paths.py`, 9 test: `canonical_storage_path` her üç kind için, `resolve_entity` hero-image/EditorJS-via-story/EditorJS-via-event/misc-fallback/orphan-None), path migration script dry-run/apply testleri (`core/tests/test_media_path_migration.py`, 11 test: filesystem storage backend ile plan/apply, already-canonical no-op, unresolved-orphan skip, copy-then-verify-then-delete, kesintiye-uğramış-koşuyu-devam-ettirme, size-mismatch güvenli red, management command dry-run default + `--apply` batching + boş tabloda güvenlik), storage settings testleri (`core/tests/test_storage_settings.py`'e 3 test eklendi: filesystem'de `media_archive` alias parity, S3'te archive bucket boşsa alias yok, doluysa signed URL'li alias).
- PR3: ✅ lifecycle state-transition testleri — `core/tests/test_media_lifecycle.py` (hero image copy→verify→DB update→delete, `MediaAsset` metadata sync, `MediaAsset` satırı olmayan hero image sweep'i, campaign/story archive-restore kapsamı ve mevcut safety kontrolleri), signal testleri — `core/tests/test_media_lifecycle_signals.py` (7 test: create'te tetiklenmiyor, ilgisiz alan değişikliğinde tetiklenmiyor, status/visibility değişikliğinde tetikleniyor, Campaign ve GeoStory için ayrı ayrı — `GeoServerSecuritySyncService`/`organizations.signals` testleriyle aynı mock-the-service deseni), backfill migration testi — `core/tests/test_storage_alias_backfill.py` (mevcut EditorJS-prefix'li satırların `media_public`'e taşınması).

## 8. Doğrulama sonucu (2026-08-14, DB ayaktayken çalıştırıldı)

Önceki notta "veritabanı servisi çalışmadığı için pytest başlatılamadı" yazıyordu. Suite artık çalıştırıldı:

- **Tam suite: 1109 passed** (`DJANGO_STORAGE_BACKEND=filesystem`, ~139 sn).
- İlk koşuda **3 test kırıktı**, düzeltildi (commit `dde1160`):
  - `test_media_paths.py::test_resolve_entity_matches_editorjs_content_via_geostory` ve `..._via_event` — testler `ContentFile(b"x")` yazıyordu, `GeoContext` image-block validasyonu bunu PIL ile açamayıp `UnidentifiedImageError` veriyordu. Gerçek PNG bayt üreten `_png_bytes()` helper'ı eklendi.
  - `test_media_lifecycle.py::test_sync_story_assets_moves_only_that_storys_assets` — `story.save()` post_save sinyalini tetikleyip taşımayı *gerçek* storage üzerinde zaten yapıyordu, dolayısıyla tmp_path-backed izole servis `no-change` dönüyordu. Test artık `queryset.update()` ile sinyali baypas ediyor (sinyal davranışı zaten `test_media_lifecycle_signals.py` kapsamında).

### Archive bucket canlı doğrulama

Kullanıcının "archive bucket görünmüyor" gözlemi araştırıldı — bucket **mevcut ve izinli**:

```
tosca-media-private
tosca-media-public
tosca-media-archive   <- RWO / GKtosca-local-django
```

Django storage alias'ları: `default → tosca-media-private`, `media_public → tosca-media-public`, `media_archive → tosca-media-archive`.

Not: `tosca-garage-bootstrap` container'ı 6 saat önce eski imajla koştuğu için log'u hâlâ iki bucket yazıyor (`Garage bootstrap complete: tosca-media-private, tosca-media-public`) — muhtemelen "archive yok" izleniminin kaynağı bu. Bucket, güncel bootstrap script'i ile oluşturulmuş durumda; `docker compose up --build garage-bootstrap` ile log da tazelenir.

Canlı Garage üzerinde uçtan uca archive→restore smoke testi:

| Adım | Sonuç |
|---|---|
| Campaign `active → archived` | asset `default → media_archive`, archive bucket'ta mevcut, private bucket'tan silinmiş |
| Campaign `archived → active` | asset güncel `visibility=private` gereği `default`'a döndü, archive bucket'tan silindi |

Yani §3.3 PR3'teki çift yönlü lifecycle prod-benzeri S3 backend'de doğrulanmıştır.
