# Epic 11 — Public Media Lifecycle & Reference-Based GC

**Tarih:** 2026-08-14
**Branch:** `epic-11-s3-production-media`
**Başlangıç branch'i:** `main`
**Durum:** BEKLEMEDE — ownership/tenancy işi (org-aware path + `MediaAsset.owner_org`) bitmeden başlanmayacak. Bu doküman sonraki session için hazır kickoff notudur.

> Dosya adı takip kolaylığı için istenen biçimde bırakıldı: `epic-11-public-media-lifecycle-gc-14082026.md`.

İlgili dokümanlar:
- `docs/development/epic-11-s3-implemtation-14082026.md` (storage foundation log)
- `docs/development/epic-11_s3-production-media-roadmap.md`
- `docs/development/epic-11-organization.md` (tenancy — bu işin ön koşulu)

---

## 1. İlk sorumuz (bu tartışma neden başladı)

Bir başka agent, review sırasında şu riski işaretledi:

> Public bucket'a (`querystring_auth=False`) yazılan dosya, sen silene kadar **süresiz public** kalır. GeoStory yayından kalkarsa ilişkili görseli Garage'dan da silmek gerekir; CDN varsa cache invalidation da lazım. Çözüm olarak bir lifecycle politikası (örn. 90 gün TTL) düşünülmeli.

Karşı görüş (diğer local agent): Sabit 90 gün TTL tehlikeli — yayında duran eski bir GeoStory'nin görselleri 90 gün sonra yok olabilir. Daha doğru model **DB referanslı cleanup + gerektiğinde CDN invalidation**:

```
asset artık referans edilmiyor  →  public derivative sil  →  varsa CDN invalidate
```

**Sorumuz:** Public media için doğru lifecycle/GC modeli nedir, ve bunu ne zaman yapmalıyız?

---

## 2. Koddan bulduklarımız (2026-08-14 itibarıyla)

Tartışmadaki premis kısmen yanlış zemine oturuyordu. Kod incelemesi:

### 2.1 Derivative'ler public bucket'ta DEĞİL
- `tosca_api/apps/core/image_derivatives.py:105` — `generate_derivative` çıktısını `default_storage`'a yazar.
- `default_storage` = **private bucket** (`querystring_auth=True`, `tosca_api/settings/base.py:266`).
- `tosca_api/apps/core/views.py:82` — `MediaImageDerivativeView`, derivative'i doğrudan S3 URL'i ile değil, uygulama üzerinden `FileResponse` ile **proxy'ler**. `Cache-Control: public, max-age=31536000, immutable` header'ı var ama dosya private'ta.
- Sonuç: "public'te süresiz kalan derivative" senaryosu **derivative path'i için geçerli değil.** İmza süresi kullanıcıya yansımaz (app her istekte taze açar); maliyeti proxy yüküdür.

### 2.2 Gerçek public yüzey: EditorJS orijinalleri
- `tosca_api/apps/geocontext/views.py:69` — EditorJS upload `storages["media_public"]`'a (public bucket, `querystring_auth=False`, `base.py:289`) yazar.
- Path: `geocontext/editorjs/...` (bkz. `_UPLOAD_SUBDIR`, `geocontext/views.py:48`).
- Yani bugün mimaride: **public ORİJİNAL + private/proxy DERİVATİF**. Risk bu public orijinallerde.

### 2.3 Referans bağı yok
- `tosca_api/apps/core/models.py:19` — `MediaAsset` yalnızca `uploader` FK'si taşır. GeoStory/GeoContext'e **hiçbir FK yok**, `owner_org` yok.
- EditorJS asset'i ile onu gömen içerik arasındaki tek bağ: içeriğin EditorJS JSON body'sine string olarak gömülü **URL**.
- Silme/cleanup sinyali kodda yok (`post_delete` yok, GC job yok).
- Sonuç: "asset referans ediliyor mu?" sorusu bugün **cevaplanamıyor** — reference-based GC için önce bu bağın kurulması gerekir.

---

## 3. Vardığımız sonuç

1. **Sabit TTL reddedildi.** Yayında duran referanslı içeriğe kör zaman-bazlı silme uygulanmaz.
2. **Doğru model: reference-based GC + grace period.** Yalnızca *referans edilmeyen VE N günden eski* asset'ler silinir (draft/race'leri hayatta tutmak için grace period).
3. **CDN invalidation ertelendi.** Public bucket'ın önünde henüz CDN yok; koyulana kadar teorik.
4. **Bu bir merge blocker değildi.** Orphan asset = storage maliyeti, doğruluk/güvenlik açığı değil (bucket kasıtlı public-read). Follow-up olarak konumlandırıldı — bu doküman.

---

## 4. Neden ownership/tenancy işi ÖNCE (bağımlılık)

Bu GC işi, tenancy işinin belirlediği üç şeye bağlı; o yüzden onun **üstüne** oturur:

