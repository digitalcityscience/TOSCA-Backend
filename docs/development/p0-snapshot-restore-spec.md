# P0 Snapshot / Restore Spec — TOSCA (Postgres + GeoServer)

**Status:** Design locked (grill tamamlandı). Implementation-ready.
**Scope:** Deploy/upgrade öncesi tek komutlu, aynı-host, bütünsel restore point.
**Date:** 2026-08-21

---

## 1. Kilitlenen kararlar (grill sonucu)

| # | Karar |
|---|---|
| Amaç | Deploy/upgrade/migration/GeoServer-config değişikliği sistemi bozarsa **hızlı geri dönüş** (development/deployment rollback). Disaster recovery değil. |
| Kapsam | **P0 = Postgres + GeoServer data dir + manifest/verify.** Garage full-copy YOK. |
| RPO/RTO | Deploy-snapshot RPO≈0, RTO<1h. PITR/WAL ve kapsamlı scheduled backup **scope dışı**. |
| Q1 Granülerlik | Tek `snapshot_id` altında **bütünsel**; Postgres ve GeoServer **ayrı artefakt**; seçici restore mümkün. |
| Q3 GeoServer SoT | `geoserver_data` **authoritative** → full volume tar zorunlu. `sync_geoserver` backup değil, restore-sonrası verify/reconcile aracı. |
| Q6 Quiesce | Snapshot sırasında **django + geoserver stop** (kısa downtime kabul). `db` açık kalır. |
| Q7 Postgres | `pg_dump -Fc` (logical, custom format). Raw volume tar DEĞİL. |
| Q8 GeoServer | Container stop → **full volume tar.gz**. REST backup eklentisi DEĞİL. |
| Q9 Güvenlik ağı | Restore'dan önce **otomatik pre-restore safety snapshot**. |
| Q10 Manifest+verify | Manifest'te version/gitSHA/migration/checksum; her snapshot'ta **lightweight verify**; **full restore provası periyodik + her GeoServer upgrade öncesi**. |
| Q11 Garage | P0'da **warning-only** referans kontrolü (DB→Garage HEAD), restore'u bloklamaz. |
| Q12 Teslimat | `make snapshot` / `make restore` üst katman + ince `scripts/snapshot.sh`. Local/prod **aynı mekanizma**, sadece `ENV`/volume farkı (mevcut `which-env` pattern). |

### Tasarımı şekillendiren iki repo-kısıtı
1. **Postgres init scriptleri yalnızca boş volume'da çalışır** (`docker/postgis:/docker-entrypoint-initdb.d`). Roller/schema'lar/grant'lar ilk boot'ta `init001.sh` + `reconcile_service_role_passwords.sh` ile kurulur. → Restore'da **pg_data volume'u SİLİNMEZ**; global roller yerinde kalır.
2. **`GEOSERVER_ENABLE_JDBC_CONFIG=false`** → GeoServer katalog/config ve dosya-bazlı katmanlar (Shapefile/GeoTIFF) **filesystem'de** (`geoserver_data` volume). Kaybolan-Shapefile olayının kök nedeni budur; `sync_geoserver` bunu geri getirmez, sadece volume backup getirir.

---

## 2. On-disk layout

```
backups/
  <snapshot_id>/
    manifest.json          # metadata + checksums (aşağıda §3)
    postgres.dump          # pg_dump -Fc çıktısı (tek dosya)
    geoserver_data.tar.gz  # geoserver_data volume içeriğinin tam kopyası
    snapshot.log           # create sırasında stdout/stderr
    verify.log             # verify çalıştıysa
```

- `snapshot_id = <UTC>_<gitSHA8>[_<label>]`, ör: `20260821T142530Z_6380333_pre-epic12`.
- `backups/` **`.gitignore`'a eklenir** (repoya girmez).
- Pre-restore safety snapshot'lar `backups/<id>/` ile aynı formatta ama `manifest.json` içinde `"kind": "safety"` işaretlenir.

---

## 3. `manifest.json` şeması

```json
{
  "snapshot_id": "20260821T142530Z_6380333_pre-epic12",
  "kind": "manual",                     // manual | safety
  "created_at_utc": "2026-08-21T14:25:30Z",
  "env": "prod",                        // dev | prod
  "label": "pre-epic12",
  "git_sha": "6380333",
  "geoserver_version": "<GEOSERVER_VERSION env değeri>",
  "geoserver_image": "ghcr.io/digitalcityscience/tosca-geoserver:<tag>",
  "postgres": {
    "database": "<PG_DATABASE>",
    "dump_format": "custom",
    "migration_head": {"app": "...", "name": "...."},   // django_migrations son satır
    "row_counts": {                     // sanity sayaçları (kilit tablolar)
      "django_migrations": 123,
      "<gis_schema kilit tablo>": 456
    }
  },
  "geoserver": {
    "data_dir": "/geoserver_data/data",
    "layer_count": 42                   // opsiyonel sanity sayacı
  },
  "artifacts": {
    "postgres.dump":       {"sha256": "...", "bytes": 12345},
    "geoserver_data.tar.gz": {"sha256": "...", "bytes": 67890}
  },
  "tooling_version": "p0-snapshot/1"
}
```

