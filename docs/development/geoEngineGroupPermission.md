[TODO - GeoDataEngine Group Authorization]

Amaç:
GeoDataEngine yapısına grup bazlı yetkilendirme eklemek (Keycloak ile uyumlu), böylece kullanıcı sadece izinli olduğu Engine/Workspace/Store/Layer kayıtlarını görebilsin.

Hedef model alanları:
- GeodataEngine.allowed_groups (M2M -> auth.Group, blank=True)
- Workspace.allowed_groups (M2M -> auth.Group, blank=True)
- Store.allowed_groups (M2M -> auth.Group, blank=True)
- Layer.allowed_groups (M2M -> auth.Group, blank=True)

Kurallar:
- allowed_groups boş ise: varsayılan görünürlük politikası net tanımlanacak (public ya da deny-by-default karar verilecek).
- superuser/staff bypass davranışı açık belirlenecek.
- Public layer + group kuralı çakışırsa öncelik tanımlanacak.

API/DRF:
- queryset filtreleri request.user gruplarına göre uygulanacak.
- anonymous kullanıcı yalnızca public kayıtları görecek.
- operations endpointleri (publish/unpublish/sync/validate) role/permission kontrollü olacak.

Keycloak entegrasyonu:
- Keycloak role/group -> Django Group mapping stratejisi netleştirilecek.
- Login sırasında group sync yapılıp yapılmayacağı belirlenecek.
- Kurumsal departman yapısı group isim standardına bağlanacak.

Migration/uygulama sırası:
1) Model alanları + migration
2) Admin form ve list filtreleri
3) DRF queryset permission filtreleri
4) Testler (model + api + permission)
5) Dokümantasyon (architecture + auth mapping)

Not:
Bu iş POC sonrası faza alınacak. Öncelik Console app ve mevcut GeoServer akışının stabil çalışması.
