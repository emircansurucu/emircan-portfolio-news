# Mimari

## Tasarım ilkeleri

1. Sayısal sonuçlar yalnız deterministik Python fonksiyonlarından gelir.
2. LLM, doğrulanmış `MaterialEvent` kaydı dışında bilgi görmez ve hesap yapmaz.
3. Her fiyat, makro gözlem ve olay `SourceRecord` ile URL, yayın/gözlem ve erişim zamanı taşır.
4. Sağlayıcı hataları birbirinden yalıtılır; eksik veri uydurulmaz.
5. Rapor dosyaları başarıyla yazılmadan checkpoint ilerlemez.

## Bileşenler

- `models.py`: Pydantic yapılandırma, kaynak, fiyat, olay, AI ve rapor modelleri.
- `calculations.py`: değerleme, döviz dönüşümü, ağırlıklı katkı, nakit akışı ayrımı, XIRR, düşüş ve oynaklık.
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

`processed_event_ids` SEC accession numarasını veya sembol/başlık/URL SHA-256 fingerprint'ini tutar. Tam haber metni saklanmaz. Taramalar son başarılı zamandan yedi gün geriye taşar; bu, geçici sağlayıcı arızasından sonra kaçırılan kaydı yakalarken fingerprint tekrarları önler.

Rapor renderer'ı iki dosyayı atomik değiştirir. Ardından history/event audit dosyaları ve en son yetkili `state.json` değiştirilir. Sürecin daha güçlü çok-dosyalı işlem garantisine ihtiyaç duyan sürümü PostgreSQL `StateRepository` ile uygulanabilir.

Telegram state commit'inden sonra çalışır ve başarısızlığı raporu geçersiz kılmaz. Dış sağlayıcıların kısmi hataları raporda görünür. İşlenmemiş bir istisna veya rapor yazma hatası checkpoint'i ilerletmez.

## Genişletme noktaları

Her yeni sağlayıcı ilgili Protocol'ü uygular. Lisanslı fiyat kaynağı Yahoo adaptörünün yerine, Anthropic adaptörü OpenAI adaptörünün yanına, PostgreSQL repository ise JSON repository yerine dependency injection ile geçirilebilir. Sayısal mantık sağlayıcılara veya prompt'a taşınmamalıdır.

