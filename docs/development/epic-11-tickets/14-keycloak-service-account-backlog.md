# 14 — (Backlog) Django Keycloak service account (Admin API / reconcile)

**Track:** — (POC dışı) · **Canonical:** §4b (iki iletişim kanalı), §8 ("Sıfırdan gelecek"), §11 (kapsam DIŞI notu)

**What to build:** Django'ya bir Keycloak **service account** (confidential client + client-credentials) ekleyip, token'da **olmayan** veriyi (reconcile, çoklu-org üyelik listesi) Keycloak Admin REST API'den okumak. **Authz kararları için DEĞİL** — sadece nadir işler (reconcile, çoklu-org).

**Blocked by:** 11.

**Status:** backlog — **POC kapsamı dışı.** Canonical §11 onaylı planı bunu açıkça **hariç** tutuyor ("Storage ve Keycloak service account/Admin API hariç"). Bu ticket, iş gündeme geldiğinde hazır olsun diye **kayıt** amaçlı; şu an implement edilmez.

---

## Kapsam netleştirme (2026-08-12): bu ticket "org satırı otomatik açılsın" değil

Login'i gerçek Keycloak ile test ederken ayrı bir ihtiyaç çıktı: kullanıcı token'ında `default_organization` scalar claim'i geliyorsa ama o slug'a karşılık Django'da `Organization` satırı yoksa, satır **login anında otomatik açılıyor** (bkz. ticket 01 "Ek" notu, `organizations/services.py::get_or_create_organization`). Bu **bu ticket'ın kapsamına girmiyor** ve backlog değil — zaten implement edildi:

- Hiçbir Admin API / service account gerektirmiyor; sadece login'de zaten elde var olan token'ı okuyor.
- Sadece `slug` (+ `slug.upper()`'dan türetilen `name`) mirror'lanıyor — `keycloak_org_id`, gerçek görünen isim, grup/rol varlığı gibi Admin API'den gelmesi gereken hiçbir alanı doldurmuyor.

Bu ticket'ın (14) kapsamı hâlâ geçerli ve backlog: **çoklu-org üyelik listesi** + **read-only reconcile** (Keycloak'taki grup/rollerin gerçekten var olduğunu Admin API'den doğrulama, `keycloak_org_id` gibi alanların doldurulması). Bunlar token'da yok / token'dan türetilemez, o yüzden hâlâ Admin API + service account gerektiriyor.

## Neden şimdi değil

- Canonical §4: "POC'ta `Membership` tablosu YOK — org token'dan okunur." Token'daki scalar `default_organization` tek-org için yeterli.
- Canonical §4 canlı doğrulama: `organization` (çoklu-üyelik listesi) token'da **YOK**. Çoklu-org gerekince **ya** mapper'a `organization` array eklenir **ya** bu service account dalı devreye girer.
- §11 onaylı plan Admin API'yi **kapsam dışı** bıraktı.

## Ne zaman açılır (tetikleyiciler)

- Çoklu-org üyelik gerçekten gerekince (bir kullanıcı >1 org), ve mapper'a `organization` array eklemek yerine Admin API tercih edilirse.
- Read-only **reconcile** (Django'nun beklediği grup/rollerin Keycloak'ta gerçekten var olduğunu doğrulama, §4b) otomasyona bağlanmak istenince.

## İş kalemleri (gelecekte)

- [ ] Keycloak'ta `django-dev` (veya ayrı) client'ı **confidential** + **service account** etkinleştir; gerekli Admin API rollerini (realm-management, sınırlı) ver.
- [ ] Django tarafı client-credentials token akışı + Admin API client (rate-limitli, cache'li).
- [ ] Reconcile job: beklenen `/<slug>-readers|writers|admins` grupları + `ROLE_<SLUG>_*` rollerinin varlığını read-only doğrula; drift'i admin'e raporla (Keycloak'a **yazma yok**).
- [ ] Çoklu-org üyelik listesini Admin API'den oku (yalnız token'da yoksa).

## Acceptance criteria (açıldığında)

- [ ] Admin API yalnız reconcile + çoklu-org listesi için; **hiçbir authz kararı** Admin API'ye bağlı değil (request-time authz token'dan).
- [ ] Django Keycloak'a **yazmaz** (§1 — provisioning insan tarafında).
- [ ] Reconcile drift raporlar, otomatik düzeltmez.

## Canonical atıfları
§1 (Django yazmaz) · §4b (iki kanal + service account) · §8 · §11 (kapsam dışı).
