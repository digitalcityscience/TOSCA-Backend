# TOSCA — Geodata Providers Admin Task Takip Dokumani

**Hedef app:** `geodata_providers`  
**Admin prefix:** `/admin/geodata_providers/`  
**API prefix:** `/api/`  
**Son guncelleme:** 20 Nisan 2026

---

## Durum Gostergesi

- `✅` Tamamlandi
- `🔄` Devam ediyor
- `⬜` Baslanmadi

---

## Amac ve Kapsam

Bu dokumanin amaci, `geodata_providers` app'i icin PoC seviyesinde ama guvenilir bir Django Admin gelistirme backlog'u olusturmak.

Bu iteration'da hedef:

- Ayrica bir `geo console` uygulamasi yazmamak
- Mevcut operasyonel mantigi Django Admin altina tasimak
- Ilk engine tipi olarak `GeoServer` ile entegrasyonu tam ve guvenilir hale getirmek
- `Engine -> Workspace -> Store -> Layer` sirasiyla gelismek
- UI/template yatirimini minimumda tutup fonksiyonellige odaklanmak

### Bu calisma ne degil

- Ayrica React disinda yeni bir console UI gelistirme isi degil
- Bu fazda yeni bir admin theme/design calismasi degil
- Async queue, Celery, job runner ilk fazin parcasi degil
- Tum provider tiplerini ayni anda tam destekleme isi degil

Bu PoC'de ilk hedef provider:

- `GeoServer`

---

## Sync Felsefesi

Bu kisim pazarliga acik degil. Tum CRUD akislari asagidaki sira ile calismali:

| Islem | Akis |
|------|------|
| CREATE | Remote pre-check -> remote create -> remote verify -> Django persist |
| UPDATE | Remote minimal update -> remote verify -> Django update |
| DELETE | Remote delete -> remote verify -> Django delete |
| PULL SYNC | Remote state -> Django DB reconciliation |

### Kurallar

- Sadece `200 OK` dondu diye islem basarili sayilmayacak
- Remote verify gecmeden Django DB yazilmayacak
- Remote delete basarisizsa Django kaydi silinmeyecek
- Admin view icinde daginik sekilde client kullanmak yerine ortak servis/engine factory uzerinden gidilecek
- `GeoServer` operasyonlari icin `EngineClientFactory.create_client(engine)` kullanilacak

---

## Mimari Yonelim

Bu task listesindeki ana fikir su:

- Admin sadece operasyon yuzu olacak
- Gercek is mantigi servis katmaninda olacak
- Sync ve verify davranisi merkezi hale getirilecek

Beklenen katmanlar:

```text
geodata_providers/
  admin.py
  admin_forms.py
  admin_actions/
  admin_views/
  sync_service.py
  engine_factory.py
  geoserver/client.py
  exceptions.py
```

### Mimari kurallar

- Admin action ve custom admin view sadece orchestration baslatsin
- Remote is mantigi service/client katmaninda toplansin
- Destructive operation'lar verify olmadan local delete yapmasin
- Hardcoded admin URL yazmak yerine mumkun oldugunca reverse/name tabanli ilerleyelim
- `except Exception: pass` kullanilmasin

---

## Git ve Branch Kurallari

Bu dokumani kullanacak agent icin git calisma sekli de net olmali. Bu is parcali ilerleyecegi icin her task grubu ayri branch'te gelistirilmeli.

### Temel kurallar

- Her ana faz ayri branch'te implement edilsin
- Gerekirse faz icindeki buyuk alt isler de ayri branch'e ayrilabilsin
- Tek branch'te birden fazla bagimsiz konu karistirilmasin
- Kullaniciya ait mevcut degisiklikler geri alinmasin
- Ana branch'e ancak ilgili faz smoke/test seviyesiyle dogrulandiktan sonra donulsun

### Onerilen branch yapisi

| Faz | Onerilen branch |
|------|------|
| Engine | `feature/geodata-providers-admin-engine` |
| Workspace | `feature/geodata-providers-admin-workspace` |
| Store | `feature/geodata-providers-admin-store` |
| Layer | `feature/geodata-providers-admin-layer` |
| Sync/Reconciliation | `feature/geodata-providers-admin-sync` |
| Admin path/template cleanup | `feature/geodata-providers-admin-cleanup` |
| Tests | `feature/geodata-providers-admin-tests` |

