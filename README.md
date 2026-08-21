# Kişisel Yatırım İzleme ve Araştırma Ajanı

Bu proje, kişisel portföyü deterministik olarak değerleyen; SEC başvuruları, resmî şirket duyuruları, makro göstergeler ve piyasa verilerini kaynaklarıyla arşivleyen bir araştırma sistemidir. Günlük, haftalık ve aylık Türkçe Markdown/HTML raporları üretir ve isteğe bağlı kısa Telegram özeti gönderir.

Bu bir yatırım danışmanı veya işlem sistemi değildir. Aracı kuruma bağlanmaz; emir oluşturmaz, simüle etmez veya göndermez. Sayılar Python tarafından hesaplanır. LLM yalnızca doğrulanmış yapılandırılmış kayıtları yorumlar ve çıktısı Pydantic şemasından geçirilir.

## Mimari

Akış `sağlayıcılar → doğrulanmış modeller → deterministik hesaplamalar → isteğe bağlı AI yorumu → Markdown/HTML → state checkpoint → Telegram` şeklindedir. Her dış sistem bir Protocol arayüzünün arkasındadır. Ayrıntılar [ARCHITECTURE.md](ARCHITECTURE.md), güvenlik modeli [SECURITY.md](SECURITY.md), kaynak kapsamı [DATA_SOURCES.md](DATA_SOURCES.md) içindedir.

Kalıcı MVP durumu özel repository içinde tutulur:

```text
data/state.json                 # checkpoint, fingerprint ve rapor meta verisi
data/portfolio_history.jsonl    # deterministik değer geçmişi
data/processed_events.jsonl     # telifli tam metin değil, olay meta verisi
reports/                        # Markdown ve HTML
```

`state.json` yetkili commit işaretidir. Başarısız rapor üretimi checkpoint'i değiştirmez. Kaynak geçici olarak başarısız olduğunda yedi günlük örtüşmeli yeniden tarama ve fingerprint tekilleştirmesi olası veri boşluğunu azaltır.

## Kurulum

Python 3.12 gerekir.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Çevrimdışı, anahtarsız doğrulama:

```bash
python -m investment_agent daily --dry-run
```

Canlı komutlar:

```bash
python -m investment_agent daily
python -m investment_agent weekly
python -m investment_agent monthly
```

LLM anahtarı yoksa deterministik rapor yine üretilir; AI bölümü “kullanılamıyor” olarak işaretlenir. `--dry-run` sabit fixture kullanır, ağa çıkmaz, state değiştirmez ve Telegram göndermez.

## Yapılandırma ve sırlar

`.env.example` dosyasını `.env` olarak kopyalayın. Gerekli/isteğe bağlı değerler:

| Değer | Amaç | Gerekli mi? |
|---|---|---|
| `SEC_IDENTITY` | SEC adil erişim kimliği (`Ad Soyad e-posta`) | SEC için evet |
| `FRED_API_KEY` | FRED gözlemleri | FRED için evet |
| `OPENAI_API_KEY` + `OPENAI_MODEL` | Şema doğrulamalı AI yorumu | Hayır |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Kısa bildirim | Hayır |
| `IR_FEEDS_JSON` | Sembol → yalnızca resmî RSS/Atom URL listesi | Duyurular için |
| `REPORT_BASE_URL` | Telegram'daki tam rapor bağlantısının tabanı | Hayır |

Kod bir model adı varsaymaz. `OPENAI_MODEL` değerini erişiminize, maliyet ve kalite tercihinize göre açıkça verin. Anthropic alanları ayrılmıştır fakat adaptörü henüz uygulanmamıştır.

Örnek IR ayarı:

```env
IR_FEEDS_JSON={"MSFT":["RESMI_RSS_URL"],"RKLB":["RESMI_RSS_URL"],"ASTS":["RESMI_RSS_URL"]}
```

RSS/Atom URL'sini şirketin resmî yatırımcı ilişkileri sayfasından doğrulayın. HTML kazıma varsayılan olarak yapılmaz; bu tercih kullanım koşulları ve kırılganlık riskini azaltır.

## Portföyü değiştirme ve yeni varlık ekleme

Gerçek değerler [portfolio.yaml](portfolio.yaml), açıklamalı kopya [portfolio.example.yaml](portfolio.example.yaml) içindedir. `quantity`, `average_cost_usd`, `asset_type` ve `unit` alanlarını güncelleyin. Yatırma/çekme, alış/satış, temettü ve komisyon için `transactions` listesine tarihli kayıt ekleyin. Yalnızca `deposit` ve `withdrawal` dış nakit akışı sayılır; alış ve yeni yatırılan para performansla karıştırılmaz.

