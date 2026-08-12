# 12 — auth2/`tosca-dev` token mapper canlı doğrulama

**Track:** B · **Canonical:** §4 (canlı doğrulama notu), §12 "Doğrulanacak"

**What to build:** auth2/`tosca-dev` realm'inin token'a doğru claim'leri düşürdüğünü **canlı token ile** doğrula: `default_organization` (scalar), `ROLE_<SLUG>_*` (realm rolleri), `realm_access.roles`. Bu, A-track'in (03/04/05) ve C-track'in (08) çalışması için ön koşuldur — claim'ler gelmezse tüm org-scope sessizce boş kalır.

**Blocked by:** 11.

**Status:** ✅ done (2026-08-12, `kose` + `geo-client`, auth2/`tosca-dev`) — bulgular aşağıda. `[ORG-DEBUG]` print'leri kaldırıldı (`role_sync.py`, `backends.py`, `organizations/services.py`); mevcut `logger.info`/`logger.warning` çağrıları zaten aynı bilgiyi (claim adı, kaynak, org slug) token içeriğini loglamadan taşıyor.

---

## Sonuç (2026-08-12 canlı doğrulama — `kose` ve `geo-client`, auth2/`tosca-dev`)

`backends.py::KeycloakAdapter.pre_social_login`'e konan geçici loglarla (+ `role_sync.py` içindeki extraction logları) gerçek token'lar incelendi:

- ❌ **`default_organization` (scalar) claim'i GELMİYOR** — ne `kose` ne `geo-client` için. §4'teki eski "canlı doğrulandı, `dcs` geliyor" notu **artık geçersiz**; auth2/`tosca-dev` mapper config'i o doğrulamadan sonra değişmiş.
- ✅ **`organization` claim'i geliyor ama LİSTE olarak**: `"organization": ["gq2"]` (`kose` için). Beklenenin (§4: "yok, gerekince eklenir") tam tersi — zaten eklenmiş.
- ✅ Ek olarak `map-org-membership` claim'i de geliyor: `{"gq2": {"id": ..., "groups": ["/kose-org-group"], "realm_access": {"roles": ["kose-rol-test", "ADMIN"]}}}` — org-scoped grup/rol bilgisini token içinde taşıyor. **Bu claim şu an kullanılmıyor**, ileride çoklu-org/grup mantığı gerekirse girdi olabilir.
- ✅ `realm_access.roles` içinde platform rolleri geliyor (`DJANGO_SUPERADMIN` `kose` için — ayrıca bkz. commit `b71c4a0`).
- ✅ Hem `userinfo`/`id_token` (allauth `extra_data`) hem decode edilmiş `access_token`'da aynı claim'ler mevcut.

**Yapılan kod düzeltmesi:** `role_sync.py::_extract_org_from_payloads` artık iki şekli de destekliyor — önce `default_organization` scalar'a bakıyor, yoksa `organization` array'inin ilk elemanını org slug olarak alıyor (`_org_slug_from_payload`). Bkz. ticket 03 "Güncelleme" notu.

**Kapanış notu (2026-08-12):** `[ORG-DEBUG]` print'leri kaldırıldı (`role_sync.py::_social_login_payloads`/`_extract_org_from_payloads`, `backends.py::pre_social_login`/`_run_login_checks`, `organizations/services.py::get_or_create_organization`). `backends.py:147`'deki `raw extra_data={extra_data!r}` print'i özellikle önemliydi — tüm token payload'ını loglama riski taşıyordu. Kalan `logger.info`/`logger.warning` çağrıları claim adı/kaynağı/org slug'ı yapılandırılmış şekilde loglamaya devam ediyor, token içeriğini değil.

---

## Neden ayrı bir doğrulama ticket'ı

Canonical §4'teki canlı doğrulama **eski** Keycloak (`geo-client` login, 2026-08-12) üzerinde yapıldı ve `default_organization: "dcs"` token'da geliyordu. **auth2/`tosca-dev`'de mapper'ların aynı davrandığı garanti değil** (yeni sürüm, yeni realm). §12 bunu açıkça "Doğrulanacak" listesine koyuyor.

## Doğrulanacaklar (canonical §12 + §4)

- [x] `default_organization` scalar claim token'a düşüyor mu? → **HAYIR**, gelmiyor (bkz. Sonuç).
- [x] `realm_access.roles` içinde `ROLE_<SLUG>_*` / platform org rolleri geliyor mu? → evet (`kose`: `DJANGO_SUPERADMIN`; test verisinde ayrıca `kose-rol-test`, `ADMIN`).
- [x] Platform rolleri (`DJANGO_SUPERADMIN`, `DJANGO_STAFF`, `ADMIN`) `realm_access.roles`'ta geliyor mu? → evet.
- [x] `organization` (çoklu-üyelik listesi) hâlâ **YOK** mu? → **VAR** (beklenenin tersi), liste olarak (`["gq2"]`); kod buna göre güncellendi.

## Adımlar

1. Lokal stack auth2/`tosca-dev`'e bağlıyken (ticket 11) gerçek bir kullanıcıyla login ol.
2. `backends.py::KeycloakAdapter.pre_social_login`'e **geçici** log koy (canonical §4'teki yöntemin aynısı) ve gerçek `extra_data` / decoded token'ı incele. **Doğrulama sonrası logu KALDIR** (token içeriği loglanmaz).
3. Bulguları bu ticket'a ve gerekiyorsa canonical §4/§12'ye işле (claim adı, hangi token'da geldiği).
4. Claim eksikse: Keycloak tarafında (auth2/`tosca-dev`) ilgili **protocol mapper**'ı eklet/düzelt (Keycloak-tarafı iş; provisioning insan tarafından — §4b). Bu ticket bunu **tespit + talep** eder; Keycloak admin değişikliği gerekiyorsa not düş.

## Acceptance criteria

- [x] Canlı token incelendi; bulgular yukarıda (Sonuç bölümü).
- [x] Geçici log **kaldırıldı** (bkz. Sonuç → "Kapanış notu").
- [x] Eksik claim (`default_organization`) net: mapper artık bu adı basmıyor, onun yerine `organization` (liste) + `map-org-membership` basıyor. Keycloak tarafında bunun kasıtlı bir mapper değişikliği mi yoksa yanlışlıkla mı olduğu **doğrulanmadı** — takip gerekebilir.
- [x] `organization` array'i **var** (beklenenin tersi) — ticket 03'e işlendi, kod bunu okuyor.

## Doğrulama

Manuel: lokal login + log incelemesi. Otomatik test **yok** (canlı realm bağımlı); bulgular dokümante edilir.

## Canonical atıfları
§4 canlı doğrulama · §12 "Doğrulanacak" · §10a default_org login'i bloklamaz.
