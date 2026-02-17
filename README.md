# Quiz Game Host

Türkçe, sosyal ve parti tarzı bir genel kültür quiz oyunu. Oyun sunucusu bir LLM tarafından yönetilir ve host promptu sistem mesajı olarak sabitlenir.

## Kurulum

1. `.env.example` dosyasını `.env` olarak kopyala
2. `.env` içine `GEMINI_API_KEY` yaz
3. `python3 app.py`
4. Tarayıcıdan `http://localhost:3000`

## Render ile Online Deploy (Multiplayer)

Bu proje icin online ortamda onerilen yol Render'dir. (`app.py` calisir)

1. Bu klasoru GitHub repo'suna push et
2. Render'da `New +` -> `Blueprint` sec
3. Repo'yu bagla ve `render.yaml` ile olustur
4. Servis env vars'ta `GEMINI_API_KEY` degerini gir
5. Deploy bitince Render URL'sini iki cihazdan ac

## Özellikler

- Tek soru akışı (host kuralları prompt ile zorlanır)
- `Yeni soru`, `Kategori: <X>`, `Cevabı söyle` komutlarını destekleyen sohbet
- Sunucu tarafında oda bazlı ortak konuşma geçmişi
- Node.js gerektirmez, sadece `python3` ile çalışır

## 2 Cihazla VS (Otomatik)

1. İki cihaz da aynı linkten girsin (`http://<senin-ip>:3000`)
2. Her oyuncu kendi adını yazıp `Hazirim` desin
3. Oyun ekranında iki oyuncu da `Hazir` butonuna bassın
4. Her iki cihazda 10'dan geri sayım başlar
5. Soru gelince doğru cevabı ilk veren turu alır (+1 puan)
6. Sonraki tur için iki taraf tekrar `Hazir` basar

## Dosyalar

- `app.py`: API + statik dosya sunumu + Gemini çağrısı
- `public/index.html`: oyun arayüzü
- `public/main.js`: chat istemcisi
- `public/styles.css`: görsel tema

## Streamlit (opsiyonel)

Bu klasorde `streamlit_app.py` da var, ama multiplayer `app.py` kadar uygun degil.

Yerelde:

```bash
pip3 install -r requirements.txt
streamlit run streamlit_app.py
```

Streamlit Cloud:

- Repo'yu GitHub'a push et
- Streamlit Cloud'da yeni app aç
- Main file: `streamlit_app.py`
- Secrets kısmına `GEMINI_API_KEY="..."` ekle