Yeni bir varlık için YAML pozisyonunu ekleyin ve piyasa adaptörünün sembol eşlemesinde desteklendiğini kontrol edin. Yeni emtia/baz para için uygun `PreciousMetalsProvider` veya `FxProvider` adaptörü eklenmelidir.

## Telegram bot kurulumu

1. Telegram'da BotFather üzerinden bot oluşturun ve token'ı yalnızca secret olarak saklayın.
2. Bota mesaj gönderip güvenilir yöntemle chat ID'nizi öğrenin.
3. `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` değerlerini yerelde `.env`, GitHub'da Actions Secrets olarak girin.
4. `REPORT_BASE_URL` yoksa mesaj repository içindeki rapor yolunu gösterir; herkese açık bağlantı üretmez.

Telegram başarısızlığı tamamlanmış raporu veya checkpoint'i geri almaz. İstek URL'sindeki bot token hata kayıtlarına yazılmaz.

## GitHub Actions

[investment-reports.yml](.github/workflows/investment-reports.yml) manuel çalıştırma ile üç UTC cron içerir:

- Günlük: hafta içi 21:17 UTC, Türkiye'de ertesi gün 00:17; ABD kapanışı sonrası.
- Haftalık: Pazar 18:23 UTC / Türkiye 21:23.
- Aylık: ayın ilk günü 18:37 UTC / Türkiye 21:37.

GitHub repository Settings → Secrets and variables → Actions altında yukarıdaki sırları, `OPENAI_MODEL` ve `REPORT_BASE_URL` için variables değerlerini tanımlayın. Workflow önce Ruff ve pytest çalıştırır, sonra raporu üretir; artifact yükler ve yalnız başarılı komuttan sonra state/raporları commit eder. `contents: write` workflow'un gereken tek yükseltilmiş iznidir. Repository özel tutulmalıdır.

Günlük canlı komut NYSE takvimini kontrol eder. Tatil/hafta sonunda normal fiyat raporunu atlar fakat önemli olay taramasını sürdürür.

## Maliyetler, limitler ve veri kısıtları

- Yahoo chart adaptörü gecikmeli/resmî olmayan MVP kaynağıdır; lisanslı fiyat garantisi vermez.
- SEC kimliğinde gerçek iletişim bilgisi ve makul istek sıklığı kullanın.
- FRED anahtarı ücretsiz olabilir fakat kota/koşullar sağlayıcıya aittir.
- LLM yalnız yeni olaylar için çağrılır; maliyet seçilen modele ve olay sayısına bağlıdır.
- Kaynakların kapanış saatleri farklıdır. Rapor her kayıt için kaynak ve zaman damgası verir.
- TCMB/EVDS, TÜİK, BLS/Federal Reserve doğrudan adaptörleri; kazanç/makro takvimi; lisanslı piyasa feed'i ve hedef takibi sonraki aşamadır.
- VOO/QQQM resmî sayfaları HTML/RSS yapılarına göre `IR_FEEDS_JSON` ile bağlanmalıdır; varsayılan kırılgan scraper yoktur.

## Yeni LLM sağlayıcısı ekleme

`providers/base.py` içindeki `LLMProvider.analyze` sözleşmesini uygulayın; `MaterialEvent` alıp `AIEventAnalysis` döndürün. Çıktıyı Pydantic ile doğrulayın, yalnız olayda verilen URL'leri kabul edin ve doğrulama hatasında en fazla bir tekrar yapın. Ardından `cli.py` factory seçimine sağlayıcıyı ekleyin. Anthropic için ortam alanları hazırdır; mevcut CLI onu etkinleştirmez.

## Test ve sorun giderme

```bash
ruff check .
ruff format --check .
pytest
python -m investment_agent daily --dry-run
```

- “Portföy değerlemesi ... yapılamadı”: başarısız sağlayıcılar ve eksik semboller rapor başındadır.
- SEC çalışmıyor: `SEC_IDENTITY` biçimini ve EdgarTools sürümünü kontrol edin.
- AI yok: hem anahtar hem model adı gerekir; deterministik çıktı etkilenmez.
- Duyuru yok: resmî RSS/Atom URL'lerini `IR_FEEDS_JSON` içine koyun.
- GitHub push reddedildi: Actions için read/write workflow iznini ve branch protection kurallarını kontrol edin.

## Uyarı

Bu sistem yalnızca bilgilendirme ve araştırma amaçlıdır; kişiselleştirilmiş yatırım tavsiyesi değildir. Üretilen hiçbir metin alım, satım veya tutma talimatı değildir. Finansal karar vermeden önce bütün verileri birincil kaynaklardan doğrulayın ve uygun profesyonel desteği değerlendirin.

