# Güvenlik ve güven sınırları

- Sistem aracı kuruma bağlanmaz; parola istemez veya saklamaz; çalıştırılabilir emir üretmez.
- Rapor dili doğrudan al/sat/tut talimatı vermez. LLM prompt'u bunu yasaklar.
- `.env` git tarafından dışlanır. Kimlik bilgileri yerelde environment, GitHub'da Actions Secrets içindedir.
- GitHub workflow yalnız `contents: write` izni alır; eşzamanlı state yazımları concurrency grubu ile sıralanır.
- Telegram bot token URL içinde bulunduğundan HTTP hata nesnesi loglanmaz. FRED sorgu URL'sindeki anahtar da rapora/loga taşınmaz.
- LLM çıktısı `extra=forbid` Pydantic şemasıyla doğrulanır. Kaynak URL kümesi girdideki URL ile sınırlandırılır; sembol, olay kimliği, tür ve birincil kaynak durumu değiştirilemez. Bir tekrar sonrası yorum atlanır, olgu korunur.
- AI fan-out olay limiti, bounded concurrency ve üstel retry/backoff ile sınırlandırılır. İçeriği çıkarılmamış SEC metadata'sı LLM'e gönderilmez.
- Sosyal medya doğrulanmış kaynak değildir. Varsayılan haber adaptörü yalnız kullanıcı tarafından doğrulanıp tanımlanmış resmî RSS/Atom adreslerini okur.
- Markdown'dan türetilen HTML allowlist parser ile sanitize edilir. Script/style/iframe/object, event-handler attribute'ları ve HTTP(S) dışı link protokolleri atılır.
- Paywall aşma veya robots/kullanım koşulu ihlali yapan scraper bulunmaz.
- Repository, portföy ve raporlar kişisel finansal veri içerdiği için özel tutulmalıdır. Artifact erişimini de repository üyeleriyle sınırlayın.
- Loglara request header, environment veya secret değeri yazmayın. Debug HTTP loglamasını production'da açmayın.
- Provider uyarıları URL/query/header yerine güvenli scope ve hata sınıfı/durum kodu taşır; FRED anahtarı ve Telegram token'ı hata metnine girmez.

Bağımlılık güncellemelerini inceleyin, Dependabot veya eşdeğer tarama kullanın ve bot token/API anahtarlarını düzenli döndürün. Bir secret loga girerse logu gizlemekle yetinmeyip sağlayıcı tarafında derhal iptal edin.