1. **Ne public?** — Tenancy hedefi "private orijinal + public derivative" inversiyonunu içeriyor (bkz. `epic-11-organization.md` ve diğer agent'ın grill sorusu: EditorJS orijinali private'a mı çekilecek?). GC public yüzeyi hedefler; yüzey değişiyorsa GC'nin kapsamı da değişir. Doğru inversiyon kararı GC'nin işini daha baştan küçültür.
2. **Path şeması ne?** — Bugün flat (`geocontext/editorjs/...`, `derivatives/{digest}/...`). Tenancy bunu `orgs/<org-slug>/geostories/originals|derivatives/...` yapacak. GC bu path'lere göre yazılır; şimdi yazılırsa path değişince baştan yazılır.
3. **Referans bağı nasıl?** — `MediaAsset.owner_org` FK'si zaten tenancy işinde ekleniyor. GC'nin referans sorgusu bu bağın üstüne oturur.

**Kısacası:** GC'yi önce yapmak = çift iş. Sıra: ownership → GC.

**Not:** GC'nin beklediği kısım dar. Bloklayan: (a) public/private inversiyon kararı, (b) org-aware path şeması, (c) `owner_org` migration'ı. **Keycloak çoklu-org (tenancy madde 5) GC'yi bloklamaz** — paralel gidebilir.

---

## 5. Sonraki session — çözüm planı (ownership bittikten sonra)

Kullanıcı bu dokümanı verdiğinde başlanacak iş. **Önce doğrula, sonra uygula** — aşağıdaki dosya/satır referansları 2026-08-14 tarihli; tenancy işi bunları değiştirmiş olabilir.

### 5.0 Ön koşul kontrolü (başlamadan)
- [ ] `MediaAsset` artık `owner_org` taşıyor mu? (`core/models.py`)
- [ ] Org-aware path üretimi devrede mi? (`orgs/<slug>/.../originals|derivatives/`)
- [ ] Public/private inversiyon kararı verildi mi ve kod buna uyuyor mu? (EditorJS orijinali private mı, public mı?)
- Bu üçü yoksa GC'ye başlama — kullanıcıya eksik ön koşulu bildir.

### 5.1 Referans bağını netleştir (GC'nin temeli)
İki seçenekten birini uygula (tenancy işinin ne bıraktığına göre karar ver):
- **A — Usage/reference tablosu:** İçerik (GeoStory/GeoContext) kaydedilirken body'deki asset URL'leri parse edilip `MediaAsset` ile ilişki (M2M veya usage tablosu) kurulur. GC bu tabloyu sorgular. Doğru ama daha çok kod.
- **B — Reconciler (periyodik tarama):** Bir management command tüm içerik body'lerini tarar, kullanılan `storage_path` setini çıkarır. `MediaAsset` tablosundaki bu sete girmeyen kayıtlar "unreferenced" işaretlenir. Daha az invaziv, eventual-consistent.
- Öneri: İçerik hacmine göre B ile başla (basit, güvenli), sonra gerekiyorsa A'ya geç.

### 5.2 GC management command
- Yeni: `tosca_api/apps/core/management/commands/gc_orphan_media.py`
- Mantık: `unreferenced AND updated_at < now() - GRACE_PERIOD` olan `MediaAsset`'leri seç → storage'dan sil → DB kaydını sil (veya soft-delete).
- `--dry-run` zorunlu default; gerçek silme `--apply` ile.
- Grace period ayarlanabilir (settings, örn. `MEDIA_GC_GRACE_DAYS = 30`).
- Org-aware: `owner_org` ile scope'lanabilmeli (tek org GC'si mümkün olmalı).
- Mevcut `migrate_media_to_storage` command'ini (`core/management/commands/`) ve `media_migration.py`'deki `verify_tracked_assets` desenini referans al — aynı storage-alias erişim modeli.

### 5.3 Storage silme
- `storages[alias].delete(path)` üzerinden — view/model'e doğrudan boto3 sokma (epic-11 kuralı, bkz. `epic-11-s3-implemtation`).
- Hem public bucket (EditorJS orijinali) hem private bucket (derivative cache) temizlenmeli; asset'in bulunduğu alias'a göre.
- Derivative cache invalidation: bir orijinal silinince ona ait `derivatives/{digest}/...` girdilerini de temizle (digest = `sha1(original_path)`, bkz. `image_derivatives.py:137`).

### 5.4 CDN invalidation (koşullu)
- Sadece public bucket'ın önünde CDN varsa. Yoksa **atla** — bu iş kapsamı dışı, ayrı ticket.

### 5.5 Testler
- `filesystem` storage backend ile çalıştır (bkz. memory: `DJANGO_STORAGE_BACKEND=filesystem` ile pytest — container env `s3`).
- Kapsam: unreferenced tespiti, grace period sınırı, dry-run silmez, `--apply` siler, referanslı asset asla silinmez, org-scope doğru.

---

## 6. Açık kararlar (session başında netleştir)
1. Referans modeli A (tablo) mı B (reconciler) mı? → §5.1
2. Grace period kaç gün? → öneri 30, ama draft yaşam döngüsüne göre.
3. Silme mi soft-delete mi? → geri dönülebilirlik isteniyorsa soft-delete + ayrı hard-delete job.
4. GC nasıl tetiklenir? → cron/management command manuel mi, periyodik job mu?

---

## 7. Özet
- **Risk gerçek ama derivative'lerde değil, public EditorJS orijinallerinde.**
- **Çözüm: reference-based GC + grace period**, sabit TTL değil.
- **Ownership işinin üstüne oturur** — path şeması, `owner_org` ve public/private inversiyon kararı ön koşul.
- Bu doküman, o karar verildikten sonra doğrudan §5'ten başlanabilecek şekilde hazır.
