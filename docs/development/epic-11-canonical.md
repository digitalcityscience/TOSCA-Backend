# Epic 11 — Canonical Kararlar (Tek Doğru Kaynak)

> **Bu belge, Epic 11'in auth + storage kararları için TEK canonical kaynaktır.**
> Aşağıdaki belgelerin çelişen/eskimiş kısımlarını **supersede eder**:
> - `epic-11-organization.md` "Decisions Already Made" içindeki projeksiyon-yönü ve Issue 51'in `KeycloakSyncService` yarısı
> - `epic-11_s3-production-media-roadmap.md` (storage yönü korunur; açık sorular §10'a taşındı)
>
> **Birleştirilmiş ve kaldırılmış belgeler (2026-08-12):** `epic-11-User_ACL_decision.md`, `epic-11-part1-oauth-plan.md`, `epic-11-auth2-migration-notes.md` — bu üç belgenin karar içeriği yukarıdaki bölümlere, uygulama planı §11'e ve migration notları §12'ye taşındı; belgeler silindi. `User_ACL_decision.md`'nin §5.0/§5.1 "Django canonical → Keycloak projection" kararı **ters çevrildi** (bkz. §1), `ROLE_ORG_*` isimlendirmesi **değişti** (bkz. §2).
>
> Bir çelişki olursa **bu belge kazanır.** Son güncelleme: 2026-08-12. Durum: auth kararları kesin; storage §10b'de açık.

---

## 1. Yetki modeli — tek ilke

**Keycloak kimliğin VE rol atamasının tek kaynağıdır. Django saf uygulayıcı + kaynak control-plane'idir (beyin). GeoServer, token'daki rolleri Django'nun yazdığı ACL kurallarıyla eşleştirir.**

Eski dokümanların "Django Membership canonical, Keycloak'a projekte eder" kararının **tam tersidir.** Sebep (kullanıcı kararı): rolü iki yerde yönetmek karmaşa yaratır; atama tek yerde (Keycloak) olsun, "o rol neyi yapabilir"i Django belirlesin ama rol *atamasına* karışmasın. **Django Keycloak'a yazmaz** (istisna yok — provisioning bile Keycloak'ta insan tarafından yapılır, bkz. §4b).

| Sistem | Sorumluluk | Yazma yönü |
|--------|-----------|-----------|
| **Keycloak** | Kimlik + org tanımı + realm-group + rol tanımı + rol/grup→kullanıcı ataması | — (canonical) |
| **Django** | "Bu rol neyi yapabilir" politikası; kaynak sahipliği (workspace/campaign → org); GeoServer ACL üretir; org/rol'ü Keycloak'tan okur/reconcile eder | Keycloak'a **yazmaz**; GeoServer ACL'ine **yazar** |
| **GeoServer** | Enforcement point: token rolünü, Django'nun yazdığı Data Security kuralıyla eşleştirir | — (tüketici) |

**Native Keycloak Organizations KULLANILIR ve ZORUNLUDUR:** her kullanıcı en az bir org'a üye olur, tam bir tanesi **default**'tur (bkz. §4).

---

## 2. Rol sözlüğü (konvansiyon)

Rol ismi **`ROLE_<SLUG>_<LEVEL>`** biçimindedir. `<SLUG>` = Django `Organization.slug` (büyük harf); `<LEVEL>` ∈ `{READER, WRITER, ADMIN}`. **`ORG` infix'i yoktur.**

```
Org roller (her org için üç tane):
  ROLE_DCS_READER
  ROLE_DCS_WRITER   (composite: READER'ı include eder)
  ROLE_DCS_ADMIN    (composite: WRITER'ı include eder)

Alt-proje (dcs altında X projesi = KENDİ Organization satırı, slug=dcs_x):
  ROLE_DCS_X_READER / ROLE_DCS_X_WRITER / ROLE_DCS_X_ADMIN

Platform rolleri (org-bağımsız):
  DJANGO_SUPERADMIN → Django superuser      (role_sync.py — VAR)
  DJANGO_STAFF      → Django is_staff       (role_sync.py — VAR; ADMIN'den taşındı)
  ADMIN             → GeoServer konsol admin (GS_ADMIN_ROLE — VAR, değişmez)
  GROUP_ADMIN       → GeoServer group admin  (GS_GROUP_ADMIN_ROLE — VAR)
```

