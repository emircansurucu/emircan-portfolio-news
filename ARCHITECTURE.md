# Mimari

## Tasarım ilkeleri

1. Sayısal sonuçlar yalnız deterministik Python fonksiyonlarından gelir.
2. LLM, doğrulanmış `MaterialEvent` kaydı dışında bilgi görmez ve hesap yapmaz.
3. Her fiyat, makro gözlem ve olay `SourceRecord` ile URL, yayın/gözlem ve erişim zamanı taşır.
4. Sağlayıcı hataları birbirinden yalıtılır; eksik veri uydurulmaz.
5. Rapor dosyaları başarıyla yazılmadan cadence veya provider checkpoint'i ilerlemez.
6. Her ABD piyasa seansı tek bir kanonik history gözlemine sahiptir.

## Bileşenler

- `models.py`: Pydantic yapılandırma, kaynak, fiyat, olay, AI ve rapor modelleri.
- `calculations.py`: değerleme, Modified Dietz/TWR, döviz dönüşümü, nakit-akışına göre düzeltilmiş katkı, XIRR, düşüş ve oynaklık.
- `providers/base.py`: piyasa, SEC, duyuru, makro, metal, FX, LLM ve teslimat Protocol'leri.
- `providers/`: Yahoo (MVP), EdgarTools, FRED, resmî RSS/Atom, OpenAI ve Telegram adaptörleri.
- `state.py`: JSON/JSONL repository; PostgreSQL ile değiştirilebilir sözleşme.
- `service.py`: hata yalıtımı, yeni olay filtresi, rapor ve commit sıralaması.
- `reporting.py` + `templates/`: Markdown/HTML ve kısa Telegram metni.

```text
SEC / resmî IR / FRED / fiyatlar
              │
              ▼
      Pydantic kaynak kayıtları
         │              │
         ▼              ▼
 deterministik hesap   LLM yorum şeması
         │              │
         └──────┬───────┘
                ▼
        Markdown + HTML
                │
                ▼
      atomik state checkpoint
                │
                ▼
        Telegram (isteğe bağlı)
```

## State ve hata semantiği

`processed_event_ids` SEC accession numarasını veya sembol/başlık/URL SHA-256 fingerprint'ini tutar. Tam haber metni saklanmaz. Event lifecycle keşif, cadence-raporlama, AI durumu ve teslimat durumunu birbirinden ayırır. Başarısız AI denemesi `failed` kalır ve yeniden denenebilir; limit dışı olaylar `deferred`, içeriksiz SEC kayıtları `skipped` olur.

Provider checkpoint'leri scope bazındadır: örneğin `sec:MSFT`, tek bir IR feed hash'i veya `fred:DGS10`. Kısmi batch başarısında yalnız başarılı scope ilerler. Taramalar checkpoint'ten yedi gün geriye taşar; başka cadence için raporlanmamış keşifler kalıcı event registry'den alınır.

History, `market_session` anahtarıyla upsert edilir. Session arası dış nakit akışları zaman ağırlıklı Modified Dietz ile çıkarılır ve getiriler geometrik bağlanır. Drawdown ve yıllıklandırılmış oynaklık bu bağlantılı wealth/return serisini kullanır. Position quantity değişimi kayıtlı alış/satış netiyle uyuşmazsa rapora uyarı eklenir.

Rapor renderer'ı iki dosyayı atomik değiştirir. Ardından history/event audit dosyaları ve en son yetkili `state.json` değiştirilir. Sürecin daha güçlü çok-dosyalı işlem garantisine ihtiyaç duyan sürümü PostgreSQL `StateRepository` ile uygulanabilir.

Telegram state commit'inden sonra kalıcı outbox üzerinden çalışır ve başarısızlığı raporu geçersiz kılmaz. Başarısız öğe artan gecikmeyle retry edilir. Dış sağlayıcıların sembol/feed/şirket/seri bazlı kısmi hataları raporda görünür. İşlenmemiş bir istisna veya rapor yazma hatası checkpoint'i ilerletmez.

## Genişletme noktaları

Her yeni sağlayıcı ilgili Protocol'ü uygular. Lisanslı fiyat kaynağı Yahoo adaptörünün yerine, Anthropic adaptörü OpenAI adaptörünün yanına, PostgreSQL repository ise JSON repository yerine dependency injection ile geçirilebilir. Sayısal mantık sağlayıcılara veya prompt'a taşınmamalıdır.
