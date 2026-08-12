# 03 — `role_sync`: `default_organization` + `ROLE_<SLUG>_*` okuma + level map

**Track:** A · **Canonical:** §4, §5(d), §8, §11 A3

**What to build:** Login/token anında Keycloak token'ından **okuma yönü**: kullanıcının `default_organization` scalar claim'i → org slug; `realm_access.roles` içinden `ROLE_<SLUG>_*` → effective level (READER/WRITER/ADMIN). Convention'dan türetilir, **DB'de tutulmaz** (drift riski, §4b). Django Keycloak'a yazmaz.

**Blocked by:** 01.

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

`authentication/role_sync.py` içinde uygulandı:
- `ExtractedRoles(roles, authoritative, sources)`, `ExtractedOrg(default_slug, present, sources)` dataclass'ları.
- `extract_roles_from_token` / `extract_roles_from_social_data` — `realm_access.roles` okur.
- `extract_org_from_token` / `extract_org_from_social_data` — scalar `default_organization` okur.
- `org_role_level(roles, org_slug)` — rol setinden `ROLE_<SLUG>_*` seviyesini döndürür (`ORG_ROLE_LEVELS = ("READER","WRITER","ADMIN")`).
- `sync_user_permissions_from_roles` — `DJANGO_STAFF`→`is_staff`, `DJANGO_SUPERADMIN`→`is_superuser` (canonical §2 çakışma çözümü: `ADMIN` artık staff'ı ima etmez).
- `ORG_CHECK_EXEMPT_ROLES = frozenset({"DJANGO_SUPERADMIN", "DJANGO_STAFF"})`.
- `backends.py::KeycloakAdapter.pre_social_login` bunları çağırır (login hook).

## Acceptance criteria

- [x] Token/social-data'dan `realm_access.roles` okunur (iki kaynak: id_token + userinfo).
- [x] Scalar `default_organization` claim'i okunur; claim adı **`default_organization`** (canlı doğrulandı §4).
- [x] `org_role_level` slug'dan `ROLE_<SLUG>_*` türetip en yüksek seviyeyi döndürür; **slug parse edilmez**.
- [x] Staff/superuser bayrakları **yalnız** authoritative kaynaktan senkronlanır; `ADMIN` staff yapmaz.
- [x] Hiçbir yazma Keycloak'a gitmez; roller Django DB'sinde explicit saklanmaz.

## Notlar / açık uçlar (ticket 12'de doğrulanacak)

- Çoklu-org üyelik listesi token'da **YOK** — sadece scalar `default_organization`. Çoklu-org gelince ticket 14.

## Güncelleme (2026-08-12): claim şekli canonical'da yazılandan farklı çıktı, düzeltildi

Gerçek Keycloak login'i (`kose`, realm `tosca-dev`) canlı izlendiğinde token'da **`default_organization` scalar claim'i hiç gelmiyor**; onun yerine:
```
"organization": ["gq2"],
"map-org-membership": {"gq2": {"id": ..., "groups": [...], "realm_access": {"roles": [...]}}}
```
— yani `organization` **liste** claim'i (Keycloak-tarafında mapper değişmiş/eklenmiş). `_extract_org_from_payloads` (`role_sync.py`) artık ikisini de destekliyor: önce eski `default_organization` scalar'ı, yoksa `organization` array'inin **ilk elemanını** default org olarak okuyor (`_org_slug_from_payload` helper'ı). Çoklu-org UI/mantığı hâlâ yok (tek eleman alınıyor) — canonical §4/ticket 14 kapsamı bu noktada değişmedi, sadece **claim'in okunma şekli** genişledi.

`epic-11-canonical.md:122`'deki "canlı doğrulandı, `default_organization: dcs` geliyor" notu **artık güncel değil** — o tarihten sonra Keycloak mapper config'i değişmiş görünüyor.

## Doğrulama

```
make django-test-unit
```
> Not: extraction/level-map için **birim testleri henüz yok** → ticket 06 kapsar.
