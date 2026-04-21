# Geodata Providers Admin CRUD Planı

Tarih: 2026-04-20

## Kısa Log

- `2026-04-21` GeodataEngine add kırığı düzeltildi: olmayan custom template referansları kaldırıldı. `geri alindi`
- `2026-04-21` Engine formunda connection validation eklendi. `yapildi`
- `2026-04-21` Workspace create için remote create + verify eklendi. `yapildi`
- `2026-04-21` Store create için PostGIS remote create + verify eklendi. `yapildi`
- `2026-04-21` Workspace editte `name` ve `geodata_engine` readonly yapıldı. `yapildi`
- `2026-04-21` Hardcoded admin linklerinin bir kısmı `reverse(...)` ile düzeltildi. `kismen yapildi`
- `2026-04-21` Store clone template eklendi. `yapildi`
- `2026-04-21` Layer add ekranı normal model formdan çıkarılıp Publish PostGIS akışına yönlendirildi. `yapildi`
- `2026-04-21` Engine / Workspace / Store / Layer create-update sonrası ilgili sync otomatik tetiklenir kuralı eklendi. `yapildi`
- `2026-04-21` Workspace create post-verify strict hale getirildi; GeoServer'da yoksa Django save olmaz. `yapildi`
- `2026-04-21` Workspace verify liste yerine direct workspace detail endpoint ile sıkılaştırıldı. `yapildi`
- `2026-04-21` Workspace verify hem detail hem list ile zorunlu hale getirildi; save sonrası workspace-level sync kullanildi. `yapildi`

## Hemen Sonraki İşler

- `GeodataEngine` delete bağlı kayıt varsa bloklanacak
- `Workspace / Store / Layer` admin path stringleri tek namespace altında temizlenecek
- Layer publish ve metadata update sonrası verify sertleştirilecek

## Amaç

`geodata_providers` app'i için PoC seviyesinde, tamamen Django Admin üzerinden çalışan CRUD akışlarını sağlamlaştırmak.

Buradaki ana hedef UI değil, fonksiyonellik:

- Ayrı bir admin template çalışması şu an kapsam dışı
- Minimum admin geliştirmesi ile operasyonların çalışması hedefleniyor
- Asıl öncelik Django DB ile geodata provider'ın gerçek state'inin senkron kalması

Bu yüzden temel prensip şu olacak:

`Django'da bir kayıt ancak remote provider üzerinde işlem gerçekten doğrulanınca create/update/delete edilmiş sayılacak.`

Yani sadece `200 OK` yeterli kabul edilmeyecek. Her kritik işlemden sonra hedef sistemde gerçekten oluştu mu, güncellendi mi, silindi mi diye ikinci bir doğrulama isteği yapılacak.

## Mevcut Durum Özeti

Kod tarafında önemli bir temel zaten atılmış:

- Modeller ayrılmış: `GeodataEngine`, `Workspace`, `Store`, `Layer`
- Admin kayıtları mevcut: [admin.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/admin.py)
- Sync servisi mevcut: [sync_service.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/sync_service.py)
- GeoServer client wrapper mevcut: [client.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/geoserver/client.py)
- Layer publish/unpublish için GeoServer-first mantığı kısmen uygulanmış

Bu iyi bir başlangıç. Ama CRUD davranışları tüm modeller için aynı disiplinle tamamlanmış değil.

## Kritik Gözlemler

### 1. Layer tarafında sync mantığı diğer modellere göre daha olgun

`publish_layer`, `unpublish_layer` ve `publish_postgis_view` içinde şu pattern zaten görülüyor:

1. Remote pre-check
2. Remote operation
3. Remote verify
4. Sonra Django persist

Bu pattern doğru. Bunu `Engine`, `Workspace`, `Store` için de aynı netlikte standart hale getirmek lazım.

### 2. Create akışları admin save_model seviyesinde tam remote-first değil

Şu an:

- `GeodataEngineAdmin.save_model` sadece Django save yapıyor
- `WorkspaceAdmin.save_model` sadece Django save yapıyor
- `StoreAdmin.save_model` sadece Django save yapıyor

Bu PoC için kritik açık. Çünkü admin üzerinden obje oluşturulunca remote provider'a da gönderilip doğrulanması gerekiyor.

Beklenen davranış:

- Admin form submit
- Remote create command
- Remote verify
- Başarılıysa Django save
- Verify başarısızsa DB'ye hiç yazmama

