# Veri kaynakları ve sınırlamalar

| Alan | MVP kaynağı | Niteliği | Not |
|---|---|---|---|
| SEC formları | SEC EDGAR, EdgarTools | Birincil | 10-K, 10-Q, 8-K, Form 4, S-3 ve ilgili 424B formları |
| Şirket duyuruları | Yapılandırılmış resmî RSS/Atom | Birincil | HTML scraper yok; URL'yi IR sayfasından doğrulamak kullanıcı sorumluluğunda |
| ABD makro | FRED API | Birincil/resmî dağıtım | CPIAUCSL, UNRATE, FEDFUNDS, DGS10 |
| Hisse/ETF/endeks | Yahoo chart | Gecikmeli, resmî olmayan | MVP; lisanslı feed ile değiştirilmelidir |
| Altın/gümüş | Yahoo vadeli işlem göstergesi | Gecikmeli, resmî olmayan | Troy ons değeri deterministik biçimde gram USD'ye çevrilir; spot fiyat olmayabilir |
| USD/TRY, DXY | Yahoo chart | Gecikmeli, resmî olmayan | Aynı zamanlı kotasyon garantisi yok |

İzlenen semboller MSFT, RKLB, ASTS, VOO, QQQM; karşılaştırmalar SP500 (`^GSPC`) ve NASDAQ100 (`^NDX`); emtialar `GC=F`, `SI=F`; kur `TRY=X` eşlemesidir.

Henüz canlı adaptörü bulunmayan kapsam: TCMB/EVDS politika faizi ve USD/TRY, TÜİK Türkiye enflasyonu, doğrudan BLS/Federal Reserve, kurumsal kazanç/makro takvim, temettü/tax belge içe aktarımı, lisanslı gerçek zamanlı fiyat. Arayüzler yeni `MacroDataProvider`, `MarketDataProvider`, `FxProvider` uygulamalarını kabul eder.

Fiyat sağlayıcı arızası raporu çökertmez; sayı “doğrulanamadı” olur. Her maddi kayıtta kaynak URL'si ile yayın/gözlem ve erişim zamanı gösterilir. Telifli haber tam metni state'e yazılmaz; başlık, kısa özet, URL, zaman, sınıflandırma ve hash saklanır.

Resmî başlangıç sayfaları, RSS/Atom adresi doğrulamak için:

- Microsoft Investor Relations
- Rocket Lab Investor Relations
- AST SpaceMobile Investor Relations
- Vanguard VOO ürün/duyuru sayfaları
- Invesco QQQM ürün/duyuru sayfaları

Sayfa yolları ve yayın mekanizmaları değişebildiği için uygulama bunların HTML yapısını hard-code etmez. Yalnız kurumun yayımladığı güncel feed URL'lerini `IR_FEEDS_JSON` içine ekleyin.
