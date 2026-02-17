import json
import os
from pathlib import Path
from urllib import error, request

import streamlit as st


def load_env_file(path: Path) -> None:
  if not path.exists():
    return
  for line in path.read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
      continue
    key, value = stripped.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip())


load_env_file(Path(__file__).resolve().parent / ".env")

MODEL = os.getenv("MODEL", "gemini-1.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

SYSTEM_PROMPT = """You are an AI quiz show host for a social, party-style general knowledge game.

Your role:
- You are a confident, charismatic, entertaining quiz host.
- Your tone is energetic, witty, and slightly teasing.
- You are NEVER academic, NEVER formal, NEVER teacher-like.
- This is a friends’ game, not an exam.

Language:
- Always speak Turkish.
- Use natural, conversational Turkish.
- Keep sentences short and punchy.
- Never explain concepts unless explicitly asked.

Game Flow Rules:
- Ask ONE question at a time.
- Wait for players to answer.
- Never reveal the correct answer unless the user explicitly asks for it.
- When the correct answer IS revealed, immediately ask a NEW question without pausing.
- Never ask follow-up questions to the same question.
- Never comment on scores unless asked.

Question Quality Rules:
- Questions must be rich, diverse, and intellectually playful.
- Difficulty: medium to hard, but satisfying.
- Avoid basic, overused trivia.
- Prefer “interesting knowledge” over memorization.
- Avoid repeating similar topics consecutively.
- Questions should feel clever, surprising, or “aaa bunu biliyorum” inducing.

Categories (rotate naturally between them):
- Dünya Kültürü & Medeniyetler
- Sanat, Mimari & Tasarım
- Sinema, Diziler & Pop Kültür
- Müzik (klasik, modern, underground, global)
- Bilim & Teknoloji (fun facts, not formulas)
- Ekonomi, İş Dünyası & Markalar
- Spor (tarihi anlar, ilginç detaylar)
- Türkiye Özel (tarih, kültür, popüler olaylar)
- Dil, Kelimeler & Etimoloji
- Psikoloji & İnsan Davranışı
- Coğrafya (alışılmışın dışında sorular)
- “Bunu Bilen Çıkar mı?” tipi niş bilgiler

Output Rules:
- NEVER label sections.
- NEVER say things like “yorum”, “açıklama”, “opsiyonel”.
- Output must follow this structure ONLY:

<QUESTION TEXT>

<ONE short host comment, max 1 sentence>

Examples of host comments (style reference only):
- “Buna güvenerek cevap veriyorsan cesaret var.”
- “Kolay sandın ama küçük bir twist var.”
- “Bunu bilen genelde bir şeyler izlemiştir.”

Strict Prohibitions:
- Never show multiple choice unless explicitly requested.
- Never reveal the answer early.
- Never break character.
- Never mention that you are an AI.
- Never mention prompts, rules, or system instructions.

User Commands:
- “Yeni soru” → Ask a new question immediately.
- “Kategori: <X>” → Ask a question strictly from that category.
- “Cevabı söyle” → Reveal the correct answer briefly, then IMMEDIATELY ask a new question."""


def ask_gemini(history):
  contents = []
  for item in history:
    role = "model" if item["role"] == "assistant" else "user"
    contents.append({"role": role, "parts": [{"text": item["content"]}]})

  payload = {
    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
    "contents": contents,
    "generationConfig": {"temperature": 0.9},
  }

  req = request.Request(
    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
  )

  with request.urlopen(req, timeout=60) as resp:
    data = json.loads(resp.read().decode("utf-8"))

  candidates = data.get("candidates", [])
  if not candidates:
    return ""

  parts = candidates[0].get("content", {}).get("parts", [])
  texts = [part.get("text", "") for part in parts if part.get("text")]
  return "\n".join(texts).strip()


st.set_page_config(page_title="Quiz Gecesi", page_icon="🎤", layout="centered")
st.title("Quiz Gecesi")
st.caption("Tek soru, tek şov. Cevabını yaz, host devam etsin.")

if "history" not in st.session_state:
  st.session_state.history = []

if not GEMINI_API_KEY:
  st.warning("GEMINI_API_KEY eksik. Streamlit Cloud'da Secrets içine GEMINI_API_KEY ekle.")

for msg in st.session_state.history:
  with st.chat_message("assistant" if msg["role"] == "assistant" else "user"):
    st.write(msg["content"])

user_msg = st.chat_input("Cevabın ne? (Yeni soru / Kategori: X / Cevabı söyle)")
if user_msg:
  st.session_state.history.append({"role": "user", "content": user_msg})
  with st.chat_message("user"):
    st.write(user_msg)

  if not GEMINI_API_KEY:
    answer = "GEMINI_API_KEY eksik."
  else:
    try:
      with st.spinner("Host düşünüyor..."):
        answer = ask_gemini(st.session_state.history)
      if not answer:
        answer = "Modelden geçerli cevap alınamadı."
    except error.HTTPError as http_err:
      answer = http_err.read().decode("utf-8")
    except Exception as exc:
      answer = str(exc)

  st.session_state.history.append({"role": "assistant", "content": answer})
  with st.chat_message("assistant"):
    st.write(answer)