### Agent icin branch uygulama kurali

- Agent ise baslamadan once ilgili faz icin branch acsin
- O branch'te sadece o fazin kapsamina giren degisiklikleri yapsin
- Faz bitince test veya smoke sonucunu not dusup branch'i teslim etsin
- Sonraki faz icin yeni branch acilsin

### Commit yaklasimi

- Kucuk ve anlamli commit'ler atilsin
- Commit'ler operasyonel olarak okunabilir olsun
- Ornek commit basliklari:

`engine admin connection verification`

`workspace create flow remote first`

`store create verify before persist`

`layer publish flow admin hardening`

---

## Agent Calisma Protokolu

Bu dokuman sadece backlog degil, uzun sure kesintisiz calisacak bir agent icin de yurutme talimati olarak kullanilacak.

Hedef davranis su:

- Agent bir gorevi bitirince durmasin
- Ayni faz icindeki siradaki goreve otomatik gecsin
- Faz icindeki kritik maddeler tamamlaninca ilgili testleri kosup sonucu not etsin
- Faz kabul kriteri saglandiysa bir sonraki faza gecsin
- Sadece gercek bir blocker varsa dursun

### Agent'in calisma sirasi

Agent su sirayla ilerlemeli:

1. `PHASE 1 — Engine Admin`
2. `PHASE 2 — Workspace Admin`
3. `PHASE 3 — Store Admin`
4. `PHASE 4 — Layer Admin`
5. `PHASE 5 — Sync ve Reconciliation Sertlestirme`
6. `PHASE 6 — Admin Navigation ve Path Temizligi`
7. `PHASE 7 — Test ve Smoke Test`

### Faz icinde ilerleme kurali

- Faz icindeki gorevleri yukaridan asagi sirayla ele al
- Bir madde baska bir maddeyi block etmiyorsa siradaki maddeye gec
- Kucuk teknik borclari not et ama fazi gereksiz yere bloklama
- Fazin ana kabul kriterini etkileyen eksik varsa ayni faz icinde cozmeye devam et

### Ne zaman bir sonraki goreve gecmeli

Agent, asagidaki kosullarda sonraki goreve otomatik gecmeli:

- Ilgili kod degisikligi yapildiysa
- Temel test veya dogrulama yapildiysa
- Acik hata kalmadiysa veya kalan hata blocker degilse
- Dokumandaki gorevin ana amaci saglandiysa

### Ne zaman bir sonraki faza gecmeli

Agent, bir fazdan digerine su durumda gecmeli:

- Fazin kritik CRUD/verify davranislari calisiyorsa
- Fazin milestone kontrolundeki maddeler buyuk oranda saglandiysa
- Kalan isler sonraki fazi block etmiyorsa

### Ne zaman durmali

Agent sadece su durumlarda durmali:

- Cevabi repo icinden bulunamayan urun karari gerekiyorsa
- Veri kaybi riski olan bir karar gerekiyorsa
- Sandbox / permission / network engeli yuzunden ilerleyemiyorsa
- Bir sonraki adim icin zorunlu gizli bilgi veya credential gerekiyorsa
- Kod tabaninda kullanicinin mevcut degisiklikleriyle dogrudan cakisan bir durum varsa

### Ne zaman durmamali

Asagidaki durumlarda agent durmamali:

- Kucuk refactor ihtiyaci varsa
- Testlerden biri fail olduysa ve cozum bulunabiliyorsa
- Mevcut kod daginiksa ama toparlanabiliyorsa
- Hardcoded path veya naming tutarsizliklari varsa
- Faz icinde mantikli varsayim yaparak ilerlemek mumkunse

### Her fazin sonunda beklenenler

Agent her faz sonunda sunlari yapmali:

- Ilgili branch'teki degisiklikleri toparla
- Mumkun olan testleri veya smoke check'leri calistir
- Kisa bir sonuc notu birak
- Sonra otomatik olarak siradaki faz branch'ine gec

### Faz teslim notu formati

Her faz sonunda agent kendi notlarinda su formatta ilerlesin:

```text
Faz: PHASE X
Branch: feature/...
Tamamlananlar:
- ...
- ...
Calistirilan testler:
- ...
Kalan riskler:
- ...
Sonraki adim:
- PHASE Y
```

