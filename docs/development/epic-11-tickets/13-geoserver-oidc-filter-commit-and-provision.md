# 13 — GeoServer OIDC auth filter config commit + provision + auth2 repoint

**Track:** B · **Canonical:** §3, §8, §11 B1, §12

**What to build:** GeoServer'ın halihazırda **çalışan** OIDC auth filter config'ini repoya alıp (commit) provision script'ine dahil et, ve auth2/`tosca-dev` realm'ine repoint et. Şu an filter chain repoda sadece basic/form/anonymous içeriyor — çalışan OIDC config commit edilmemiş (§3 "İş kalemi").

**Blocked by:** 11.

**Status:** blocked (11 bekliyor)

---

## Mevcut durum (canonical §3, §8, §12)

- ✅ GeoServer OIDC login **zaten çalışıyor** (Keycloak ile giriş + realm rolleriyle tanıma). Sıfırdan değil.
- ✅ OIDC plugin mevcut: `.env.dev` `COMMUNITY_PLUGINS=sec-oauth2-openid-connect`.
- ✅ JDBC role service + `ADMIN`/`GROUP_ADMIN` seed script'i var: `docker/geoserver_docker/scripts/geoserver_configure_jdbc_security.py`.
- ❌ **OIDC auth filter config repoda YOK** (filter chain'de sadece basic/form/anonymous). Commit + provision edilmeli (§3, §8 "yeni iş kalemi").
- `git status`: `docker/geoserver_docker` değişiklik altında — kısmen başlamış olabilir; tamamla ve tutarlılaştır.

## GeoServer OIDC filter beklenen ayarları (canonical §3)

- `Role source = AccessToken`
- `Roles claim = realm_access.roles`
- `Principal key = preferred_username`
- issuer / jwks / client → **auth2/`tosca-dev`'e çevrilir** (§12 "İlgili": filter'ın issuer/jwks/client ayarları da auth2'ye).
- **JDBC break-glass değişmez:** `adminRoleName=ADMIN`, `groupAdminRoleName=GROUP_ADMIN` aynen kalır (yerel `admin` kullanıcısı, token'sız, Keycloak çökse bile girebilen — §3).

## Adımlar

1. Çalışan GeoServer instance'ından OIDC auth filter config'ini (security/filter/... XML) **repoya al** — `docker/geoserver_docker` altındaki provision/config yapısına yerleştir.
2. Filter chain'e OIDC filter'ı ekle (basic/form/anonymous yanına), doğru sırayla.
3. issuer/jwks/client ayarlarını auth2/`tosca-dev`'e repoint et; `Role source`/`Roles claim`/`Principal key` §3'teki gibi.
4. JDBC break-glass seed'ine (`geoserver_configure_jdbc_security.py`) **dokunma** — `ADMIN`/`GROUP_ADMIN` korunur.
5. Provision script'in temiz bir GeoServer'ı sıfırdan bu config'le ayağa kaldırdığını doğrula (idempotent).

## Acceptance criteria

- [ ] OIDC auth filter config repoda (commit'li) ve provision script'ine dahil.
- [ ] Filter `Role source=AccessToken`, `Roles claim=realm_access.roles`, `Principal key=preferred_username`.
- [ ] issuer/jwks/client auth2/`tosca-dev`'e repoint edilmiş.
- [ ] JDBC break-glass `ADMIN`/`GROUP_ADMIN` değişmemiş.
- [ ] Temiz stack: `make` ile GeoServer ayağa kalkar, OIDC login token rollerini okur (ticket 08'in yazdığı ACL kurallarıyla eşleşir).
- [ ] Kullanıcı auth2 ile GeoServer'a girip kendi org layer'ını görebilir; başka org'unkini göremez (ticket 08 + 13 birlikte uçtan uca).

## Doğrulama

Manuel/stack: `make` ile stack ayağa kaldır, auth2 kullanıcısıyla GeoServer OIDC login, token rolüyle Data Security eşleşmesini gözle. (Otomatik ACL doğrulaması ticket 10.)

## Canonical atıfları
§3 GeoServer rol çözümleme (JWT AccessToken) · §8 "OIDC auth filter config yok" · §11 B1 · §12 "İlgili".