- `git_sha` + `geoserver_version` **zorunlu**: restore, snapshot'ın alındığı image ile uyumlu image'a yapılmalı (config uyumsuzluğu = ikinci kaybolan-Shapefile riski). Restore, aktif `GEOSERVER_VERSION` ile manifest'teki değeri karşılaştırıp **uyuşmuyorsa uyarır**.

---

## 4. `make snapshot` akışı (create)

`make snapshot [ENV=dev|prod] [LABEL=...]`

Ortam çözümü mevcut `which-env` ile: `ENV_FILE=.env.$(ENV)`, `COMPOSE_FILE=docker-compose-$(ENV).yml`. Tüm compose çağrıları `docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) ...`.

```text
0. Preflight
   - ENV geçerli mi (dev|prod), COMPOSE_FILE/ENV_FILE var mı (which-env zaten kontrol ediyor)
   - db container UP + healthy mi? (değilse abort)
   - backups/<id>/ oluştur, snapshot.log'a yönlendir

1. snapshot_id üret:  <UTC>_<git rev-parse --short HEAD>[_<LABEL>]

2. Metadata topla (SERVİSLER DURMADAN ÖNCE, db açıkken):
   - migration_head:  exec -T db psql -tA -U $PG_SUPERUSER -d $PG_DATABASE \
       -c "SELECT app,name FROM django_migrations ORDER BY id DESC LIMIT 1"
   - row_counts / layer_count: psql sayaçları

3. QUIESCE:
   docker compose ... stop django geoserver
   # db AÇIK kalır (pg_dump ona bağlanacak)

4. Postgres dump:
   docker compose ... exec -T -e PGPASSWORD=$PG_SUPERPASS db \
     pg_dump -Fc -U $PG_SUPERUSER -d $PG_DATABASE  > backups/<id>/postgres.dump

5. GeoServer volume tar (geoserver STOP durumda, tutarlı):
   GS_CID=$(docker compose ... ps -aq geoserver)      # stopped container da gelir
   docker run --rm --volumes-from $GS_CID \
     -v "$PWD/backups/<id>:/backup" alpine:3 \
     tar czf /backup/geoserver_data.tar.gz -C /geoserver_data/data .

6. UNQUIESCE:
   docker compose ... start geoserver
   # geoserver healthy bekle (compose healthcheck)
   docker compose ... start django

7. Manifest yaz:
   - sha256sum postgres.dump + geoserver_data.tar.gz
   - git_sha, geoserver_version (=$GEOSERVER_VERSION), env, timestamps, sayaçlar
   - manifest.json'ı atomik yaz (tmp → mv)

8. Lightweight verify (§6.1) çalıştır, sonucu yazdır.
   Başarılıysa: "✅ snapshot <id> hazır" ; değilse artefaktı 'suspect' işaretle.
```

**Downtime penceresi:** yalnızca adım 3→6 arası (dump + tar süresi). Deploy penceresinde kabul.

---

## 5. `make restore` akışı (restore)

`make restore SNAPSHOT=<snapshot_id> [ENV=dev|prod] [--yes]`