### Otonom ilerleme ilkesi

Bu dokumani kullanacak agent icin temel beklenti:

`Bir task bitince bekleme, siradaki taska gec.`

`Bir faz bitince bekleme, blocker yoksa sonraki faza gec.`

`Sadece gercek karar blokaji varsa dur.`

---

## Mevcut Durumun Kisa Ozet

Kod tarafinda bugun itibariyla su temel yapilar var:

- `GeodataEngine`, `Workspace`, `Store`, `Layer` modelleri var
- Django admin kayitlari mevcut
- `sync_service.py` mevcut
- `GeoServerClient` wrapper mevcut
- Layer publish/unpublish mantigi diger alanlara gore daha olgun

Ancak eksikler de var:

- `Engine`, `Workspace`, `Store` create akislari tam remote-first degil
- Update davranislari tam standardize degil
- Delete verify her yerde ayni sertlikte uygulanmiyor
- Admin path/template isimlerinde tutarsizliklar var

Bu nedenle yeni agent'in odagi:

- Var olan parcali yapilari tek bir standart altinda toplamak

---

# PHASE 1 — Engine Admin ⬜

**Hedef:** GeoServer engine kaydini Django Admin uzerinden guvenilir sekilde yonetmek.

Bu fazda engine icin kritik nokta sunun netlestirilmesi:

- Engine remote tarafta create edilen bir obje degil
- Engine kaydi esasen bir connection tanimi
- Bu nedenle create/update akisinin kalbi `validate_connection`

## 1.1 — Engine Admin Temizligi

| # | Gorev | Durum |
|---|------|------|
| 1.1.1 | `GeodataEngineAdmin` mevcut yapisini gozden gecir, `geodata_providers` isimlendirmesiyle tutarli hale getir | ⬜ |
| 1.1.2 | `list_display`, `list_filter`, `search_fields`, `readonly_fields` alanlarini mevcut kararlarla uyumlu hale getir | ⬜ |
| 1.1.3 | `admin_password` ve `api_key` alanlarinin UI'da geri echo edilmemesini netlestir | ⬜ |
| 1.1.4 | Hardcoded template/path kullanimlarini tespit et ve duzelt | ⬜ |

## 1.2 — Engine Create / Update Guvencesi

| # | Gorev | Durum |
|---|------|------|
| 1.2.1 | `GeodataEngineAdmin.save_model()` akisini incele | ⬜ |
| 1.2.2 | Yeni engine create sirasinda `validate_connection()` zorunlulugu ekle | ⬜ |
| 1.2.3 | `base_url`, `admin_username`, `admin_password` degisince yeniden `validate_connection()` calistir | ⬜ |
| 1.2.4 | Connection verify basarisizsa Django save'i iptal et | ⬜ |
| 1.2.5 | `is_default` davranisinin tek engine default kalacak sekilde korundugunu dogrula | ⬜ |

## 1.3 — Engine Actions

| # | Gorev | Durum |
|---|------|------|
| 1.3.1 | `test_connection` action ve custom view akisini mevcut kodla yeniden audit et | ⬜ |
| 1.3.2 | `sync_engines` action'inin GeoServer -> Django reconciliation mantigini korudugunu dogrula | ⬜ |
| 1.3.3 | Engine change form uzerindeki `Test Connection` ve `Sync` butonlarini calisir hale getir | ⬜ |
| 1.3.4 | Admin feedback mesajlarini operasyonel ve net hale getir | ⬜ |

## 1.4 — Engine Delete Politikasi

| # | Gorev | Durum |
|---|------|------|
| 1.4.1 | Engine delete oncesi bagli `Workspace`, `Store`, `Layer` kayitlarini kontrol et | ⬜ |
| 1.4.2 | PoC icin bagli kayit varsa engine delete'i blokla | ⬜ |
| 1.4.3 | Kullaniciya neden silinemedigini net admin mesaji ile don | ⬜ |

**Milestone kontrolu:**

- Engine create/update sirasinda connection verify zorunlu  
- Basarisiz connection ile DB'ye kayit dusmuyor  
- Admin'den test connection ve sync calisiyor  
- Delete policy net

---

# PHASE 2 — Workspace Admin ⬜

**Hedef:** Workspace CRUD akisini GeoServer-first mantikla guvenilir hale getirmek.