### 3. Delete akışlarında kısmi koruma var ama standartlaştırma eksik

Şu an:

- Workspace delete için `GeoServerSyncService.delete_workspace_safe(...)` çağrılıyor
- Store delete için önce remote delete deneniyor
- Layer delete için published ise remote delete deneniyor

Bu iyi ama her yerde aynı garanti düzeyi yok. Özellikle şu kural sabit olmalı:

- Delete çağrısından sonra remote existence check yapılmalı
- Remote'da hala duruyorsa Django kaydı silinmemeli

### 4. Update akışları en zayıf alan

Create ve delete kadar update de sync açısından riskli.

Örnekler:

- `Workspace.name` değişirse remote workspace rename ya desteklenmeli ya da yasaklanmalı
- `Store` connection field'ları değişirse GeoServer datastore update gerçekten uygulanmış mı kontrol edilmeli
- `Layer.title` ve `description` için remote update denenmiş ama verify adımı eksik

PoC için önerim:

- Rename gerektiren alanları admin'de readonly yap
- Desteklenen update'leri sadece metadata seviyesinde tut
- Remote tarafı güvenilir olmayan update'lerde explicit recreate flow kullan

### 5. Admin path/template tarafında tutarsızlıklar var

Kodda `admin/geodata_providers/...` ve `admin/geodata_engine/...` path'leri karışık kullanılıyor.

Örnek:

- [admin.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/admin.py)
- [admin_views/store.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/admin_views/store.py)
- [admin_views/layer.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/admin_views/layer.py)

Hatta `geodata_providersers` gibi typo da var.

Bu PoC'nin ana hedefi template olmadığı için burada minimum düzeltme yeterli:

- URL name reverse kullan
- Hardcoded admin path'leri temizle
- Sadece çalışan admin navigation bırak

## Temel Mimari Karar

Bu app için source of truth tek yönlü değil, işlem türüne göre iki farklı çalışma modu tanımlanmalı:

### A. Command Flow

Admin üzerinden create/update/delete yapılıyorsa:

- Django command source olur
- Ama DB write işlemi remote verify sonrası yapılır

Pattern:

1. Input validate et
2. Remote pre-check yap
3. Remote operation yap
4. Remote verify yap
5. Transaction içinde Django persist et
6. Gerekirse son bir reconciliation log'u yaz

### B. Reconciliation Flow

Sync action çalıştırılıyorsa:

- Remote provider source of truth olur
- Django DB remote state'e göre reconcile edilir

Bu zaten [sync_service.py](/Users/hsadmin/Desktop/coding/dcs-django-api/tosca_api/apps/geodata_providers/sync_service.py) içinde başlatılmış.

## Admin İçin Önerdiğim CRUD Stratejisi

## 1. GeodataEngine CRUD

### Create ✅

PoC seviyesinde sadece Django'da create edilebilir.

Gerekçe:

- Engine aslında remote sistemin kendisi, remote'da ayrıca create edilen bir obje değil
- Burada kritik olan create değil, `validate_connection`

Akış:

1. Admin form submit
2. `validate_connection`
3. Başarılıysa Django save
4. Başarısızsa save etme
5. Save sonrası otomatik `engine sync` çalışmalı

### Update ✅

- `base_url`, `admin_username`, `admin_password` değişince yeniden `validate_connection`
- Yeni connection doğrulanmadan kayıt update edilmemeli
- Update sonrası otomatik `engine sync` çalışmalı

### Delete ✅

- Engine bir remote resource değil, connection tanımıdır
- Bu yüzden engine delete local delete olarak çalışır
- Bağlı `workspace/store/layer` kayıtları Django cascade ile birlikte silinir

## 2. Workspace CRUD

### Create ✅

Akış:

1. Django form valid
2. Remote `workspace exists` pre-check
3. Remote `create workspace`
4. Remote `workspace exists` verify
5. Başarılıysa Django save
6. Save sonrası otomatik `workspace-level sync` çalışmalı

Not:

- `vector` gibi reserved workspace isimleri merkezi policy ile korunmalı

### Update ✅

PoC için:

- `name` readonly olsun
- `geodata_engine` readonly olsun
- Sadece `description` update edilsin
- Update sonrası otomatik `workspace-level sync` çalışmalı
- Update sonrası otomatik `engine sync` çalışmalı

Gerekçe:

