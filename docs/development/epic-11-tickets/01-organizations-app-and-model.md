# 01 — `organizations` app + `Organization` modeli

**Track:** A (Django org-scope) · **Canonical:** §4, §11 A1

**What to build:** Native Keycloak Organization'ın Django aynası olan tek tablo. Kullanıcı/rol tablosu YOK (o Keycloak'ta) — bu sadece bir **sahiplik etiketi** (ownership label). Slug'dan `ROLE_<SLUG>_*` rol isimlerini türetir.

**Blocked by:** None — can start immediately.

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

Uygulandı. `tosca_api/apps/organizations/` app'i mevcut:
- `models.py::Organization` — `TimeStampedModel`; alanlar: `id` (uuid7 pk), `name`, `slug` (unique), `keycloak_org_id` (unique, null), `is_active`. Helper property'ler: `role_prefix`, `reader_role`, `writer_role`, `admin_role`.
- `admin.py::OrganizationAdmin` — read-only alanlarla (drift riski yok; roller convention'dan türetilir, §4b).
- `apps.py`, `migrations/0002_seed_dcs_and_backfill.py` (bkz. ticket 02).
- `settings/base.py` INSTALLED_APPS'e eklendi.

## Ek: login-time auto-provisioning (2026-08-12, sonradan eklendi)

Org satırının Django'da nasıl açılacağı ticket 01/02 yazılırken netleşmemişti (yalnız seed `dcs` migration'la elle açılmıştı). Pratikte gerçek Keycloak login'i test edilince şu ihtiyaç çıktı: kullanıcı Keycloak'ta bir org'a atanmış + login olabiliyorsa, Django'da o org'un satırı **otomatik** olmalı — aksi halde "kimliği geçerli ama org-scoped erişimi kapalı" gibi gereksiz bir ara durum oluşuyor.

Uygulandı: `organizations/services.py::get_or_create_organization(slug)` — token'daki `default_organization` claim'i geldiğinde (`role_sync.extract_org_from_*`), slug DB'de yoksa `Organization(slug=slug, name=slug.upper())` oluşturulur; varsa dokunulmaz. Çağrı noktaları: `authentication/backends.py` — hem `KeycloakTokenAuthentication.authenticate` (API) hem `KeycloakAdapter._run_login_checks` (browser/allauth).

**Bunun ticket 14 (Admin API/service account, backlog) ile ilgisi yok** — bkz. o dosyadaki not. Bu sadece token'da zaten mevcut scalar claim'i mirror'lıyor, Keycloak'a hiçbir yazma yok, service account gerekmiyor.

## Acceptance criteria

- [x] `organizations` app'i `INSTALLED_APPS`'te.
- [x] `Organization(id=uuid7, name, slug unique, keycloak_org_id unique null, is_active default True)`.
- [x] Slug'dan rol türeten helper'lar: `ROLE_<UPPER(SLUG)>_READER|WRITER|ADMIN`.
- [x] Admin'de roller **read-only** gösterilir, DB'de explicit saklanmaz.
- [x] Kullanıcı/rol/`Membership` tablosu **YOK** (canonical §4: POC'ta Membership çıktı).

## Doğrulama

```
make django-test-unit   # migration + model import hatasız
```