Workspace, GeoServer entegrasyonunda ilk gercek remote CRUD objesi oldugu icin bu faz kritik.

## 2.1 — Workspace Admin Temel Yapisi

| # | Gorev | Durum |
|---|------|------|
| 2.1.1 | `WorkspaceAdmin` listesini `engine`, `store_count`, `layer_count` gorunecek sekilde koru/duzelt | ⬜ |
| 2.1.2 | Inline `Store` gorunumu calisiyor mu kontrol et | ⬜ |
| 2.1.3 | Engine link ve admin navigation path'lerini duzelt | ⬜ |
| 2.1.4 | `geodata_engine` alanini edit modunda readonly tut | ⬜ |

## 2.2 — Workspace Create Akisi

| # | Gorev | Durum |
|---|------|------|
| 2.2.1 | Workspace create icin service/orchestration noktasi belirle | ⬜ |
| 2.2.2 | Remote pre-check: workspace zaten var mi kontrol et | ⬜ |
| 2.2.3 | Remote create: GeoServer'da workspace olustur | ⬜ |
| 2.2.4 | Remote verify: workspace gercekten olustu mu tekrar kontrol et | ⬜ |
| 2.2.5 | Verify gectikten sonra Django save yap | ⬜ |
| 2.2.6 | Verify fail olursa kaydi hic olusturma ve hata mesajini admin'de goster | ⬜ |

## 2.3 — Workspace Update Politikasi

| # | Gorev | Durum |
|---|------|------|
| 2.3.1 | `name` alanini PoC icin readonly yap | ⬜ |
| 2.3.2 | `geodata_engine` alanini readonly tut | ⬜ |
| 2.3.3 | Sadece `description` gibi local metadata alanlarinin update edilmesine izin ver | ⬜ |
| 2.3.4 | Remote rename ihtiyacini bu fazda kapsam disi olarak dokumante et | ⬜ |

## 2.4 — Workspace Sync ve Delete

| # | Gorev | Durum |
|---|------|------|
| 2.4.1 | `workspace_sync_view` ve action davranisini audit et | ⬜ |
| 2.4.2 | Delete sirasinda `delete workspace -> verify deletion -> Django delete` zincirini zorunlu hale getir | ⬜ |
| 2.4.3 | `vector` gibi reserved workspace kurallarini tek yerde topla | ⬜ |
| 2.4.4 | Bulk delete senaryosunda da ayni guvenlik kurallarini uygula | ⬜ |

**Milestone kontrolu:**

- Workspace admin'den olusturulabiliyor  
- GeoServer verify olmadan DB save olmuyor  
- Workspace update kontrollu  
- Delete remote-first calisiyor

---

# PHASE 3 — Store Admin ⬜

**Hedef:** Store CRUD akisini PoC'nin en kritik entegrasyon noktasi olarak saglamlastirmak.

Bu faz en onemli fazlardan biri. Cunku:

- GeoServer datastore olusturma gercek remote entegrasyon isidir
- Buradaki eksik verify tum sistemi drift'e sokar
- Layer publish akisi store guvenilirligine baglidir

## 3.1 — Store Admin Temel Temizlik

| # | Gorev | Durum |
|---|------|------|
| 3.1.1 | `StoreAdmin` fieldset, readonly, list_display yapisini audit et | ⬜ |
| 3.1.2 | `workspace_link` gibi hatali/hardcoded URL kullanimlarini duzelt | ⬜ |
| 3.1.3 | Password badge davranisinin decrypt edilebilir deger uzerinden calistigini dogrula | ⬜ |
| 3.1.4 | Password korunumu davranisini (`blank save mevcut sifreyi ezmesin`) koru | ⬜ |

## 3.2 — Store Create Akisi

| # | Gorev | Durum |
|---|------|------|
| 3.2.1 | Admin create akisini service tabanli hale getir | ⬜ |
| 3.2.2 | Store create oncesi workspace ve engine bagini net resolve et | ⬜ |
| 3.2.3 | Remote pre-check: store ayni workspace altinda zaten var mi bak | ⬜ |
| 3.2.4 | Remote create: GeoServer datastore create et | ⬜ |
| 3.2.5 | Remote verify: `get_datastores(workspace)` ile store gorunuyor mu kontrol et | ⬜ |
| 3.2.6 | Gerekirse `get_datastore_detail()` ile beklenen host/database/schema bilgilerini dogrula | ⬜ |
| 3.2.7 | Verify basariliysa Django save yap | ⬜ |
| 3.2.8 | Verify basarisizsa local kayit yazma | ⬜ |

