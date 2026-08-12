# 09 — ACL hata yolu: push başarısızsa Workspace hard-fail

**Track:** C · **Canonical:** §5(c) HTTP semantiği, §11 C3 — **§10a'daki dirty+retry tasarımı bu ticket'ta ürün kararıyla terk edildi, aşağıya bak.**

**What to build:** ACL push başarısız olursa Workspace **hiç kaydedilmez** (create/update rollback olur, hata kullanıcıya/API'ye yükselir). `dirty` durumu, `sync_acl` retry command'ı **yok** — v1'de outbox/framework zaten yoktu (§10a), bu revizyonla soft-fail de kalktı.

**Blocked by:** 08.

**Status:** ✅ done (2026-08-12)

---

## Ürün kararı — canonical §10a'dan sapma

Orijinal ticket metni (aşağıda arşivlendi) canonical §10a'nın "ACL push başarısız → `sync_status=dirty` + retry management command" tasarımını uyguluyordu. İlk implementasyon bu şekilde yapıldı (bkz. git history), **ancak kullanıcı bunu reddetti**:

> "ben dirty kavramına karşıyım. eğer workspace oluştururken ACL oluşturamadıysak hata versin."

Karar: **hard-fail**. Bir Workspace, GeoServer ACL'i başarıyla push edilmeden Django'da var olamaz. `dirty`/`sync_status` alanı yok, `sync_acl` command yok. Bir ACL push hatası → workspace save'i (create veya `organization`/`visibility` değişen update) tamamen başarısız olur, DB'ye hiçbir şey yazılmaz.

**İstisna — engine yok/inaktif bir hata değildir.** Bir Workspace'in henüz aktif bir `GeodataEngine`'e bağlı olmaması (örn. engine provision edilmeden önce metadata hazırlanması, ya da bilinçli olarak deaktive edilmiş bir engine) "push edilecek bir şey yok" durumudur — sessizce atlanır (ticket 08'in orijinal davranışı). Hard-fail sadece **aktif bir engine varken push gerçekten başarısız olursa** tetiklenir (client oluşturulamadı ya da `set_layer_rule` hata döndü).

## Uygulama

1. **`GeoServerSecuritySyncService.sync()`** (`security_sync.py`) artık `bool` dönmüyor — başarıda `None` döner, başarısızlıkta `GeoServerACLSyncError` fırlatır. Engine yok/inaktif → sessiz no-op (log + return, hata yok).
2. **`Workspace.save()`** (`models.py`) `transaction.atomic()` ile sarmalandı: `post_save` sinyali (`signals.py`) içindeki `GeoServerSecuritySyncService.sync()` çağrısı hata fırlatırsa, atomic blok INSERT/UPDATE'i rollback eder — Workspace DB'de hiç var olmaz / eski haline döner.
3. **`signals.py`** hatayı yutmaz, sarmalamaz — olduğu gibi yukarı fırlatmasına izin verir.
4. **Test altyapısı:** kök `conftest.py`'a `GeodataEngine.get_client` için test-only bir varsayılan patch eklendi (`_default_acl_sync_client`) — repodaki onlarca mevcut test, ACL davranışıyla ilgilenmeden gerçek bir GeoServer olmadan Workspace fixture'ı kuruyordu; artık hepsi varsayılan olarak "push başarılı" mock client'ı alıyor. ACL davranışını gerçekten test eden dosyalar (`test_security_sync_service.py`) kendi `patch.object(GeodataEngine, 'get_client', ...)`'ını kullanarak bunu per-test override ediyor.

## Acceptance criteria

- [x] ACL push hatası (aktif engine + gerçek push hatası) Workspace save'ini **tamamen başarısız** kılar — satır DB'ye yazılmaz/geri alınır.
- [x] Engine yok/inaktif → sessiz no-op, hata yok (ticket 08 davranışı korunur).
- [x] `dirty`/`sync_status` alanı, migration, `sync_acl` command **yok** (bilinçli olarak eklenmedi).
- [x] Birim test (mock client): push hatası → `GeoServerACLSyncError` + Workspace DB'de yok; push başarısı → normal; no-engine/inactive-engine → no-op.
- [x] Regresyon: `make django-test-unit` tüm suite yeşil (889 test, 2 pre-existing/ilgisiz `ConnMaxAgeSettingsTests` hariç).

## Doğrulama

```
make django-test-unit
```

## Canonical atıfları
§5c idempotent push · §11 C3 · **§10a bu ticket'ta ürün kararıyla override edildi — dirty+retry yerine hard-fail.**

---

<details>
<summary>Arşiv: orijinal ticket metni (dirty+retry tasarımı, artık geçerli değil)</summary>

Aşağıdaki metin bu ticket'ın ilk yazıldığı haliydi (canonical §10a'nın dirty+retry tasarımını uyguluyordu). İlk implementasyon bu şekilde yapıldıktan sonra kullanıcı tasarımı reddetti ve yukarıdaki hard-fail kararına geçildi. Referans için saklanıyor.

> **What to build (eski):** ACL push başarısız olursa Workspace `dirty` işaretlenir (istek başarısız olsa da veri kaybı yok), bir management command sonradan dirty Workspace'leri **idempotent** olarak yeniden push eder. v1'de dağıtık outbox/framework **YOK** — bilinçli olarak basit (§10a).
>
> Adımlar: `SyncStateMixin`'den ayrı bir `acl_sync_status` alanı eklenecekti (`sync_state` katalog senkronuyla karışmasın diye), `GeoServerSecuritySyncService` push sonucunu bu alana yazacaktı, `sync_acl` management command dirty olanları tekrar push edecekti (`--workspace` opsiyonuyla).

</details>
