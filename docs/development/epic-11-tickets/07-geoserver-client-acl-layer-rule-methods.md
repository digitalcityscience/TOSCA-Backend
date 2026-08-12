# 07 — `GeoServerClient` ACL layer-rule metodları (`add/update/delete`)

**Track:** C (GeoServer ACL — çok kritik) · **Canonical:** §5(c) canlı doğrulama, §8, §11 C1

**What to build:** `GeoServerClient`'a GeoServer **Data Security ACL** kurallarını yazan üç metot: `add_layer_rule`, `update_layer_rule`, `delete_layer_rule`. `geoserver-rest` bu endpoint'i sarmıyor → mevcut `GeoServerClient._request()` (basic-auth raw REST) üzerinden gidilir.

**Blocked by:** None — can start immediately (yalnız client metodu; sync servisi ayrı, ticket 08).

**Status:** ✅ done

---

## Mevcut durum (kod incelendi 2026-08-12)

`geodata_providers/geoserver/client.py::GeoServerClient` 49 metotlu, `_request(method, path, **kwargs)` raw REST helper'ı var (basic-auth). **ACL/layer-rule metodu YOK** (canonical §8: "ACL metodu YOK" boşluğu tam burası). Katalog ops (workspace/store/layer/style) mevcut ama security ACL değil.

## Canlı doğrulanmış endpoint sözleşmesi (canonical §5c)

Çalışan GeoServer'da (`localhost:8080`) manuel test edildi:
```
POST /geoserver/rest/security/acl/layers
Content-Type: application/json
{"hamburg.bezirke.r": "db-data-test-rol"}          → HTTP 200
```
- **Key formatı:** `<workspace>.<layer>.<access>`, `access ∈ {r, w, a}`, `<layer>=*` → workspace-geneli.
- **Değer:** virgülle ayrılmış rol string'i (ör. `"ROLE_DCS_READER"` veya `"ROLE_A,ROLE_B"`).
- **HTTP fiil semantiği (ÜÇÜNÜ AYIR):**
  - yeni key → **`POST`** `/rest/security/acl/layers`
  - var olan key'i güncelle → **`PUT`** `/rest/security/acl/layers` (`POST` var olan key'de **hata** verir)
  - sil → **`DELETE`** `/rest/security/acl/layers/{key}` (key path'te; nokta içeren key'in encode'una dikkat).
- Daha spesifik kural (`ws.layer.r`) global `*.*.r`'yi o layer için **ezer**.

## Adımlar

1. `GeoServerClient`'a üç metot ekle (raw REST, basic-auth, `_request` üzerinden):
   - `add_layer_rule(self, key: str, roles: str) -> ...` → `POST` body `{key: roles}`.
   - `update_layer_rule(self, key: str, roles: str) -> ...` → `PUT` body `{key: roles}`.
   - `delete_layer_rule(self, key: str) -> ...` → `DELETE /rest/security/acl/layers/{key}`.
2. **Idempotency yardımcısı (opsiyonel ama önerilir):** `set_layer_rule(key, roles)` — önce var mı bak / ya da POST dener, 4xx "already exists" ise PUT'a düş. Ticket 08'in idempotent push'u (§10a) bunu ister; `add` vs `update` ayrımını 08'e bırakmak yerine burada `set_layer_rule` sağlamak sync servisini sadeleştirir. **Karar:** `set_layer_rule` ekle; `add`/`update`/`delete` primitive'leri de expose kalsın.
3. Hata dönüşleri sınıfın mevcut `OperationResult` / `validate_response` desenine uy — diğer metotlarla tutarlı (`create_workspace` vb.'ye bak).
4. Key encoding: nokta ayraç anlamlı; workspace/layer isimlerinde nokta beklenmiyor ama DELETE'te path segment encode'unu doğrula.

## Acceptance criteria

- [x] `add_layer_rule` → `POST /rest/security/acl/layers`, body `{"<ws>.<layer>.<access>": "<roles>"}`.
- [x] `update_layer_rule` → `PUT` aynı endpoint (var olan key).
- [x] `delete_layer_rule` → `DELETE /rest/security/acl/layers/{key}` (key `urllib.parse.quote` ile encode edilir).
- [x] `set_layer_rule(key, roles)` idempotent (yeni→POST, varsa→PUT via `get_layer_rules()`); tekrar çağrıda hata vermez.
- [x] Dönüşler sınıfın mevcut `OperationResult` desenine uygun.
- [x] Birim test: `_request` mock'lanarak doğru fiil+path+body üretildiği doğrulanır (`test_geoserver_client_acl.py`, 11 test).

**Status: done** (2026-08-12).

**Netleştirme (2026-08-12, kullanıcı sorusu üzerine):** `add/update/delete_layer_rule` isimleri GeoServer'ın kendi endpoint adından geliyor (`/rest/security/acl/layers`) — Django'nun layer-seviyeli ACL yazacağı anlamına gelmiyor. Bunlar generic primitifler; `key`'i çağıran taraf üretir. Epic-11 faz 1 (ticket 08) bunları **yalnızca workspace-geneli** key ile çağıracak (`<ws>.*.<access>`, `layer="*"`) — gerçek per-layer key'ler kapsam dışı (v2, canonical §6). Rol kaynağı: workspace'i açan kullanıcının o anki token rolü **değil**, `Workspace.organization` + `visibility`'den türeyen convention rolü (canonical §5c: PRIVATE→`ROLE_<SLUG>_READER/WRITER`, PUBLIC read→`*`). `client.py`'deki ACL bloğunun docstring'i bunu netleştirecek şekilde güncellendi.

## Doğrulama

```
make django-test-unit    # mock'lu client testleri
```
Gerçek GeoServer'a karşı uçtan uca doğrulama ticket 10'da.

## Canonical atıfları
§5(c) canlı doğrulama (endpoint sözleşmesi) · §8 "ACL metodu YOK" · §11 C1.