## 3.3 — Store Update Politikasi

| # | Gorev | Durum |
|---|------|------|
| 3.3.1 | `name`, `workspace`, `store_type` alanlarini readonly tut | ⬜ |
| 3.3.2 | Local metadata ile remote connection field'larini ayir | ⬜ |
| 3.3.3 | Connection field update'leri icin GeoServer update destek durumunu netlestir | ⬜ |
| 3.3.4 | Guvenli remote update yoksa `recreate gerekli` politikasini uygula | ⬜ |

## 3.4 — PostGIS Preview ve Credential Akisi

| # | Gorev | Durum |
|---|------|------|
| 3.4.1 | `store_postgis_tables_view` davranisini audit et | ⬜ |
| 3.4.2 | Password yoksa acik ve net warning goster | ⬜ |
| 3.4.3 | Decrypt edilemeyen credential durumunu `missing` gibi ele al | ⬜ |
| 3.4.4 | Preview sonucunda geometry type, srid, table bilgilerini guvenilir don | ⬜ |

## 3.5 — Store Clone ve Delete

| # | Gorev | Durum |
|---|------|------|
| 3.5.1 | `store_clone_view` akisini remote-first ve verify zorunlu olacak sekilde audit et | ⬜ |
| 3.5.2 | Clone sonrasinda verify olmadan Django create yapilmamasini sagla | ⬜ |
| 3.5.3 | Delete sirasinda `remote delete -> remote verify -> local delete` standardini zorunlu hale getir | ⬜ |
| 3.5.4 | PUBLISHED layer bagliysa delete policy'yi acik belirle | ⬜ |

**Milestone kontrolu:**

- Store create GeoServer-first calisiyor  
- Verify olmadan DB save olmuyor  
- PostGIS preview credential durumunu dogru yonetiyor  
- Delete guvenli calisiyor

---

# PHASE 4 — Layer Admin ⬜

**Hedef:** Layer publish/unpublish ve metadata update akisini admin altinda guvenilir sekilde tamamlamak.

Layer tarafi bugun en gelismis kisim ama standartlastirilmasi gerekiyor.

## 4.1 — Layer Admin Temel Yapisi

| # | Gorev | Durum |
|---|------|------|
| 4.1.1 | `LayerAdmin` listesi, filtreleri ve readonly alanlari audit et | ⬜ |
| 4.1.2 | `workspace_link` ve `store_name` admin linklerini duzelt | ⬜ |
| 4.1.3 | `publishing_state_badge` davranisini koru | ⬜ |
| 4.1.4 | Layer add akisinin normal generic add form degil publish flow olmasi gerektigini netlestir | ⬜ |

## 4.2 — Publish from PostGIS

| # | Gorev | Durum |
|---|------|------|
| 4.2.1 | `PublishPostGISForm` yapisini audit et | ⬜ |
| 4.2.2 | Workspace secimine gore store filtreleme akisini calisir hale getir | ⬜ |
| 4.2.3 | Publish flow'u `pre-check -> publish -> verify -> local persist` standardina gore netlestir | ⬜ |
| 4.2.4 | `verify_featuretype()` ile duplicate publish'i engelle | ⬜ |
| 4.2.5 | Verify basarisizsa `Layer` kaydi olusturma | ⬜ |
| 4.2.6 | Form hata aldiginda secilen degerleri koru | ⬜ |

## 4.3 — Layer Update Politikasi

| # | Gorev | Durum |
|---|------|------|
| 4.3.1 | `table_name`, `workspace`, `store`, `geometry_column`, `geometry_type` alanlarini readonly tut | ⬜ |
| 4.3.2 | `title`, `description`, `is_public` gibi alanlari mutable kabul et | ⬜ |
| 4.3.3 | `PUBLISHED` layer save sirasinda once remote metadata update yap | ⬜ |
| 4.3.4 | Remote update sonrasi verify adimi ekle | ⬜ |
| 4.3.5 | Verify fail olursa Django update yapma | ⬜ |

## 4.4 — Layer Publish / Unpublish Actions