- Workspace rename remote tarafta riskli
- Engine taşıma fiilen başka obje lifecycle'ı demek

### Delete ✅

Akış:

1. Remote delete workspace
2. Remote verify workspace yok
3. Sonra Django delete

Ek kontrol:

- İçinde store/layer varsa silent delete değil, açık policy ile davranılmalı
- PoC için cascade delete ancak remote başarıdan sonra olmalı

Uygulanan policy:

- İçinde `store` veya `layer` olan workspace admin'den silinmez
- Önce bağımlı kayıtlar temizlenir
- Sonra remote delete + verify + Django delete çalışır

## 3. Store CRUD

### Create

Store CRUD bu app'in en kritik parçası.

Akış:

1. Form validation
2. Workspace ve engine resolve
3. Remote pre-check: store zaten var mı
4. Remote create store
5. Remote verify: ilgili workspace altında store gerçekten oluşmuş mu
6. Başarılıysa Django save
7. Save sonrası otomatik ilgili workspace için sync çalışmalı

Burada özellikle sadece HTTP response'a güvenilmeyecek.

Verify için:

- `get_datastores(workspace)` çağrısı
- Gerekirse `get_datastore_detail(workspace, store_name)` çağrısı
- Beklenen host/database/schema gibi alanların tutarlılığı kontrolü

### Update

PoC için ikiye ayırmak lazım:

- `description` gibi local metadata alanları: normal update
- Connection alanları: remote update + verify zorunlu

Pragmatik karar:

- `name`, `workspace`, `store_type` readonly kalsın
- Connection değişince GeoServer datastore update servisi yazılsın
- Remote update güvenli değilse "recreate required" mesajı verilsin
- Desteklenen update sonrası otomatik ilgili workspace için sync çalışmalı

### Delete

Akış:

1. Remote delete store
2. Remote verify store yok
3. Sonra Django delete

Ek kontrol:

- Store altında publish edilmiş layer varsa delete öncesi bloklanmalı ya da controlled cascade yapılmalı
- PoC için block etmek daha güvenli

## 4. Layer CRUD

Layer için mevcut yön doğru ama sertleştirme gerekli.

### Create

Admin'de layer create için manuel generic model form yerine publish flow kullanılmalı.

Yani:

- Layer doğrudan normal admin add formuyla oluşturulmasın
- Sadece `Publish PostGIS` akışı ile oluşsun

Akış:

1. Table metadata al
2. Remote pre-check
3. Remote publish
4. Remote verify
5. Django `Layer` kaydı oluştur
6. Create sonrası otomatik ilgili workspace için layer sync çalışmalı

### Update

PoC için sadece şunlar desteklensin:

- `title`
- `description`
- `is_public`

Eğer layer `PUBLISHED` ise:

1. Remote metadata update
2. Remote detail fetch ile verify
3. Sonra Django update
4. Update sonrası otomatik ilgili workspace için layer sync çalışmalı

`table_name`, `workspace`, `store`, `geometry_column`, `geometry_type` readonly kalmalı.

### Delete

Mevcut mantık korunmalı ama sertleştirilmeli:

1. Remote delete layer
2. Remote verify layer gerçekten yok
3. Sonra Django delete

`verify_featuretype(...)` kontrolünün layer name ve resource name ayrımını net ele aldığından emin olunmalı.

## Sync Güvenilirliği İçin Net Kurallar

Bu app'te uygulayacağım ana development standardı şu olacak:

### 1. Her remote mutasyon için tek bir orchestration service olacak

Admin class'larının içine doğrudan iş mantığı gömmek yerine servisler tanımlanmalı:

- `EngineAdminService`
- `WorkspaceAdminService`
- `StoreAdminService`
- `LayerAdminService`

Ya da daha yalın bir isimlendirme ile:

- `workspace_service.py`
- `store_service.py`
- `layer_service.py`

Admin sadece form/input ve user message yönetsin.

### 2. Remote verify başarısızsa Django persist yapılmayacak

Bu, sistemin ana sözleşmesi olacak.

Örnek:

- GeoServer create store dedi
- 200 döndü
- Ama `get_datastores()` içinde görünmüyor

Bu durumda:

- Django save yok
- Kullanıcıya açık hata mesajı var
- Log'da verify failure var

### 3. DB write için transaction kullanılacak

Pattern:

1. Remote operation outside transaction
2. Verify
3. `transaction.atomic()` ile local persist

