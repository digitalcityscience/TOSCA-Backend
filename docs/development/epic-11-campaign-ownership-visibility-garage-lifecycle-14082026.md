# Epic 11 — Campaign Ownership, Visibility & Garage Lifecycle

**Tarih:** 2026-08-14
**Branch:** `epic-11-s3-production-media`
**Durum:** ONAYLANDI — implementasyona başlanacak.

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
2. **PR2 — Canonical Garage paths + backfill + archive altyapısı**
   - Yeni path şeması: `orgs/<org>/campaigns/<campaign-id>/stories/<story-id>/...`, `.../events/<event-id>/...`.
   - **Backfill-all stratejisi** — yeni upload'lar değil, mevcut tüm asset'ler yeni path şemasına taşınır (aşağıda §4).
   - Archive bucket: `tosca-media-archive`, env değişkenleri `GARAGE_ARCHIVE_BUCKET`, `S3_ARCHIVE_BUCKET_NAME`, dev/prod compose + idempotent bootstrap.
3. **PR3 — Campaign/GeoStory archive & restore lifecycle**
   - Status değişince asset'lerin private/public/archive arası taşınması.
   - Restore davranışı: Campaign.visibility'ye göre doğru konuma geri taşıma.

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
3. PR3 restore davranışı: Campaign archived'dan geri alınabiliyor mu (tek yönlü mü çift yönlü mü) — PR3 tasarımında netleştirilecek.

---

## 7. Doğrulama planı

- PR1: ✅ migration testleri (backfill script'i sentetik veri üzerinde — `core/tests/test_media_ownership.py`, 8 test: hero-image eşleşmesi, EditorJS content eşleşmesi [GeoStory/Event/EventSeries.default_context üzerinden], unmatched fallback, apply_backfill'in yalnızca resolved entry'leri yazması, zaten-linked asset'lerin re-plan'da atlanması), serializer testleri (`visibility` read-only, `effective_visibility` doğru türetiliyor — `events/tests/test_event_visibility.py`'e eklendi: Campaign.visibility ile Event.visibility çelişirken effective_visibility'nin campaign'i takip etmesi, API'nin visibility yazma isteklerini yok sayması), permission testleri (org-scoped queryset Campaign-owned kaynaklara doğru uygulanıyor — yeni `organizations/tests/test_campaign_scoped_permission.py`, 11 test: SAFE_METHODS her zaman geçer, WRITER/ADMIN seviye kontrolü, cross-org obj-level red, create-time `validate_campaign_organization`). Mevcut events/geostories test suite'leri (`test_api.py`, `test_phase2/7/8`) yeni org-token gereksinimine göre güncellendi — hepsi geçiyor.
- PR2: path migration script dry-run/apply testleri (filesystem storage backend ile, mevcut pytest deseniyle), archive bucket bootstrap idempotency testi.
- PR3: lifecycle state-transition testleri (Campaign archive → child asset archive path'e taşınıyor, tek GeoStory archive → yalnızca o story'nin asset'leri taşınıyor, restore → doğru private/public konuma dönüyor).
