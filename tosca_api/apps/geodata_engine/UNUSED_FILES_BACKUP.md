# Kullanılmayan Dosyalar Backup - 4 Şubat 2026

## Açıklama
Session-based + Plugin architecture'e geçilirken aşağıdaki dosyalar kullanılmayacak hale geldi.
Bu dosyalar ileride referans için saklanıyor.

## 📂 Kullanılmayan Dosyalar

### 1. admin_old.py
**Durum**: Template-heavy eski admin system backup
**Nedeni**: Yeni session-based admin.py sistemi aktif

### 2. Template Dosyaları (templates/admin/geodata_engine/)
**Durum**: Template system kaldırıldı
**Nedeni**: Session-based approach ile gereksiz

- `manage_engine.html`
- `workspace_introspect.html`
- `add_workspace.html`
- `sync_confirm.html`
- `engine_selector.html`
- `copy_store_confirmation.html`
- `upload_layer.html`
- `add_store.html`
- `layer_upload.html`

### 3. geoserver/engine.py
**Durum**: Eski GeoServer engine implementation
**Nedeni**: `geoserver/client.py` wrapper kullanılıyor

## 🔍 Kullanımda Olan Dosyalar

### Core
- `admin.py` (session-filtered admin)
- `models.py` (domain models)
- `apps.py`, `__init__.py`

### Architecture
- `middleware.py` (session management)
- `plugins.py` (plugin registry)
- `actions.py` (admin actions)
- `sync_service.py` (GeoServer sync)

### GeoServer
- `geoserver/client.py` (REST wrapper)
- `geoserver/__init__.py`

### Utilities
- `exceptions.py`
- `encryption.py`
- `templatetags/` (engine tags)

### Management Commands
- `management/commands/` (setup, test, sync commands)

---

**Not**: Bu dosyalar silinmeden önce backup alındı.