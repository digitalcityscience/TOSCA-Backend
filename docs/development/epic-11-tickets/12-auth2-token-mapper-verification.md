# 12 — auth2/`tosca-dev` token mapper canlı doğrulama

**Track:** B · **Canonical:** §4 (canlı doğrulama notu), §12 "Doğrulanacak"

**What to build:** auth2/`tosca-dev` realm'inin token'a doğru claim'leri düşürdüğünü **canlı token ile** doğrula: `default_organization` (scalar), `ROLE_<SLUG>_*` (realm rolleri), `realm_access.roles`. Bu, A-track'in (03/04/05) ve C-track'in (08) çalışması için ön koşuldur — claim'ler gelmezse tüm org-scope sessizce boş kalır.

**Blocked by:** 11.

**Status:** blocked (11 bekliyor)

---

## Neden ayrı bir doğrulama ticket'ı

Canonical §4'teki canlı doğrulama **eski** Keycloak (`geo-client` login, 2026-08-12) üzerinde yapıldı ve `default_organization: "dcs"` token'da geliyordu. **auth2/`tosca-dev`'de mapper'ların aynı davrandığı garanti değil** (yeni sürüm, yeni realm). §12 bunu açıkça "Doğrulanacak" listesine koyuyor.

## Doğrulanacaklar (canonical §12 + §4)

- [ ] `default_organization` scalar claim token'a düşüyor mu? (`userinfo` **ve/veya** `id_token`).
- [ ] `realm_access.roles` içinde `ROLE_<SLUG>_*` org rolleri geliyor mu?
- [ ] Platform rolleri (`DJANGO_SUPERADMIN`, `DJANGO_STAFF`, `ADMIN`) `realm_access.roles`'ta geliyor mu?
- [ ] `organization` (çoklu-üyelik listesi) hâlâ **YOK** mu? (beklenen: yok — §4; varsa ticket 14/çoklu-org planı güncellenir.)

## Adımlar

1. Lokal stack auth2/`tosca-dev`'e bağlıyken (ticket 11) gerçek bir kullanıcıyla login ol.
2. `backends.py::KeycloakAdapter.pre_social_login`'e **geçici** log koy (canonical §4'teki yöntemin aynısı) ve gerçek `extra_data` / decoded token'ı incele. **Doğrulama sonrası logu KALDIR** (token içeriği loglanmaz).
3. Bulguları bu ticket'a ve gerekiyorsa canonical §4/§12'ye işле (claim adı, hangi token'da geldiği).
4. Claim eksikse: Keycloak tarafında (auth2/`tosca-dev`) ilgili **protocol mapper**'ı eklet/düzelt (Keycloak-tarafı iş; provisioning insan tarafından — §4b). Bu ticket bunu **tespit + talep** eder; Keycloak admin değişikliği gerekiyorsa not düş.

## Acceptance criteria

- [ ] Canlı token incelendi; `default_organization` + `ROLE_<SLUG>_*` + `realm_access.roles`'un gelip gelmediği **kanıtla** (log çıktısı özeti ticket'a eklenir, token'ın kendisi değil).
- [ ] Geçici log **kaldırıldı** (token içeriği kalıcı loglanmıyor).
- [ ] Eksik claim varsa Keycloak mapper eksikliği net tarif edildi (hangi mapper, hangi realm).
- [ ] `organization` array'inin varlık/yokluğu netleşti (çoklu-org planı için girdi).

## Doğrulama

Manuel: lokal login + log incelemesi. Otomatik test **yok** (canlı realm bağımlı); bulgular dokümante edilir.

## Canonical atıfları
§4 canlı doğrulama · §12 "Doğrulanacak" · §10a default_org login'i bloklamaz.