> **Karar değişikliği (2026-08-12):** Platform superadmin rolü önceden `SUPERADMIN` (DJANGO_ öneki yok) olarak tanımlıydı; kose'deki gerçek rol her zaman `DJANGO_SUPERADMIN` idi. İki taraf uyuşmadığından superadmin kullanıcılar `/admin/`'e giremiyordu. Karar: **Keycloak tarafı değişmez** (rol adlarının hepsi `DJANGO_` önekiyle açık olsun istendi) — kod/ayar tarafı `DJANGO_SUPERADMIN`'i eşleştirecek şekilde güncellendi (`role_sync.py`, `settings/base.py` `KEYCLOAK_DJANGO_SUPERUSER_ROLES` default'u).

**Kurallar:**
- Django ismi **slug'dan ÜRETİR ve token'da EŞLEŞTİRİR — asla parse etmez.** `ROLE_DCS_X_READER` "org=DCS, alt=X" diye ayrıştırılmaz; `dcs_x` atomik bir slug'dır. Keycloak'ta karşılığı olan bir `Organization` yoksa o rol Django'da **hiçbir şey ifade etmez.**
- İnsan kuralı: *yeni rol açıyorsan her zaman `ROLE_<ORG>[_<ALTKIRILIM>]_<READER|WRITER|ADMIN>`, ve her rolün Django'da karşılık gelen bir Organization slug'ı olmalı.*
- **Çakışma çözümü:** Django staff artık `DJANGO_STAFF` ile keylenir, `ADMIN` ile değil (`KEYCLOAK_DJANGO_STAFF_ROLES=["DJANGO_STAFF"]`; `DJANGO_SUPERADMIN` zaten staff'ı ima eder). `ADMIN` yalnız GeoServer'a aittir.
- **🔒 GÜVENLİK KURALI:** Platform rolleri (`ADMIN`, `GROUP_ADMIN`, `DJANGO_SUPERADMIN`) **org-gruplarına ASLA map edilmez** — yalnızca kontrollü, realm-seviyesi manuel atama. Aksi halde bir org-admin kendi grubuna `ADMIN` ekleyip birini global GeoServer admin'i yapabilir (yetki yükseltme).

### 2a. Realm-group katmanı (rolleri paketler)

Her org, **realm-seviyesi group**'larla desteklenir; bu gruplar rolleri map eder:

```
realm group /dcs-readers → ROLE_DCS_READER
realm group /dcs-writers → ROLE_DCS_WRITER
realm group /dcs-admins  → ROLE_DCS_ADMIN
alt-proje:  /dcs-x-readers → ROLE_DCS_X_READER ...
```

- Kullanıcıyı oluştururken **grubu seçersin → roller otomatik gelir** (tek tek rol atamazsın). UX kazancı budur.
- **GeoServer grubu görmez; grubun ROLLERİNİ görür.** Zincir: `realm group → realm rol → token.realm_access.roles → GeoServer`. **Organization-scoped group'lar `organization` claim'ine düşer, GeoServer onu okumaz** — o yüzden haklar **realm** group'larıyla verilir, org-scoped group'larla değil.

### 2b. Rol yetenek tablosu

```
Seviye   Django API/admin              GeoServer ACL   Org üye yönetimi
------   ---------------------------   -------------   -------------------
READER   oku                           .r              —
WRITER   oluştur + düzenle (SİLME YOK) .w  (*)         —
ADMIN    oluştur + düzenle + SİL       .w              evet (kendi org'u)
```

**(\*) Kritik:** GeoServer `.w` create/edit/delete'i **ayıramaz** — WFS-T ile yazan siler de. "Writer silemez" kuralı **yalnız Django planında** geçerlidir; GeoServer planında writer=admin=`.w` (bu, §7'deki direct-write trade-off'unun parçası). Org-admin'in "diğer kullanıcıları yönetmesi" ileride Keycloak delegated-admin/FGAP ile gelir; şimdilik sözlükte `ROLE_<SLUG>_ADMIN` rolü var, delegasyon mekanizması sonraki iş.

Geoserver in bu kisiti kabul edilebilir. bizim acimizdan bir sorun yok. eger kullanici admin ise WFS-t ile islemin farkinda olmasi lazim. Biz gerekirse daha sonra bunu kapatiriz

---

## 3. GeoServer rol çözümleme — JWT AccessToken

- OIDC auth filter: `Role source = AccessToken`, `Roles claim = realm_access.roles`, `Principal key = preferred_username`.
- Keycloak `ROLE_<SLUG>_*` rollerini token'a claim olarak koyar; GeoServer bu string'leri Data Security kurallarıyla eşleştirir.
- **JDBC role service demote:** org rolleri için değil, yalnızca **break-glass** için (yerel `admin` kullanıcısı, token'sız, Keycloak çökse bile girebilen). `adminRoleName=ADMIN`, `groupAdminRoleName=GROUP_ADMIN` aynen kalır.
- **İş kalemi:** OIDC auth filter config'i şu an repoda **yok** (filter chain'de sadece basic/form/anonymous var). Commit + provision edilmeli.

---

## 4. Django veri modeli — minimal

**Tek yeni tablo: `Organization` (native Keycloak org'un aynası). Artı `Workspace`/`Campaign`'e `organization` FK. Kullanıcı/rol tablosu YOK (o Keycloak'ta).**

```python
class Organization(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid7)
    name = CharField()
    slug = SlugField(unique=True)              # rol ismini üretir: ROLE_<SLUG>_*
    keycloak_org_id = CharField(unique=True, null=True)   # native org id
    is_active = BooleanField(default=True)

Workspace.organization = FK(Organization, on_delete=PROTECT)
Workspace.visibility   = CharField(choices=[PRIVATE, PUBLIC], default=PRIVATE)
Campaign.organization  = FK(Organization, on_delete=PROTECT)
```

- **Her kullanıcı ≥1 org üyesi, tam 1 DEFAULT.**
- **default_org çözümü** — token'daki `default_organization` (scalar) claim'inden okunur; **canlı doğrulandı, çalışıyor** (bkz. aşağıdaki not):
  ```
  default_org = userinfo["default_organization"]     # veya id_token — scalar, doğrudan token'dan
  ```
  - Django cache'leyebilir ama **her login'de doğrular** (ilk-login-only DEĞİL — org değişirse bayatlar).
  - **Çoklu üyelik:** token'da `organization` array'i (birden çok org listesi) şu an YOK. Çoklu-org gerekince ya mapper'a `organization` array'i eklenir ya da Keycloak Admin API'den okunur — **yalnız o durumda**. default_org tek başına scalar claim'den gelir, fallback gerektirmez.

  > **✅ Canlı doğrulama (2026-08-12, `geo-client` login):** `pre_social_login`'e geçici log konup gerçek token incelendi. Bulgular:
  > - **default org claim token'da GELİYOR** — hem `userinfo` hem `id_token` içinde: `"default_organization": "dcs"`. Yani bu claim için mapper çalışıyor; birincil dalda **Admin API fallback'ine gerek yok**.
  > - **Claim adı `default_organization`** (`default_org` değil) — yukarıdaki pseudocode buna göre yazıldı: `userinfo["default_organization"]` (veya `id_token`).
  > - ⚠️ **`organization` (çoklu üyelik listesi) token'da YOK** — sadece scalar `default_organization` var. Dolayısıyla çoklu-org üyelik mantığı şu an token'dan **beslenemiyor**. Çoklu-org gerekince ya mapper'a `organization` array'i eklenmeli ya da o dal Admin API'ye kalır.
  > - Roller `id_token.realm_access.roles`'ta geliyor (ör. `DJANGO_SUPERADMIN`, `ADMIN`).
- **POC'ta `Membership(user, org, role)` tablosu YOK** — org, token'dan okunur. (default_org cache'i istersen küçük bir user-profile alanı olur; source Keycloak.)
- **Django built-in `Group`/`user_permissions` KULLANILMAZ** — model-CRUD/global; org-scope ifade edemez.

### 4b. Provisioning + Django↔Keycloak iki kanal

**Provisioning Keycloak-tarafı (insan):** bir org doğunca Keycloak'ta yaratılır — (a) native Organization, (b) `/<slug>-readers|writers|admins` realm grupları, (c) `ROLE_<SLUG>_*` roller + mapping'leri. **Django yaratmaz.**

**Django yalnız kaydeder + doğrular:** `Organization` satırı + `Workspace` açar; rolleri slug'dan **türetir** (`ROLE_<SLUG>_*`), GeoServer ACL'ine yazar; beklenen grup/rollerin Keycloak'ta var olduğunu **read-only reconcile** eder. Roller Django'da explicit tabloda saklanmaz (drift riski) — convention'dan türetilir, admin'de read-only gösterilir.

**İki iletişim kanalı:**
```
1. TOKEN (birincil)        — her istekte JWT claim'leri; request-time authz.
2. Keycloak Admin REST API — sunucu-sunucu (Django = confidential client + SERVICE ACCOUNT).
                             Token'da olmayan veri (reconcile, çoklu-org üyelik listesi) için.
```
→ Django'ya Keycloak **service account** (client-credentials) eklenmeli; Admin API sadece nadir işler için (reconcile, çoklu-org listesi), authz kararları için değil.

---

## 5. Enforcement akışları

`Organization` bir **sahiplik etiketidir**; Workspace/Campaign satırları ona FK ile bağlıdır. Dört yer bunu okur:

**(a) API list:**
```python
Workspace.objects.filter(organization__slug__in=user_org_slugs_from_token)
```

**(b) Django admin:** `get_queryset` + `has_change/delete_permission` non-superuser'ı kendi org'una kısar.

**(c) GeoServer ACL sync** (workspace kaydedilince senkron push):
```
PRIVATE:  <ws>.*.r = ROLE_<SLUG>_READER    <ws>.*.w = ROLE_<SLUG>_WRITER
PUBLIC :  <ws>.*.r = *  (anonymous)        <ws>.*.w = ROLE_<SLUG>_WRITER
```

> **✅ Canlı doğrulama (2026-08-12) — Django→GeoServer ACL yazımı ÇALIŞIYOR:** Çalışan GeoServer'da (`localhost:8080`, admin2/geoserver2, JDBC role service aktif) manuel test yapıldı. `db-data-test-rol` rolü seed edilip `hamburg:bezirke` layer'ına read-only kural REST ile eklendi:
> ```
> POST /geoserver/rest/security/acl/layers
> Content-Type: application/json
> {"hamburg.bezirke.r": "db-data-test-rol"}          → HTTP 200
> ```
> Sonuç kural seti: `{"*.*.r":"*", "*.*.w":"GROUP_ADMIN,ADMIN", "hamburg.bezirke.r":"db-data-test-rol"}`. Daha spesifik kural (`hamburg.bezirke.r`) global `*.*.r`'yi bu layer için ezer → read yalnız o role kısıtlanır. **Sonuçlar (Issue 51 / `GeoServerSecuritySyncService` için):**
> - Kural key formatı: `<workspace>.<layer>.<access>`, access ∈ `{r, w, a}`, değer = virgülle ayrılmış roller. `<layer>=*` ile workspace-geneli (§5c'deki `<ws>.*.r` şablonu birebir bu endpoint'e oturur).
> - **HTTP fiil semantiği:** yeni key → `POST`; var olan key'i güncelle → `PUT` (`POST` var olan key'de hata); sil → `DELETE /rest/security/acl/layers/{key}`. Sync servisi bu üçünü ayırmalı (idempotent push, §10a).
> - `geoserver-rest` bu ACL endpoint'lerini sarmıyor → mevcut `GeoServerClient._request()` (basic-auth raw REST) üzerinden gidilecek; client'a `add/update/delete_layer_rule` metodları eklenir (§8'deki "ACL metodu YOK" boşluğu tam olarak bu).

**(d) Login-check'ler** (mevcut `role_sync.py` login hook'unda):
- **Org-presence check:** `organization` claim boşsa → login'i bloklama (kimlik geçerli) ama org-scoped erişim kapalı + net uyarı: *"no organization assigned — contact your admin."* `DJANGO_SUPERADMIN`/`DJANGO_STAFF` muaf.
- **Org-role coherence check:** kullanıcı bir org'un üyesi ama o org'a ait hiç rolü yoksa (örn. dcs üyesi + sadece gq rolü) → uyarı + admin'in görebileceği bir yere log. (Keycloak'ta zorunluluk yok; yanlış-yapılandırmayı Django yakalar.) Erişim zaten rol-güdümlü, yani rolsüz üye fonksiyonel olarak erişemez; kontrolün değeri misconfig tespiti.

Tam zincir:
```
Keycloak: kose → /dcs-writers grubu   (atama — Keycloak'ta, insan yapar)
   │ login/token
   ▼
token.realm_access.roles = [ROLE_DCS_WRITER, ...]   +  default_organization=dcs
   ├─→ Django API/admin: token rolü + Workspace.organization FK → izin + login-check'ler
   └─→ GeoServer: OIDC filter rolü okur → Django'nun yazdığı dcs.*.w kuralıyla eşleşir
```

---

## 6. Workspace görünürlük & paylaşım

- **PRIVATE** (default): sadece sahip org.
- **PUBLIC**: anonymous read + owner-org write. Yalnız read'i genişletir, write asla.
- **Org'lar arası paylaşım (POC):** `shared_with` M2M **yok**. Paylaşım = **ayrı bir shared workspace** (sahip=paylaşan org), aynı PostGIS tablolarına ikinci bir GeoServer namespace publish eder (veri kopyalanmaz), ACL partner org'a reader verir; write hep sahip org'da. Layer-level paylaşım → **v2**.

---

## 7. GeoServer direct-write — bilinçli trade-off (korunur)

`ROLE_<SLUG>_WRITER`/`_ADMIN` GeoServer'a WFS-T ile doğrudan yazabilir/silebilir (Django'yu baypas eder). Bilinçli kabul: Issue 32 geometri kontrolü, Issue 8 audit, `MediaAsset` takibi direct-write'ı görmez. Sebep: Django upload akışı henüz her veri tipini kapsamıyor. İleride sıkılaştırma açık (write'ı tek Django service-account rolüne kilitle). `ADMIN` (GeoServer konsol) bundan ayrı, az kişiye verilen kaçış valfi.