| # | Gorev | Durum |
|---|------|------|
| 4.4.1 | Bulk `publish_layer` action'ini audit et | ⬜ |
| 4.4.2 | Bulk `unpublish_layer` action'ini audit et | ⬜ |
| 4.4.3 | Publish action'da verify olmadan `PUBLISHED` state set edilmemesini sagla | ⬜ |
| 4.4.4 | Unpublish action'da verify olmadan `UNPUBLISHED` state set edilmemesini sagla | ⬜ |
| 4.4.5 | Reconciliation ile yeni intent arasindaki farki admin mesajlarinda netlestir | ⬜ |

## 4.5 — Layer Delete Guvenligi

| # | Gorev | Durum |
|---|------|------|
| 4.5.1 | `PUBLISHED` layer icin remote-first delete standardini koru | ⬜ |
| 4.5.2 | Delete sonrasi verify zorunlulugunu netlestir | ⬜ |
| 4.5.3 | `DRAFT`, `FAILED`, `UNPUBLISHED` kayitlarda local delete politikasini netlestir | ⬜ |
| 4.5.4 | Bulk delete senaryosunda da ayni kurali uygula | ⬜ |

**Milestone kontrolu:**

- Layer publish admin altinda calisiyor  
- Verify olmadan local `PUBLISHED` state yazilmiyor  
- Update davranisi kontrollu  
- Delete published state icin remote-first

---

# PHASE 5 — Sync ve Reconciliation Sertlestirme ⬜

**Hedef:** Admin CRUD ile `sync_service` arasindaki davranis farklarini azaltmak ve drift'i temizlemek.

## 5.1 — Sync Service Audit

| # | Gorev | Durum |
|---|------|------|
| 5.1.1 | `sync_all_resources()` akisini tekrar incele | ⬜ |
| 5.1.2 | `sync_workspaces`, `sync_stores_for_workspace`, `sync_layers_for_workspace` davranislarini tek tek audit et | ⬜ |
| 5.1.3 | `Store` sync sirasinda password overwrite edilmedigini yeniden dogrula | ⬜ |
| 5.1.4 | Layer/store prefix cleanup ve data integrity kontrollerini dogrula | ⬜ |

## 5.2 — Command Flow vs Reconciliation Flow Netlestirme

| # | Gorev | Durum |
|---|------|------|
| 5.2.1 | Admin CRUD path'i ile sync path'ini dokumante et | ⬜ |
| 5.2.2 | CRUD sonrasi opsiyonel/manual sync ihtiyacini netlestir | ⬜ |
| 5.2.3 | Admin mesajlarinda `remote verified`, `db saved`, `sync complete` farklarini acik yaz | ⬜ |

**Milestone kontrolu:**

- Sync service CRUD politikalariyla celismiyor  
- Password ve naming integrity korunuyor  
- Reconciliation mantigi net

---

# PHASE 6 — Admin Navigation ve Path Temizligi ⬜

**Hedef:** PoC seviyesinde minimum ama calisan admin deneyimi saglamak.

## 6.1 — URL ve Template Tutarliligi

| # | Gorev | Durum |
|---|------|------|
| 6.1.1 | `admin/geodata_engine/...` ve `admin/geodata_providers/...` karisimini temizle | ⬜ |
| 6.1.2 | Hatali path'leri ve typo'lari duzelt | ⬜ |
| 6.1.3 | Mumkun oldugunca named URL/reverse kullan | ⬜ |
| 6.1.4 | Sadece gerekli minimum template override'larini birak | ⬜ |

## 6.2 — Kullanilabilirlik Iyilestirmeleri

| # | Gorev | Durum |
|---|------|------|
| 6.2.1 | Change form butonlari calisiyor mu kontrol et | ⬜ |
| 6.2.2 | Changelist uzerinden gerekli aksiyonlarin gorunur oldugunu dogrula | ⬜ |
| 6.2.3 | Hata durumlarinda sessiz failure yerine acik mesaj goster | ⬜ |

**Milestone kontrolu:**

- Admin path/template duzeni calisiyor  
- Navigation kirik degil  
- PoC icin yeterli operasyonel deneyim var

---

# PHASE 7 — Test ve Smoke Test ⬜

**Hedef:** En kritik CRUD ve sync senaryolarinin bozulmadigini guvenceye almak.

