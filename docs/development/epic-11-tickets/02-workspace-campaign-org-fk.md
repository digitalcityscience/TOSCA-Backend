# 02 — Workspace & Campaign `organization` FK (expand → backfill → contract)

**Track:** A · **Canonical:** §4, §11 A2

**What to build:** `Workspace` ve `Campaign` satırlarına sahip org'u bağlayan `organization` FK (`on_delete=PROTECT`). Ayrıca `Workspace.visibility` (PRIVATE/PUBLIC, default PRIVATE). Mevcut tüm satırlar seed `dcs` org'una bağlanır — kimse null kalmaz.

**Blocked by:** 01.

**Status:** ✅ done

---

## Neden "wide refactor" (expand–contract)

FK'nin non-null olması mevcut tüm satırları anında kırar → tek migration'da yapılamaz. Canonical §11 A2'nin üç adımı = **expand → backfill → contract**:
1. **expand:** nullable FK ekle (hiçbir şey kırılmaz).
2. **backfill (data migration):** seed `dcs` org'unu yarat, tüm eski satırları ona bağla.
3. **contract:** FK'yi non-null + `on_delete=PROTECT` yap.

## Mevcut durum (kod incelendi 2026-08-12)

Uygulandı. İlgili migration'lar diskte:
- `geodata_providers/migrations/0009_workspace_organization_workspace_visibility.py` (expand + `visibility`)
- `geodata_providers/migrations/0010_workspace_organization_nonnull.py` (contract)
- `campaigns/migrations/0003_campaign_organization.py` (expand)
- `campaigns/migrations/0004_campaign_organization_nonnull.py` (contract)
- `organizations/migrations/0002_seed_dcs_and_backfill.py` (backfill: `dcs` seed + eski satırları bağla)
- `Workspace.Visibility` TextChoices (PRIVATE/PUBLIC) ve `organization` FK modelde mevcut.

## Acceptance criteria

- [x] `Workspace.organization = FK(Organization, on_delete=PROTECT)` (non-null).
- [x] `Workspace.visibility = CharField(choices=[PRIVATE, PUBLIC], default=PRIVATE)`.
- [x] `Campaign.organization = FK(Organization, on_delete=PROTECT)` (non-null).
- [x] Seed org **slug = `dcs`** (mikro-karar §11.1); mevcut tüm Workspace/Campaign satırları buna backfill edildi.
- [x] Migration sırası expand → backfill → contract; `migrate` temiz DB'de ve dolu DB'de yeşil.

## Doğrulama

```
make django-test-unit   # makemigrations --check ile "no changes" beklenir
```