```text
0. Preflight
   - backups/<SNAPSHOT>/manifest.json var mı, artefakt sha256'ları manifest ile eşleşiyor mu (checksum verify) → değilse ABORT
   - manifest.geoserver_version vs aktif $GEOSERVER_VERSION karşılaştır:
       farklıysa BÜYÜK UYARI + onay iste (config/image uyumu riski)
   - İnteraktif onay: "Bu YIKICI bir işlem. <SNAPSHOT> geri yüklenecek." (--yes ile atlanır)

1. PRE-RESTORE SAFETY SNAPSHOT (Q9):
   make snapshot ENV=$(ENV) LABEL=pre-restore-safety   (kind=safety)
   # başarısızsa restore'u ABORT et (güvenlik ağı olmadan devam etme)

2. QUIESCE:
   docker compose ... stop django geoserver web nginx
   # db AÇIK kalır — pg_restore ona bağlanacak (DB HİÇ DURMAZ)

3. Postgres restore (fresh-DB yöntemi; --clean cascade kırılganlığını önler):
   a. Diğer bağlantıları düşür + DB'yi yeniden yarat (maintenance db 'postgres' üzerinden):
      exec -T -e PGPASSWORD=$PG_SUPERPASS db psql -U $PG_SUPERUSER -d postgres -v ON_ERROR_STOP=1 <<SQL
        SELECT pg_terminate_backend(pid) FROM pg_stat_activity
          WHERE datname='$PG_DATABASE' AND pid<>pg_backend_pid();
        DROP DATABASE IF EXISTS "$PG_DATABASE";
        CREATE DATABASE "$PG_DATABASE" OWNER "$PG_SUPERUSER";
      SQL
      # Global roller (api_user, gs_user...) volume'da yaşıyor, DROP DATABASE onları silmez.
   b. Dump'ı geri yükle (ownership + schema ACL'leri dump içinde):
      exec -T -e PGPASSWORD=$PG_SUPERPASS db \
        pg_restore -U $PG_SUPERUSER -d $PG_DATABASE --no-owner=false \
        < backups/<SNAPSHOT>/postgres.dump
      # (--exit-on-error yerine hataları logla; PostGIS extension notice'ları normal)

4. GeoServer volume restore (geoserver STOP durumda):
   GS_CID=$(docker compose ... ps -aq geoserver)
   docker run --rm --volumes-from $GS_CID \
     -v "$PWD/backups/<SNAPSHOT>:/backup" alpine:3 sh -c '
       find /geoserver_data/data -mindepth 1 -maxdepth 1 -exec rm -rf {} + ;
       tar xzf /backup/geoserver_data.tar.gz -C /geoserver_data/data '

5. RESTART (doğru sıra — senin düzelttiğin):
   docker compose ... start geoserver
   → geoserver healthy bekle (healthcheck: /geoserver/web)
   docker compose ... start django
   → django healthy bekle (/readyz)
   docker compose ... start web nginx   (prod)

6. POST-RESTORE VERIFY (§6):
   - geoengine_smoke_test
   - Garage referans kontrolü (warning-only)
   - Sonucu yazdır. Başarısızsa: "⚠️ restore tamam ama verify FAIL — safety snapshot: <id>"
```

> **Kritik sıra notu:** `db` container hiçbir aşamada durmaz. "Restore" = çalışan `db`'ye `pg_restore` ile yazmak. GeoServer DB'den *sonra* başlar ve healthy olmadan Django başlamaz.

---

## 6. Verify stratejisi

### 6.1 Lightweight (her snapshot + her restore)
- **Artefakt bütünlüğü:** `sha256sum` == manifest. Bozuk → suspect.
- **Dump açılabilirlik:** `pg_restore -l postgres.dump` (TOC listelenebiliyor mu — dosya sağlam mı).
- **Tar bütünlüğü:** `tar tzf geoserver_data.tar.gz >/dev/null`.
- **Restore sonrası:** `geoengine_smoke_test` (mevcut komut) — GeoServer ayakta ve DB ile konuşuyor mu.

### 6.2 Garage referans kontrolü — warning-only (Q11)
- DB'deki media referanslarını gez (originals + derivatives prefix'leri), her biri için Garage'da `HEAD`.
- Eksikleri **rapor et, restore'u bloklama.** Çıktı: `N referans kontrol edildi, M eksik`.
- Mevcut `scripts/list_media_buckets.py` / `scripts/garage_e2e.py` altyapısı temel alınır.

### 6.3 Full restore provası — periyodik + upgrade öncesi (Q10)
- **Ne zaman:** en az periyodik bir kez + **her GeoServer upgrade'inden önce.**
- **Nasıl:** snapshot'ı **local `ENV=dev`** ortamına restore et, smoke-test + gözle katman/harita kontrolü. Local ve prod aynı mekanizma olduğu için bu prova prod'u da doğrular.
- "Backup alındı ama restore edilemiyor" tuzağına karşı tek gerçek güvence budur.

---

## 7. Teslimat: Makefile + ince script

### Makefile target'ları (mevcut `which-env` pattern'iyle)
```make
snapshot: which-env
	@ENV_FILE=$(ENV_FILE) COMPOSE_FILE=$(COMPOSE_FILE) ENV=$(ENV) \
	  scripts/snapshot.sh create --label "$(LABEL)"

restore: which-env
	@test -n "$(SNAPSHOT)" || { echo "SNAPSHOT=<id> gerekli"; exit 1; }
	@ENV_FILE=$(ENV_FILE) COMPOSE_FILE=$(COMPOSE_FILE) ENV=$(ENV) \
	  scripts/snapshot.sh restore --id "$(SNAPSHOT)" $(if $(YES),--yes,)

snapshots: which-env         ## mevcut snapshot'ları listele (manifest özetleri)
	@scripts/snapshot.sh list
```
- `.PHONY` listesine `snapshot restore snapshots` eklenir.
- `help` çıktısına satırlar eklenir.