---

## 8. Bugün ne VAR / ne SIFIRDAN gelecek (koda dayalı)

| Var | Yer / durum |
|-----|-------------|
| Keycloak rolü → is_staff/is_superuser | `authentication/role_sync.py` (sadece iki bayrak) |
| DRF Bearer auth | `authentication/backends.py::KeycloakTokenAuthentication` |
| GeoServer katalog ops | `geodata_providers/geoserver/client.py::GeoServerClient` (ACL metodu YOK) |
| Kaba public flag | `Layer.is_public` + `CatalogVisibilityService` |
| JDBC role service + ADMIN/GROUP_ADMIN seed | `docker/geoserver_docker/scripts/geoserver_configure_jdbc_security.py` |
| Native org + `organization` / `map-org-membership` claim'leri | Keycloak (tosca-dev) — token'da çalışıyor |

| Sıfırdan gelecek | Not |
|------------------|-----|
| `Organization` modeli + Workspace/Campaign FK + `Workspace.visibility` | Issue 1 (sadeleşti) + Issue 3 |
| `role_sync` genişletmesi: `ROLE_<SLUG>_*` oku + iki login-check | Issue 2 (okuma yönü) |
| default_org çözümü (token `default_organization` scalar claim) | yeni; çoklu-org listesi ileride Admin API'den |
| Org-scoped DRF permission class + admin scoping (yetenek tablosu §2b) | Issue 4/5/6 |
| **`GeoServerSecuritySyncService`** (Django → GeoServer Data Security ACL) | Issue 51 — **en kritik, hiç izi yok** |
| GeoServer OIDC auth filter config (commit + provision) | yeni iş kalemi |
| Keycloak provisioning: native org + realm gruplar + roller | Keycloak-tarafı, spike'la doğrula |
| Django Keycloak service account (Admin API/reconcile) | yeni |