## 7.1 — Unit Test

| # | Gorev | Durum |
|---|------|------|
| 7.1.1 | Workspace create verify fail olursa DB save yok testi yaz | ⬜ |
| 7.1.2 | Store create verify fail olursa DB save yok testi yaz | ⬜ |
| 7.1.3 | Store delete verify fail olursa DB delete yok testi yaz | ⬜ |
| 7.1.4 | Layer publish verify fail olursa `PUBLISHED` state set edilmiyor testi yaz | ⬜ |
| 7.1.5 | Engine save sirasinda connection verify fail olursa DB save olmuyor testi yaz | ⬜ |
| 7.1.6 | Workspace delete verify fail olursa Django delete olmuyor testi yaz | ⬜ |

## 7.2 — Integration Test

| # | Gorev | Durum |
|---|------|------|
| 7.2.1 | Mock GeoServer ile engine validate testi yaz | ⬜ |
| 7.2.2 | Mock GeoServer ile workspace create akisini test et | ⬜ |
| 7.2.3 | Mock GeoServer ile store create akisini test et | ⬜ |
| 7.2.4 | Mock GeoServer ile layer publish/unpublish akisini test et | ⬜ |
| 7.2.5 | Admin custom view/action endpoint'lerini request bazli test et | ⬜ |

## 7.3 — Manual Smoke Test

| # | Gorev | Durum |
|---|------|------|
| 7.3.1 | Admin'den engine ekle ve connection test et | ⬜ |
| 7.3.2 | Workspace olustur ve GeoServer'da gorundugunu dogrula | ⬜ |
| 7.3.3 | Store olustur ve GeoServer datastore olarak gorundugunu dogrula | ⬜ |
| 7.3.4 | PostGIS table preview calisiyor mu test et | ⬜ |
| 7.3.5 | Layer publish et ve GeoServer layer olarak gorundugunu dogrula | ⬜ |
| 7.3.6 | Layer metadata guncelle ve remote state ile eslendigini dogrula | ⬜ |
| 7.3.7 | Layer unpublish et | ⬜ |
| 7.3.8 | Store delete et | ⬜ |
| 7.3.9 | Workspace delete et | ⬜ |
| 7.3.10 | Son olarak sync action calistir ve drift kalmadi mi kontrol et | ⬜ |

**Milestone kontrolu:**

- Temel CRUD akislari test altinda  
- En kritik verify-before-persist kurali guvence altinda  
- Manual smoke test senaryolari tanimli

---

## Bu Dokumani Kullanacak Agent Icin Notlar

- Once `Engine` fazini bitir, sonra `Workspace`, sonra `Store`, sonra `Layer`
- UI guzellestirmesi yapma, fonksiyonellige odaklan
- GeoServer ilk hedef provider; soyutlamayi bozma ama ilk entegrasyonu buna gore tamamla
- Kodun mevcut durumunu koruyarak ilerle, gereksiz buyuk refactor yapma
- En onemli kabul kriteri:

`Remote verify olmadan Django DB mutate edilmemeli.`

### Test beklentisi

Bu isler icin test yazmak mumkun ve gereklidir.

Ozellikle su alanlarda test yazilabilir:

- Admin `save_model` davranislari
- Admin delete davranislari
- Admin action'lari
- Custom admin view endpoint'leri
- `sync_service` reconciliation akislari
- `GeoServerClient` davranislarinin mock ile orchestration testleri

Ilk fazda gercek GeoServer'a bagli e2e test zorunlu degil. Ama mock/stub tabanli unit ve integration testler rahatlikla yazilabilir.

PoC icin minimum test hedefi:

- Her CRUD turunde en az bir `verify fail -> local persist yok` testi
- En az bir `verify success -> local persist var` testi
- Layer publish/unpublish icin state transition testleri
- Store create/delete icin remote-first davranis testleri

---

## Basari Kriteri

Bu task listesi tamamlandiginda su resmi gormek istiyoruz:

- Django Admin altinda `Engine -> Workspace -> Store -> Layer` akisi calisiyor
- GeoServer ile temel entegrasyon guvenilir
- CRUD operasyonlari sync-safe
- Ayrica bir geo console yazmaya gerek kalmiyor
- Sonraki asamada admin template/design degisse bile operasyonel cekirdek ayni kaliyor