Sebep:

- Network çağrıları DB transaction içinde tutulmamalı
- Lock süreleri büyütülmemeli

### 4. Her operation idempotent tasarlanacak

Örnek:

- Workspace zaten varsa create ikinci kez patlamamalı
- Store zaten silinmişse delete success-benzeri sonuç dönebilmeli
- Sync action tekrar çalıştırılınca drift temizlenmeli

### 5. Admin mesajları operasyonel olacak

Mesajlar şu netliği taşımalı:

- Remote command gönderildi mi
- Verify geçti mi
- Django persist edildi mi
- Recovery gerekiyor mu

Örnek mesaj formatı:

`Store created in GeoServer, verification passed, Django record saved.`

veya

`GeoServer create returned success but verification failed; Django record was not saved.`

## Uygulama Fazları

## Faz 1: Admin CRUD boundary'lerini sertleştirme

İlk geliştirme fazında şunları yaparım:

- Workspace create için remote-first save flow
- Store create için remote-first save flow
- Layer create'i sadece publish flow ile sınırlandırma
- Engine update sırasında connection verify zorunluluğu
- Hardcoded admin URL/path temizliği

Çıktı:

- Admin üzerinden temel create/update/delete senaryoları kontrollü çalışır

## Faz 2: Update operasyonlarını güvenli hale getirme

- Workspace rename'i kapatma
- Store identity field'larını kilitleme
- Layer mutable field set'ini daraltma
- Published layer metadata update sonrası remote verify ekleme

Çıktı:

- UI'dan hatalı veya yarım update ile drift oluşması azalır

## Faz 3: Delete güvenliğini standardize etme

- Workspace delete verify standardı
- Store delete verify standardı
- Layer delete verify standardı
- Dependency check policy

Çıktı:

- Remote silinmeden local silinmeme garantisi netleşir

## Faz 4: Reconciliation ve smoke test

- Admin action ile manuel sync
- CRUD sonrası sync smoke test
- Drift senaryoları için testler

Senaryolar:

- Remote create başarılı, verify başarısız
- Remote delete başarılı döndü, obje hâlâ duruyor
- Django'da kayıt var ama remote'da yok
- Remote'da kayıt var ama Django'da yok

## Test Planı

PoC için minimum ama etkili test seti şöyle olmalı:

### Unit Test

- Workspace create service: verify fail ise DB save yok
- Store create service: remote exists ise duplicate create yok
- Store delete service: verify fail ise DB delete yok
- Layer publish service: verify fail ise `PUBLISHED` state set edilmiyor

### Integration Test

- Mock GeoServer ile full admin create workspace
- Mock GeoServer ile create store
- Mock GeoServer ile publish layer
- Mock GeoServer ile delete store/layer

### Manual Smoke Test

Admin üzerinden sırasıyla:

1. Engine ekle
2. Connection test et
3. Workspace oluştur
4. Store oluştur
5. PostGIS tablosunu publish et
6. Layer metadata güncelle
7. Layer unpublish et
8. Store sil
9. Workspace sil

Her adımda:

- Remote state kontrol
- Django DB kontrol
- Sonra `sync` action ile reconciliation kontrol

## PoC İçin Net Scope

Bu iteration'da özellikle şunlara odaklanırım:

- Admin panel içinde minimum geliştirme
- Template/UI yatırımını minimumda tutma
- Remote-first CRUD
- Verify-before-persist
- Sync action'larının güvenilir olması

Şunları bilinçli olarak sonraya bırakırım:

- Geniş admin tema/custom design çalışması
- Async job orchestration
- Retry queue
- Audit trail ekranları
- Multi-provider abstraction'ı tam genelleme

## Sonuç

Bu app için doğru yön şu:

- Admin'i sadece operasyon konsolu gibi kullanmak
- CRUD'ü doğrudan model save/delete problemi gibi değil, remote orchestration problemi gibi ele almak
- Her mutasyonda `pre-check -> operation -> verify -> local persist` standardını zorunlu hale getirmek

Ben development'ı bu mantıkla yaparım.

Özellikle `Workspace`, `Store` ve `Layer` tarafında Django ile GeoServer arasındaki sync garantisini bu seviyede kurarsak, daha sonra admin template değişse bile kritik davranış bozulmaz. Çünkü asıl değer UI'da değil, orchestration katmanında olacak.
