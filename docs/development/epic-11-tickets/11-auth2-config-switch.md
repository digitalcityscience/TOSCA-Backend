# 11 — auth2 config switch: `.env.dev` / `base.py` / `.env.example`

**Track:** B (GeoServer OIDC + Keycloak geçişi) · **Canonical:** §11 B2, §12

**What to build:** Django'yu eski Keycloak'tan (`auth.dcs.hcu-hamburg.de`, realm `prod-realm`) yeni Keycloak'a (`auth2.dcs.hcu-hamburg.de`, realm `tosca-dev`) çevirmek. Üç env değişkeni + `base.py` default'u + `.env.example`. Client adı `django-dev` **aynı kalır**.

**Blocked by:** None — can start immediately (ama takım ortamıyla koordine et: bu, herkesin lokal login'ini etkiler).

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

`git status`: `settings/base.py` zaten değişiklik altında (auth2 geçişi başlamış olabilir). Bu ticket **tüm noktaların tutarlı** olduğunu garantiler. Canonical §12 net bir checklist veriyor.

## Değişecek yerler (canonical §12)

- [x] **`.env.dev`:** zaten auth2/tosca-dev'e geçirilmişti (bu ticket başladığında uncommitted state'te bulundu) — `KEYCLOAK_SERVER_URL`, `KEYCLOAK_JWKS_URL`, `KEYCLOAK_ISSUER` üçü de doğru.
- [x] **`tosca_api/settings/base.py`** (satır 276-277) — `KEYCLOAK_SERVER_URL` default'u `https://auth.dcs.hcu-hamburg.de/` → `https://auth2.dcs.hcu-hamburg.de/`; `KEYCLOAK_REALM` default'u `prod-realm` → `tosca-dev`. (`KEYCLOAK_JWKS_URL`/`KEYCLOAK_ISSUER` bu ikisinden türediği için otomatik düzeldi.) Bu ticket'ta bulunan ve düzeltilen tek gerçek eksikti.
- [x] **`.env.example`** — zaten jenerik placeholder'lar (`auth.example.com`, `CHANGE_ME_REALM`); eski `auth.dcs.hcu-hamburg.de`/`prod-realm` değeri hiç yok, grep zaten temiz.
- [x] **`.env.prod`** — repoda yok (deploy tarafında). **Deploy owner'a not:** prod ortamında da `KEYCLOAK_SERVER_URL=https://auth2.dcs.hcu-hamburg.de/`, `KEYCLOAK_JWKS_URL=.../realms/tosca-dev/protocol/openid-connect/certs`, `KEYCLOAK_ISSUER=.../realms/tosca-dev` olarak ayarlanmalı — bu ticket kapsamında kodda bir değişiklik yapılmadı, yalnız burada dokümante edildi.

## Doğrulanmış kararlar (canonical §12)

- ✅ Realm `prod-realm` → **`tosca-dev`** (auth2'de).
- ✅ Client adı **`django-dev`** aynı kalıyor.
- ✅ GeoServer OIDC login zaten çalışıyor (Part B = commit/provision + repoint, sıfırdan değil — ticket 13).

## ⚠️ Bu ticket'ta doğrulanacak (canonical §12 "Doğrulanacak" — kısmı burada)

- [x] **Django client `django-dev` public mi confidential mı?** Kod incelendi: `base.py`'deki `SOCIALACCOUNT_PROVIDERS["openid_connect"]["APPS"][0]["secret"]` zaten `KEYCLOAK_CLIENT_SECRET`'a bağlı (boş string değil), ve `.env.dev`'de dolu bir secret var → **confidential**, tutarlı. Ticket metnindeki "`secret": ""` çelişkisi kodun daha eski bir haliyle ilgiliydi; mevcut kodda çelişki yok.

## Acceptance criteria

- [x] `.env.dev`, `base.py`, `.env.example`'daki `auth` host'u `auth2`'ye ve `prod-realm` realm'i `tosca-dev`'e çevrildi — **hiçbir yerde eski değer kalmadı** (grep temiz, doğrulandı).
- [x] Client adı `django-dev` korundu.
- [x] `django-dev` public/confidential çelişkisi çözüldü; `KEYCLOAK_CLIENT_SECRET` ile `base.py` secret ayarı tutarlı.
- [x] `.env.prod` değişikliği deploy owner'a not olarak yazıldı (kodda değil) — bkz. yukarıdaki "Değişecek yerler" bölümü.
- [x] Lokal stack ayağa kalkar (`docker ps` ile doğrulandı, `manage.py check` temiz); gerçek login akışı canlı Keycloak doğrulaması ticket 12'nin kapsamı.

**Status: done** (2026-08-12). Tek gerçek kod değişikliği: `settings/base.py`'deki `KEYCLOAK_SERVER_URL`/`KEYCLOAK_REALM` default değerleri (env değişkeni set edilmediğinde kullanılan fallback) auth2/tosca-dev'e güncellendi — `.env.dev` zaten doğruydu ama default'lar prod-realm'de kalmıştı.

## Doğrulama

```
grep -rn "auth\.dcs\.hcu-hamburg\.de\|prod-realm" .env.dev .env.example tosca_api/settings/base.py   # boş dönmeli
make django-test-unit
```
Canlı token doğrulaması (mapper'lar) → ticket 12.

## Canonical atıfları
§12 tüm bölümler · §11 B2.