### `scripts/snapshot.sh` (ince orchestrator)
- Alt komutlar: `create`, `restore`, `list`, `verify`.
- Env değişkenlerini (`ENV_FILE`, `COMPOSE_FILE`, `ENV`, ve `.env.$ENV`'den `PG_*`, `GEOSERVER_VERSION`, `S3_*`) yükler.
- `set -euo pipefail`, tüm compose çağrıları `docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE"`.
- Manifest üretimi için `jq` (veya python one-liner) kullanır.
- Tüm iş mantığı burada; Makefile sadece ince kabuk.

---

## 8. Local vs prod

| Konu | dev | prod |
|---|---|---|
| Compose | `docker-compose-dev.yml` | `docker-compose-prod.yml` |
| pg volume | `pg_data` | `prod_pg_data` |
| geoserver volume | `geoserver_data` | `geoserver_data` |
| GeoServer image | GHCR `:$GEOSERVER_VERSION` | GHCR `:$GEOSERVER_VERSION` (pinned) |
| Ekstra durdurulan | django, geoserver | + web, nginx |
| Downtime toleransı | serbest | kısa deploy penceresi |

- **Mekanizma özdeş.** Script volume'lara isimle değil `--volumes-from <service container>` ile eriştiği için **volume adı farkı görünmez** (project-prefix / dev-prod farkı otomatik çözülür).
- Fark yalnızca `ENV=` ve prod'da ekstra `web nginx` stop/start.

---

## 9. Guardrail'ler / edge case'ler

- **db healthy değilse** snapshot/restore ABORT.
- **Checksum uyuşmazsa** restore ABORT (bozuk artefakt geri yüklenmez).
- **`GEOSERVER_VERSION` manifest ≠ aktif** → restore uyarır, açık onay ister.
- **Pre-restore safety snapshot başarısızsa** restore devam etmez.
- **Restore yıkıcıdır:** interaktif onay (`--yes`/`YES=1` ile CI/otomasyon atlar).
- **Kısmi başarısızlık:** create'te herhangi adım fail → `backups/<id>/` `suspect.flag` ile işaretlenir, "başarılı" sayılmaz.
- **Disk alanı:** create öncesi `backups/` altında yeterli boş alan kontrolü (en az son geoserver_data + dump boyutu kadar).
- **Concurrency:** aynı anda iki snapshot/restore engellenir (basit lock dosyası `backups/.lock`).
- **Seçici restore (Q1):** `--only postgres` / `--only geoserver` flag'leri ile tek artefakt restore (varsayılan ikisi birden). Pre-restore safety yine tam alınır.

---

## 10. Definition of Done (P0)

- [ ] `make snapshot ENV=dev` → `backups/<id>/` altında dump + tar + manifest + geçen lightweight verify üretir; toplam downtime yalnızca dump+tar süresi.
- [ ] `make snapshot ENV=prod` aynısını prod compose ile yapar (web/nginx dahil quiesce).
- [ ] `make restore SNAPSHOT=<id> ENV=dev` → pre-restore safety snapshot alır, DB'yi ve geoserver_data'yı geri yükler, servisleri doğru sırada başlatır, smoke-test geçer.
- [ ] Manifest `git_sha` + `geoserver_version` içerir; restore uyumsuz version'da uyarır.
- [ ] Garage referans kontrolü warning-only çalışır, restore'u bloklamaz.
- [ ] En az **bir kez local'de tam restore provası** yapılıp gözle doğrulanır (kaybolan-Shapefile senaryosu: bir shapefile'ı sil → restore → geri geldi).
- [ ] `README`/`Makefile help`'e kullanım eklenir; `backups/` `.gitignore`'da.

---

## 11. Explicitly OUT of scope (P0)

- Garage full backup / versioning / lifecycle (ayrı iş; P0 sadece warning-only referans kontrolü).
- PITR / WAL archiving.
- Otomatik scheduled/nightly backup + retention.
- Offsite kopyalama otomasyonu (kullanıcı manuel kopyalayacak).
- GeoServer REST Backup/Restore eklentisi.
- Raw pg_data physical volume tar.

---

## 12. Sıradaki önerilen iş (P1, bu spec dışında)
- Nightly scheduled snapshot + retention (N adet tut, eskisini sil).
- Garage için incremental sync (`rclone`/`aws s3 sync` offsite).
- Snapshot'ları offsite'a otomatik kopyalama.