---

## 9. Epic issue'larına etki

- **ACL §5.0/§5.1 ("Django canonical → Keycloak projection"): TERS ÇEVRİLDİ.** Keycloak canonical (atama), Django enforcer.
- **Rol isimlendirmesi `ROLE_ORG_<slug>_*` → `ROLE_<SLUG>_*`** (ORG infix kalktı, ADMIN seviyesi eklendi).
- **Issue 51'in `KeycloakSyncService` yarısı: SİLİNDİ.** Django Keycloak'a yazmaz. Geriye yalnız `GeoServerSecuritySyncService` kalır.
- **Issue 1 `Membership`: POC'tan çıktı.** `Organization` kalır; default_org opsiyonel cache.
- **Issue 2: okuma yönü + iki login-check** (Keycloak rolleri/org → Django enforcement).
- **Native Keycloak Organizations: KULLANILIYOR ve ZORUNLU** (org kayıt defteri + üyelik). Eski "gereksiz olabilir" notu geçersiz.

---

## 10. Kalan kararlar

### 10a. Karara bağlandı

- **Token TTL = 30 dk** (ACL doc ≤5dk önerisini supersede eder). Revoke edilen üyelik en fazla 30 dk GeoServer'da açık kalabilir — bilinçli. Anında revocation gerekirse: Keycloak back-channel logout / token revocation.
- **Cross-org erişim → 404** (403 değil).
- **ACL push başarısızlığı → basit `sync_status=dirty` + retry management command** (v1'de dağıtık framework/outbox YOK; idempotent push yeterli). ⚠️ **Override (2026-08-12, ticket 09):** ürün kararıyla bu terk edildi — push başarısız olursa Workspace save'i **hard-fail** eder (rollback), `dirty`/retry command yok. Detay ve gerekçe: `epic-11-tickets/09-acl-failure-path-dirty-and-retry-command.md`.
- **Realm-group ile rol yönetimi** (org-scoped group değil) — kullanıcı oluştururken grup seçimi, roller token'a düşer.
- **default_org**: token'ın `default_organization` (scalar) claim'inden okunur — canlı doğrulandı, çalışıyor (§4). Çoklu-org üyelik listesi token'da henüz yok; gerekince mapper'a `organization` array'i eklenir ya da Admin API'den okunur. Login'i bloklamaz.
- **İki login-check** (org-presence, org-role coherence) — bkz. §5(d).
- **Platform-rol güvenlik kuralı** — bkz. §2 🔒.
- **Platform-managed shared models — kalıcı ürün kararı (2026-08-19, org-scope-bypass audit ticket 05):**
  `EventType`, `TaxonomyDimension`, `TaxonomyTerm`, `GeoContext` bir org'a ait değildir (org FK yok;
  `GeoContext` özellikle `Event`/`EventSeries` üzerinden birden çok org'un içeriğine bağlanabilir —
  bkz. ticket 05 gerekçesi). Standart READER/WRITER/ADMIN merdiveni bu modeller için **geçici bir
  bugfix değil**, kalıcı ürün davranışıdır: `change`/`delete` **superuser-only**
  (`PlatformOnlyChangeDeleteMixin`, `GeodataEngineAdmin` ile aynı desen); `add` normal `has_perm()`
  merdiveninden geçer (yeni satır ekleyen org'un kendi kullanımını etkiler, paylaşılan satırları değil).
  Bir org WRITER/ADMIN'in bu paylaşılan referans verisini değiştirmesi/silmesi diğer org'ların içeriğini
  bozabileceği için kapatıldı — genişletilecek yeni model bu listeye eklenirken aynı soru sorulmalı:
  "org FK'sı var mı, yoksa platform-managed mi?"

### 10b. Storage (Q4 — bu grilling'de konuşulmadı, tamamen açık)

- Büyük geospatial dosyalar için **presigned direct-to-S3 upload** mı, Django-aracılı mı?
- Private originals + metadata-clean derivative pipeline (sync/async?).
- `MediaAsset` modeli (Issue 48) presigned akışı hesaba katarak mı tasarlanacak?
- SSRF hardening (upload-by-url), throttle rate'leri, proxy header'ları (Issue 10).

---

## 11. Uygulama Planı — Part 1: OAuth Entegrasyonu

> Onaylanan plan (2026-08-12 grilling). Kapsam: **A + B + C**. Storage (§10b) ve Keycloak service account/Admin API **hariç**.

### Zemin (karar verildi)

- Keycloak = `auth2.dcs.hcu-hamburg.de`, realm = `tosca-dev`, client = `django-dev`.
- Ortam = lokal docker stack. Test = `make django-test-unit` (docker `django`, `uv run pytest -m 'not integration'`).
- Org modeli → yeni app `tosca_api/apps/organizations`.
- Kullanıcı → **tek org** (`default_organization` scalar claim). Çoklu-org sonraya.
- Seed "default" org **slug = `dcs`**; mevcut Workspace/Campaign satırları backfill ile buna bağlanır.
- ACL push = model save (post_save signal) senkron; hata → hard-fail (§10a override, ticket 09) — `sync_status=dirty` + retry command **değil**.
- Cross-org erişim → 404. Token TTL 30 dk (§10a).
- GeoServer OIDC login zaten çalışıyor → B = commit/provision + auth2 repoint (bkz. §12).

### Sıra: A → C → B

**A — Django org-scope**
- [ ] A1. `organizations` app + `Organization` modeli (uuid7 pk, name, slug unique, keycloak_org_id unique null, is_active). `settings/base.py` INSTALLED_APPS'e ekle.
- [ ] A2. `Workspace`(geodata_providers) + `Campaign`(campaigns) → `organization` FK.
      Adım: (1) nullable FK migration, (2) `dcs` org seed + tüm eski satırları ona bağlayan data migration, (3) non-null + `on_delete=PROTECT`.
- [ ] A3. `role_sync` genişletmesi: `default_organization` → kullanıcının org slug'ı; `realm_access.roles` içinden `ROLE_<SLUG>_*` → seviye (READER/WRITER/ADMIN). Convention'dan türetir, DB'de tutmaz.
- [ ] A4. İki login-check (org-presence, org-role coherence): login'i bloklamaz; `messages` + `logger.warning`. DJANGO_SUPERADMIN/DJANGO_STAFF muaf.
- [ ] A5. Org-scoped DRF permission class + Django admin scoping (§2b: READER oku / WRITER oluştur+düzenle / ADMIN +sil). Cross-org → 404. `get_queryset` + `has_change/delete_permission`.
- [ ] A6. Unit testler (token fixture ile, infra gerekmez).

**C — GeoServer ACL yazma (çok kritik)**
- [ ] C1. `GeoServerClient`'a `add/update/delete_layer_rule` (raw REST, basic-auth; POST=yeni, PUT=güncelle, DELETE=sil — §5c doğrulandı).
- [ ] C2. `GeoServerSecuritySyncService`: Workspace save/visibility → post_save signal → senkron push. PRIVATE: `<ws>.*.r=ROLE_<SLUG>_READER`, `<ws>.*.w=ROLE_<SLUG>_WRITER`. PUBLIC: `<ws>.*.r=*`, `<ws>.*.w=ROLE_<SLUG>_WRITER`.
- [x] C3. Hata yolu: ~~`Workspace.sync_status` alanı + `dirty` işaretleme + `sync_acl` retry management command~~ → **hard-fail** (ürün kararı, ticket 09): push başarısızsa Workspace save'i rollback olur, `dirty`/retry command yok.
- [ ] C4. Integration test (gerçek GeoServer, `make django-test-integration`).

**B — GeoServer OIDC (hafif kalıntı)**
- [ ] B1. Çalışan OIDC auth filter config'ini repoya al (commit + provision script), auth2/`tosca-dev` realm'ine repoint. JDBC break-glass `ADMIN`/`GROUP_ADMIN` değişmez.
- [ ] B2. auth2 config switch: `.env.dev` + `base.py` + `.env.example` (bkz. §12).

### Mikro-kararlar
1. Seed org slug = `dcs` (mevcut veri dcs'e düşer).
2. Login-check uyarıları: `messages` + `logger.warning`; ayrı model/panel yok.
3. Seviye→izin: DRF permission class içinde convention-map; ayrı yetki DB'si yok.

---

## 12. Keycloak `auth` → `auth2` Geçiş Notları

> Yeni Keycloak: `https://auth2.dcs.hcu-hamburg.de` (yeni sürüm).
> Eski: `https://auth.dcs.hcu-hamburg.de` (artık kullanılmayacak).
> Bu, OAuth entegrasyonu (§11) yapılırken uygulanacak config değişikliklerinin listesidir.

### Değişecek yerler

- [ ] `.env.dev`
  - `KEYCLOAK_SERVER_URL=https://auth.dcs.hcu-hamburg.de/` → `auth2...`
  - `KEYCLOAK_JWKS_URL=https://auth.dcs.hcu-hamburg.de/realms/<realm>/...` → `auth2...`
  - `KEYCLOAK_ISSUER=https://auth.dcs.hcu-hamburg.de/realms/<realm>` → `auth2...`
- [ ] `tosca_api/settings/base.py:275` — `default="https://auth.dcs.hcu-hamburg.de/"` → `auth2...`
- [ ] `.env.example:189/195/196` — tutarlılık için güncelle
- [ ] `.env.prod` (repoda yok, deploy tarafında) — aynı üç değişken

### Doğrulanmış / karar

- ✅ **Realm adı `prod-realm` → `tosca-dev` DEĞİŞİYOR** (auth2'de realm = `tosca-dev`).
  Yani `.env.dev` + `base.py` + `.env.example`'daki tüm `realms/prod-realm/...` → `realms/tosca-dev/...`.
- ✅ Django client adı `django-dev` **aynı kalıyor**.
- ✅ **GeoServer OIDC login zaten çalışıyor** (Keycloak ile giriş + realm rolleriyle kullanıcı tanıma). Part B = çalışan filter config'i commit/provision + auth2'ye repoint; sıfırdan değil.

### Doğrulanacak

- [ ] Django client `django-dev` public mi confidential mı? (`.env.dev`'deki `KEYCLOAK_CLIENT_SECRET` vs base.py `"secret": ""` çelişkisi)
- [ ] `default_organization` + `ROLE_<SLUG>_*` + `realm_access.roles` mapper'ları auth2/tosca-dev'de token'a düşüyor mu? (canlı token ile doğrula)

### İlgili
- GeoServer OIDC plugin `.env.dev`: `COMMUNITY_PLUGINS=sec-oauth2-openid-connect` (zaten var).
- GeoServer OIDC auth filter'ın `issuer`/`jwks`/`client` ayarları da auth2'ye çevrilecek (Parça B).
