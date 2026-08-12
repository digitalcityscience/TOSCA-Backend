# 04 — İki login-check (org-presence, org-role coherence)

**Track:** A · **Canonical:** §5(d), §10a, §11 A4

**What to build:** Login'i **bloklamayan** iki tutarlılık kontrolü. Amaç: yanlış-yapılandırmayı (misconfig) Django'nun yakalaması. Erişim zaten rol-güdümlü; bu kontroller sadece uyarı + log üretir.

**Blocked by:** 03.

**Status:** ✅ done

---

## İki kontrol (canonical §5d)

1. **Org-presence check:** `default_organization` claim boşsa → login'i bloklama (kimlik geçerli) ama org-scoped erişim kapalı + net kullanıcı uyarısı: *"no organization assigned — contact your admin."*
2. **Org-role coherence check:** kullanıcı bir org'un üyesi ama o org'a ait hiç rolü yoksa → uyarı + admin'in görebileceği log.

**Muafiyet:** `DJANGO_SUPERADMIN` / `DJANGO_STAFF` her iki kontrolden muaf (`ORG_CHECK_EXEMPT_ROLES`).

## Mevcut durum (kod incelendi 2026-08-12)

`role_sync.py::run_org_login_checks(user, extracted_roles, extracted_org, *, request=None)` + `_emit_login_warning`. `backends.py::KeycloakAdapter._run_login_checks` login hook'undan çağırır. Uyarı kanalı: Django `messages` + `logger.warning` (mikro-karar §11.2 — ayrı model/panel yok).

## Acceptance criteria

- [x] Org-presence: claim boş + muaf değil → login geçer, `messages` + `logger.warning`, org-scoped erişim yok.
- [x] Org-role coherence: org üyesi ama org rolü yok → uyarı + log.
- [x] `DJANGO_SUPERADMIN`/`DJANGO_STAFF` her iki kontrolden muaf.
- [x] Hiçbir durumda login **bloklanmaz**.

## Doğrulama

```
make django-test-unit
```
> Not: login-check'ler için **birim testleri henüz yok** → ticket 06 kapsar.
