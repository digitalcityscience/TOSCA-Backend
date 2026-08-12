# 05 — Org-scoped DRF permission class + Django admin scoping

**Track:** A · **Canonical:** §2b (yetenek tablosu), §5(a)(b), §10a (cross-org → 404), §11 A5

**What to build:** Bir kullanıcı, API'de ve Django admin'de **yalnız kendi org'unun** Workspace/Campaign satırlarını görebilir ve seviyesine göre değiştirebilir. READER okur; WRITER oluşturur+düzenler (silmez); ADMIN +siler. Başka org'un kaynağına erişim **404** döner (403 değil — varlığı ifşa etme). `DJANGO_SUPERADMIN`/`DJANGO_STAFF` her org'u görür.

**Blocked by:** 01, 02, 03.

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

- ✅ Var: `Organization` (01), Workspace/Campaign `organization` FK (02), token'dan org+level okuma (`org_role_level`, `extract_org_from_*`, 03).
- ❌ Yok: org-scoped DRF permission class; org-scoped admin `get_queryset` / `has_change_permission` / `has_delete_permission`. `search_symbols` "org scoped permission" → yalnız mevcut syncer'lar döndü, permission izi yok.

Bu ticket A-track'in **son kod parçası**.

---

## §2b yetenek tablosu (uygulanacak eşleme)

```
Seviye   API / admin                          silme
READER   list + retrieve (GET)                hayır
WRITER   + create + update/partial (POST/PUT/PATCH)   HAYIR
ADMIN    + destroy (DELETE)                   evet
```
> GeoServer tarafında writer=admin=`.w` (silme ayrılamaz, §7) — ama **Django planında** writer silemez; bu kural burada, Django katmanında zorlanır.

## Adımlar

1. **Kullanıcının org+level'ını request'e taşı.** Login/authenticate anında (bkz. `backends.py::KeycloakTokenAuthentication` ve `KeycloakAdapter`) çıkarılan `default_organization` slug'ı ve `org_role_level` sonucunu request-time erişilebilir yap (örn. `request.user` üstünde cache'lenmiş bir attribute ya da DRF auth'un döndürdüğü `(user, auth)` context'i). **DB'de saklama** — convention'dan türet (§4b).
2. **DRF permission class** — yeni `apps/organizations/permissions.py::OrgScopedPermission` (veya mevcut permissions modülüne ekle):
   - `has_permission`: SAFE_METHODS → READER+; POST/PUT/PATCH → WRITER+; DELETE → ADMIN. Muaf roller (`DJANGO_SUPERADMIN`/`DJANGO_STAFF`) her şeyi geçer.
   - `has_object_permission`: `obj.organization.slug == user_org_slug` değilse → erişim yok. Cross-org'da **404** üretmek için object-level'da `False` döndürmek 403 verir; 404 semantiği için queryset-scoping'e güven (aşağı) — obj zaten queryset'te yoksa DRF 404 verir.
3. **Queryset scoping (§5a) — cross-org 404'ün asıl mekanizması:** ilgili ViewSet'lerin `get_queryset`'i:
   ```python
   Workspace.objects.filter(organization__slug__in=user_org_slugs_from_token)
   ```
   Muaf roller full queryset. Böylece başka org'un pk'si **hiç bulunmaz → 404** (canonical §10a).
4. **Django admin scoping (§5b)** — Workspace/Campaign (ve gerekiyorsa org-bağlı diğer) `ModelAdmin`:
   - `get_queryset`: non-superuser'ı kendi org'una kısar.
   - `has_change_permission` / `has_delete_permission`: obj varsa org eşleşmesi + seviye (WRITER değişir, ADMIN siler).
5. **ViewSet'lere permission'ı bağla.** Workspace, Campaign ve bunlara bağlı org-scoped endpoint'lere `permission_classes`. Hangi ViewSet'lerin org-scoped olduğunu belirle (Workspace/Campaign FK'si olanlar).
6. Cross-org write denemesi de **404** (obj queryset'te yok). Aynı-org ama yetersiz seviye (ör. WRITER delete) → **403** (permission red).

## Acceptance criteria

- [x] `OrgScopedPermission` oluşturuldu: SAFE=READER, write=WRITER, delete=ADMIN; muaf roller bypass. (`apps/organizations/permissions.py`)
- [x] Org-scoped ViewSet'lerin `get_queryset`'i `organization__slug__in=<token org slug'ları>` ile filtreler. (tek org: `default_organization`; `org_scoped_queryset()` — Workspace + Campaign ViewSet'lerine bağlandı)
- [x] Başka org'un objesine GET/PUT/DELETE → **404** (403 değil). (queryset scoping ile; test: `test_org_scoped_queryset_only_returns_own_org`)
- [x] Aynı org, yetersiz seviye (WRITER→DELETE) → **403**. (test: `test_writer_can_create_but_not_delete`)
- [x] Django admin: non-superuser yalnız kendi org'unun satırlarını görür; change=WRITER, delete=ADMIN. (`OrgScopedAdminMixin`, `WorkspaceAdmin`/`CampaignAdmin`)
- [x] `DJANGO_SUPERADMIN`/`DJANGO_STAFF` tüm org'ları görür/değiştirir.
- [x] Level/permission eşlemesi convention'dan türetilir; ayrı yetki DB'si **yok** (mikro-karar §11.3). (Django Permission/Group senkronu da bilinçli olarak yok — `OrgScopedAdminMixin._is_active_staff`)

**Status: done** (2026-08-12). Ek not: `Workspace`/`Campaign` create akışlarında `organization` alanı zorunlu olduğundan (ticket 02), create yollarına (`WorkspaceService.create_workspace`, `WorkspaceViewSet.create`, `CampaignViewSet.perform_create`, ilgili admin `save_model`'lar) `resolve_write_organization()` ile organizasyon ataması da eklendi — aksi halde WRITER create testleri hiç geçemezdi.

## Doğrulama

```
make django-test-unit
```
> Not: Testler ticket 06'da yazılıyor. Bu ticket'ta en azından mevcut suite'in kırılmadığını doğrula; asıl kapsama 06.

## Canonical atıfları
§2b yetenek tablosu · §5(a) API list filter · §5(b) admin scoping · §10a cross-org→404.
