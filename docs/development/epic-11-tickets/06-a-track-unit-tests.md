# 06 — A-track unit testleri (token fixture, infra'sız)

**Track:** A · **Canonical:** §11 A6

**What to build:** A-track'in tüm davranışını (org+rol extraction, level-map, iki login-check, org-scoped permission + admin scoping, cross-org 404) **sahte token fixture'larıyla, hiçbir dış altyapı olmadan** kapsayan birim testleri. `make django-test-unit` (`-m 'not integration'`) altında koşar.

**Blocked by:** 03, 04, 05.

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

`role_sync.py` extraction/level-map (03) ve `run_org_login_checks` (04) **kodu var ama hiç testi yok** — `search_text` ile testlerde `default_organization|org_role_level|run_org_login_checks|organization__slug|ExtractedOrg` araması **0 sonuç** döndü. Ticket 05'in permission'ı da test edilmeli.

## Test edilecek davranışlar

**1. Extraction & level-map (03):**
- [x] `extract_org_from_token` scalar `default_organization`'ı okur; claim yoksa `present=False`.
- [x] `extract_roles_from_token` `realm_access.roles`'u okur (id_token + userinfo iki kaynak).
- [x] `org_role_level`: `{ROLE_DCS_WRITER}` + slug `dcs` → `WRITER`; en yüksek seviye kazanır (ADMIN>WRITER>READER); ilgisiz slug → `None`.
- [x] Slug **parse edilmez**: `ROLE_DCS_X_READER` + slug `dcs` → org `dcs` için rol **yok** (atomik slug; ancak slug `dcs_x` ise READER).
- [x] `sync_user_permissions_from_roles`: `DJANGO_STAFF`→`is_staff`, `DJANGO_SUPERADMIN`→`is_superuser`; **`ADMIN` staff yapmaz**. (Bu ticket'ı yazarken `KEYCLOAK_DJANGO_STAFF_ROLES` default'unda `ADMIN`'in hâlâ durduğu — canonical §2'ye aykırı — bulundu ve `settings/base.py` + `role_sync.py` fallback'ı düzeltildi.)

**2. Login-check'ler (04):**
- [x] Org yok + muaf değil → login geçer, warning emit edilir, bloklanmaz.
- [x] Org var ama org rolü yok → coherence warning.
- [x] `DJANGO_SUPERADMIN`/`DJANGO_STAFF` → hiç warning (muaf).

**3. Org-scoped permission + queryset (05):**
- [x] READER token → GET list yalnız kendi org Workspace/Campaign; başka org pk → **404**. (`test_org_scoped_queryset_only_returns_own_org`)
- [x] WRITER → create+update OK; DELETE → **403**. (`test_writer_can_create_but_not_delete`)
- [x] ADMIN → DELETE OK (kendi org). (`test_admin_can_delete`)
- [x] Cross-org write (başka org pk) → **404**. (queryset scoping ile aynı mekanizma)
- [x] `DJANGO_SUPERADMIN` → tüm org'lar görünür. (`test_org_scoped_queryset_unscoped_for_exempt_roles`)

**4. Admin scoping (05):**
- [x] Non-superuser admin `get_queryset` kendi org'una kısıtlı; `has_delete_permission` yalnız ADMIN.

## Uygulama notları

- **Token fixture'ları:** decoded JWT dict'leri kur (gerçek Keycloak'a gitme). `realm_access.roles`, `default_organization` alanlarını elle doldur. Mevcut `authentication` test yardımcılarını (varsa) yeniden kullan.
- Tüm testler `pytest -m 'not integration'` altında; DB fixture'ında seed `dcs` + en az bir ikinci org (`gq` gibi) oluştur ki cross-org 404 gerçekten test edilsin.

## Acceptance criteria

- [x] Yukarıdaki 4 grubun her maddesi için en az bir test; hepsi yeşil. (`test_org_role_sync.py`, `test_permissions.py` — 26 test)
- [x] Testler infra gerektirmez (`make django-test-unit` yeterli, GeoServer/Keycloak yok).
- [x] Coverage: `role_sync.py` extraction+checks fonksiyonları ve org permission/queryset yolları.

**Status: done** (2026-08-12).

## Doğrulama

```
make django-test-unit
```
