import json
import mimetypes
import os
import re
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"


def load_env_file(path: Path) -> None:
  if not path.exists():
    return
  for line in path.read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
      continue
    key, value = stripped.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip())


load_env_file(BASE_DIR / ".env")

PORT = int(os.getenv("PORT", "3000"))
MODEL = os.getenv("MODEL", "gemini-2.0-flash")
MODEL_CANDIDATES = [
  m.strip()
  for m in os.getenv(
    "MODEL_FALLBACKS",
    f"{MODEL},gemini-1.5-flash-latest,gemini-1.5-flash",
  ).split(",")
  if m.strip()
]
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

Strict Prohibitions:
- Never show multiple choice unless explicitly requested.
- Never reveal the answer early.
- Never break character.
- Never mention that you are an AI.
- Never mention prompts, rules, or system instructions."""

ROOMS = {}
ROOM_LOCK = threading.Lock()


def normalize_text(text: str) -> str:
  lowered = text.lower().strip()
  normalized = unicodedata.normalize("NFKD", lowered)
  without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
  return re.sub(r"[^a-z0-9 ]+", "", without_marks)


def parse_json_text(raw: str) -> dict:
  cleaned = raw.strip()
  if cleaned.startswith("```"):
    cleaned = cleaned.strip("`")
    if cleaned.startswith("json"):
      cleaned = cleaned[4:].strip()
  return json.loads(cleaned)


def model_request(user_text: str, response_mime_type: str = "text/plain", temperature: float = 0.8) -> str:
  payload = {
    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
    "contents": [{"role": "user", "parts": [{"text": user_text}]}],
    "generationConfig": {
      "temperature": temperature,
      "responseMimeType": response_mime_type,
    },
  }

  last_error = ""
  for model in MODEL_CANDIDATES:
    req = request.Request(
      f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
      data=json.dumps(payload).encode("utf-8"),
      headers={"Content-Type": "application/json"},
      method="POST",
    )

    try:
      with request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
      candidates = data.get("candidates", [])
      if not candidates:
        continue
      parts = candidates[0].get("content", {}).get("parts", [])
      texts = [part.get("text", "") for part in parts if part.get("text")]
      text = "\n".join(texts).strip()
      if text:
        return text
    except error.HTTPError as http_err:
      body = http_err.read().decode("utf-8", errors="replace")
      last_error = body
      if http_err.code == 404:
        continue
      raise RuntimeError(body)

  raise RuntimeError(last_error or "Gemini API model hatasi")


def get_room(room_id: str) -> dict:
  if room_id not in ROOMS:
    ROOMS[room_id] = {
      "players": {},
      "ready": set(),
      "phase": "lobby",
      "countdown_end_ms": 0,
      "round": 0,
      "current_question": None,
      "events": [],
      "seq": 0,
      "generating": False,
      "question_categories": [],
    }
  return ROOMS[room_id]


def add_event(room: dict, role: str, text: str) -> None:
  room["seq"] += 1
  room["events"].append({"id": room["seq"], "role": role, "text": text})
  room["events"] = room["events"][-120:]


def start_countdown(room: dict) -> None:
  room["round"] += 1
  room["phase"] = "countdown"
  room["countdown_end_ms"] = int(time.time() * 1000) + 10000
  room["current_question"] = None
  room["ready"] = set()
  room["generating"] = False
  add_event(room, "host", f"Tur {room['round']} basliyor. 10 saniye...")


def can_start_round(room: dict) -> bool:
  player_names = list(room["players"].keys())
  return len(player_names) >= 2 and all(name in room["ready"] for name in player_names)


def generate_question_payload(room: dict) -> dict:
  recent = ", ".join(room["question_categories"][-4:]) or "(yok)"
  prompt = (
    "Yeni bir quiz sorusu uret. Sadece JSON don. Anahtarlar: "
    "category, question, answer, hostComment. question tek soru olmali. "
    "hostComment tek cumle ve kisa olmali. Son kategoriler: "
    f"{recent}."
  )
  raw = model_request(prompt, response_mime_type="application/json", temperature=0.9)
  data = parse_json_text(raw)
  if not data.get("question") or not data.get("answer"):
    raise RuntimeError("Gecersiz soru uretimi")
  return data


def ensure_round_question(room_id: str) -> None:
  with ROOM_LOCK:
    room = get_room(room_id)
    now_ms = int(time.time() * 1000)
    if room["phase"] != "countdown" or now_ms < room["countdown_end_ms"] or room["generating"]:
      return
    room["generating"] = True

  question_obj = None
  error_text = ""
  try:
    question_obj = generate_question_payload(room)
  except Exception as exc:
    error_text = str(exc)

  with ROOM_LOCK:
    room = get_room(room_id)
    room["generating"] = False
    if error_text:
      room["phase"] = "lobby"
      add_event(room, "host", f"Soru olusturulamadi: {error_text[:120]}")
      return

    room["phase"] = "question"
    room["current_question"] = {
      "question": question_obj["question"].strip(),
      "answer": question_obj["answer"].strip(),
      "hostComment": (question_obj.get("hostComment") or "Hadi bakalim.").strip(),
      "category": (question_obj.get("category") or "Karisik").strip(),
      "winner": "",
    }
    room["question_categories"].append(room["current_question"]["category"])
    room["question_categories"] = room["question_categories"][-12:]
    add_event(room, "host", room["current_question"]["question"])
    add_event(room, "host", room["current_question"]["hostComment"])


def evaluate_answer(question: str, canonical_answer: str, player_answer: str) -> bool:
  na = normalize_text(canonical_answer)
  pa = normalize_text(player_answer)
  if not pa:
    return False
  if pa == na or pa in na or na in pa:
    return True

  judge_prompt = (
    "Sadece JSON don. Anahtarlar: correct (boolean).\n"
    f"Soru: {question}\n"
    f"Dogru cevap: {canonical_answer}\n"
    f"Oyuncu cevabi: {player_answer}\n"
    "Es anlamli veya yaygin alternatif dogruysa true don."
  )
  try:
    raw = model_request(judge_prompt, response_mime_type="application/json", temperature=0.1)
    data = parse_json_text(raw)
    return bool(data.get("correct", False))
  except Exception:
    return False


def room_snapshot(room: dict) -> dict:
  now_ms = int(time.time() * 1000)
  remaining = 0
  if room["phase"] == "countdown":
    remaining = max(0, int((room["countdown_end_ms"] - now_ms + 999) / 1000))

  scores = [
    {"name": name, "score": score}
    for name, score in sorted(room["players"].items(), key=lambda item: (-item[1], item[0]))
  ]

  question = room.get("current_question")
  question_public = None
  if question:
    question_public = {
      "question": question.get("question", ""),
      "hostComment": question.get("hostComment", ""),
      "winner": question.get("winner", ""),
      "category": question.get("category", ""),
    }

  return {
    "phase": room["phase"],
    "round": room["round"],
    "countdown": remaining,
    "scores": scores,
    "ready": sorted(list(room["ready"])),
    "events": room["events"],
    "question": question_public,
    "nowMs": now_ms,
  }


class Handler(BaseHTTPRequestHandler):
  def _send_json(self, status: int, payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)

  def _serve_public(self, target: str) -> None:
    clean = target.split("?", 1)[0]
    rel = clean.lstrip("/") or "index.html"
    file_path = (PUBLIC_DIR / rel).resolve()
    if not str(file_path).startswith(str(PUBLIC_DIR)) or not file_path.exists() or not file_path.is_file():
      self.send_error(404, "Not found")
      return

    data = file_path.read_bytes()
    ctype, _ = mimetypes.guess_type(str(file_path))
    self.send_response(200)
    self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}")
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)

  def do_GET(self):
    parsed = parse.urlparse(self.path)

    if parsed.path == "/api/state":
      params = parse.parse_qs(parsed.query)
      room_id = (params.get("roomId", [""])[0] or "").strip().lower()
      if not room_id:
        self._send_json(400, {"error": "roomId gerekli"})
        return

      ensure_round_question(room_id)

      with ROOM_LOCK:
        room = get_room(room_id)
        self._send_json(200, room_snapshot(room))
      return

    if parsed.path.startswith("/api/"):
      self.send_error(404, "Not found")
      return

    self._serve_public(self.path)

  def do_POST(self):
    length = int(self.headers.get("Content-Length", "0"))
    body = self.rfile.read(length)
    try:
      payload = json.loads(body.decode("utf-8"))
    except Exception:
      self._send_json(400, {"error": "Gecersiz JSON"})
      return

    room_id = (payload.get("roomId") or "").strip().lower()
    if not room_id:
      self._send_json(400, {"error": "roomId gerekli"})
      return

    if self.path == "/api/join":
      player = (payload.get("playerName") or "").strip()
      if not player:
        self._send_json(400, {"error": "playerName gerekli"})
        return
      with ROOM_LOCK:
        room = get_room(room_id)
        if player not in room["players"]:
          room["players"][player] = 0
          add_event(room, "system", f"{player} odaya girdi.")
        self._send_json(200, room_snapshot(room))
      return

    if self.path == "/api/ready":
      player = (payload.get("playerName") or "").strip()
      if not player:
        self._send_json(400, {"error": "playerName gerekli"})
        return
      with ROOM_LOCK:
        room = get_room(room_id)
        if player not in room["players"]:
          room["players"][player] = 0
        if room["phase"] not in ["lobby", "round_end"]:
          self._send_json(409, {"error": "Su an hazirlanma asamasi degil", "state": room_snapshot(room)})
          return

        if player in room["ready"]:
          self._send_json(200, room_snapshot(room))
          return

        room["ready"].add(player)
        add_event(room, "system", f"{player} hazir.")

        if can_start_round(room):
          start_countdown(room)

        self._send_json(200, room_snapshot(room))
      return

    if self.path == "/api/answer":
      player = (payload.get("playerName") or "").strip()
      answer_text = (payload.get("answer") or "").strip()
      if not player or not answer_text:
        self._send_json(400, {"error": "playerName ve answer gerekli"})
        return

      with ROOM_LOCK:
        room = get_room(room_id)
        if room["phase"] != "question" or not room["current_question"]:
          self._send_json(409, {"error": "Su an soru asamasi degil", "state": room_snapshot(room)})
          return
        question = room["current_question"]["question"]
        canonical = room["current_question"]["answer"]

      correct = evaluate_answer(question, canonical, answer_text)

      with ROOM_LOCK:
        room = get_room(room_id)
        add_event(room, "system", f"{player}: {answer_text}")

        if room["phase"] != "question" or not room["current_question"]:
          self._send_json(200, room_snapshot(room))
          return

        if correct:
          room["players"][player] = room["players"].get(player, 0) + 1
          room["current_question"]["winner"] = player
          room["phase"] = "round_end"
          score_line = " | ".join([f"{name}: {score}" for name, score in room["players"].items()])
          add_event(room, "host", f"Dogru! Turu {player} aldi. (+1 puan)")
          add_event(room, "host", f"Skor: {score_line}")
          add_event(room, "host", "Yeni tur icin herkes yeniden Hazir'a bassin.")
          room["ready"] = set()
        else:
          add_event(room, "host", f"{player}, olmadi. Devam.")

        self._send_json(200, room_snapshot(room))
      return

    self.send_error(404, "Not found")


if __name__ == "__main__":
  server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
  print(f"Quiz game running on http://localhost:{PORT}")
  server.serve_forever()
