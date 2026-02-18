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
PRESENCE_TIMEOUT_MS = 15000
TURN_TIMEOUT_MS = 10000
QUESTION_TIMEOUT_MS = 10000
QUESTION_GENERATION_RETRIES = 6

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
      "presence": {},
      "ready": set(),
      "phase": "lobby",
      "countdown_end_ms": 0,
      "round": 0,
      "current_question": None,
      "events": [],
      "seq": 0,
      "generating": False,
      "question_categories": [],
      "question_fingerprints": [],
      "recent_questions": [],
    }
  return ROOMS[room_id]


def active_players(room: dict) -> list:
  now_ms = int(time.time() * 1000)
  return [
    name
    for name, last_seen in room["presence"].items()
    if now_ms - last_seen <= PRESENCE_TIMEOUT_MS and name in room["players"]
  ]


def mark_presence(room: dict, player: str) -> None:
  room["presence"][player] = int(time.time() * 1000)


def reconcile_room_state(room: dict) -> None:
  now_ms = int(time.time() * 1000)
  for name, last_seen in list(room["presence"].items()):
    if now_ms - last_seen > PRESENCE_TIMEOUT_MS:
      room["presence"].pop(name, None)
      room["ready"].discard(name)

  active = active_players(room)
  if room["phase"] in ["countdown", "question", "round_end"] and len(active) < 2:
    room["phase"] = "lobby"
    room["current_question"] = None
    room["ready"] = set()
    room["countdown_end_ms"] = 0
    room["generating"] = False
    add_event(room, "host", "Oyunculardan biri cikti. Tur sifirlandi, tekrar Hazir basin.")
    return

  if room["phase"] == "question" and room["current_question"]:
    q = room["current_question"]
    question_deadline_ms = int(q.get("question_deadline_ms", 0))
    if question_deadline_ms and now_ms >= question_deadline_ms:
      answer = q.get("answer", "")
      add_event(room, "host", f"Sure bitti. Dogru cevap: {answer}. 3 saniye sonra yeni soru.")
      room["phase"] = "countdown"
      room["countdown_end_ms"] = now_ms + 3000
      room["current_question"] = None
      room["generating"] = False
      return

    expected_player = q.get("expected_player", "")
    deadline_ms = int(q.get("turn_deadline_ms", 0))
    if expected_player and deadline_ms and now_ms >= deadline_ms:
      answer = q.get("answer", "")
      add_event(room, "host", f"{expected_player} sureyi doldurdu. Dogru cevap: {answer}. 3 saniye sonra yeni soru.")
      room["phase"] = "countdown"
      room["countdown_end_ms"] = now_ms + 3000
      room["current_question"] = None
      room["generating"] = False


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
  player_names = active_players(room)
  return len(player_names) >= 2 and all(name in room["ready"] for name in player_names)


def generate_question_payload(room: dict) -> dict:
  recent = ", ".join(room["question_categories"][-4:]) or "(yok)"
  banned_fps = set(room.get("question_fingerprints", []))
  recent_questions = room.get("recent_questions", [])[-8:]
  recent_text = " | ".join(recent_questions) if recent_questions else "(yok)"

  for _ in range(QUESTION_GENERATION_RETRIES):
    prompt = (
      "Yeni bir quiz sorusu uret. Sadece JSON don. Anahtarlar: "
      "category, question, answer, hostComment. question tek soru olmali. "
      "hostComment tek cumle ve kisa olmali. "
      "ASLA asagidaki sorulara benzer veya ayni soru uretme: "
      f"{recent_text}. Son kategoriler: {recent}."
    )
    raw = model_request(prompt, response_mime_type="application/json", temperature=0.9)
    data = parse_json_text(raw)
    if isinstance(data, list):
      data = data[0] if data and isinstance(data[0], dict) else {}
    if not isinstance(data, dict):
      data = {}
    if not data.get("question") or not data.get("answer"):
      continue

    q_text = str(data["question"]).strip()
    fingerprint = normalize_text(q_text)
    if not fingerprint or fingerprint in banned_fps:
      continue

    return data

  # Fallback: oyun akisi durmasin.
  return {
    "category": "Genel Kultur",
    "question": "Bir haftada kac gun vardir?",
    "answer": "7",
    "hostComment": "Bu sefer hizli gir.",
  }


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
    q_text = question_obj["question"].strip()
    q_fp = normalize_text(q_text)
    if q_fp:
      room["question_fingerprints"].append(q_fp)
      room["question_fingerprints"] = room["question_fingerprints"][-60:]
    room["recent_questions"].append(q_text)
    room["recent_questions"] = room["recent_questions"][-20:]

    room["current_question"] = {
      "question": q_text,
      "answer": question_obj["answer"].strip(),
      "hostComment": (question_obj.get("hostComment") or "Hadi bakalim.").strip(),
      "category": (question_obj.get("category") or "Karisik").strip(),
      "winner": "",
      "expected_player": "",
      "attempt_order": [],
      "turn_deadline_ms": 0,
      "question_deadline_ms": int(time.time() * 1000) + QUESTION_TIMEOUT_MS,
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
    if isinstance(data, list):
      data = data[0] if data and isinstance(data[0], dict) else {}
    if not isinstance(data, dict):
      data = {}
    return bool(data.get("correct", False))
  except Exception:
    return False


def room_snapshot(room: dict) -> dict:
  now_ms = int(time.time() * 1000)
  remaining = 0
  if room["phase"] == "countdown":
    remaining = max(0, int((room["countdown_end_ms"] - now_ms + 999) / 1000))
  question_remaining = 0
  if room["phase"] == "question" and room.get("current_question"):
    deadline = int(room["current_question"].get("question_deadline_ms", 0))
    question_remaining = max(0, int((deadline - now_ms + 999) / 1000))

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
    "questionCountdown": question_remaining,
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
      player = (params.get("playerName", [""])[0] or "").strip()
      if not room_id:
        self._send_json(400, {"error": "roomId gerekli"})
        return

      with ROOM_LOCK:
        room = get_room(room_id)
        if player and player in room["players"]:
          mark_presence(room, player)
        reconcile_room_state(room)

      ensure_round_question(room_id)

      with ROOM_LOCK:
        room = get_room(room_id)
        reconcile_room_state(room)
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
        mark_presence(room, player)
        reconcile_room_state(room)
        self._send_json(200, room_snapshot(room))
      return

    if self.path == "/api/leave":
      player = (payload.get("playerName") or "").strip()
      if not player:
        self._send_json(400, {"error": "playerName gerekli"})
        return
      with ROOM_LOCK:
        room = get_room(room_id)
        room["presence"].pop(player, None)
        room["ready"].discard(player)
        reconcile_room_state(room)
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
        mark_presence(room, player)
        reconcile_room_state(room)
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
        mark_presence(room, player)
        reconcile_room_state(room)
        if room["phase"] != "question" or not room["current_question"]:
          self._send_json(409, {"error": "Su an soru asamasi degil", "state": room_snapshot(room)})
          return
        active_q = room["current_question"]
        attempts = active_q.setdefault("attempt_order", [])
        if player in attempts:
          self._send_json(409, {"error": "Yanlis cevap. Bu tur tekrar cevap veremezsin.", "state": room_snapshot(room)})
          return

        expected_player = active_q.get("expected_player", "")
        if expected_player and expected_player != player:
          self._send_json(409, {"error": f"Sira {expected_player} oyuncusunda.", "state": room_snapshot(room)})
          return
        if not expected_player:
          active_q["expected_player"] = player

        question = active_q["question"]
        canonical = active_q["answer"]
        correct = evaluate_answer(question, canonical, answer_text)

        add_event(room, "system", f"{player}: {answer_text}")

        if room["phase"] != "question" or not room["current_question"]:
          self._send_json(200, room_snapshot(room))
          return

        if correct:
          room["players"][player] = room["players"].get(player, 0) + 1
          active_q["winner"] = player
          score_line = " | ".join([f"{name}: {score}" for name, score in room["players"].items()])
          add_event(room, "host", f"Dogru! Turu {player} aldi. (+1 puan)")
          add_event(room, "host", f"Skor: {score_line}")
          add_event(room, "host", "3 saniye sonra yeni soru.")
          room["phase"] = "countdown"
          room["countdown_end_ms"] = int(time.time() * 1000) + 3000
          room["current_question"] = None
          room["generating"] = False
        else:
          attempts.append(player)
          add_event(room, "host", "Yanlis cevap.")

          other_players = [name for name in active_players(room) if name != player]
          next_player = other_players[0] if other_players else ""

          if next_player and next_player not in attempts:
            active_q["expected_player"] = next_player
            active_q["turn_deadline_ms"] = int(time.time() * 1000) + TURN_TIMEOUT_MS
            add_event(room, "host", f"{player} bilemedi. Sira {next_player} oyuncusunda.")
          else:
            answer = active_q.get("answer", "")
            add_event(room, "host", f"Iki taraf da bilemedi. Dogru cevap: {answer}. 3 saniye sonra yeni soru.")
            room["phase"] = "countdown"
            room["countdown_end_ms"] = int(time.time() * 1000) + 3000
            room["current_question"] = None
            room["generating"] = False

        self._send_json(200, room_snapshot(room))
      return

    self.send_error(404, "Not found")


if __name__ == "__main__":
  server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
  print(f"Quiz game running on http://localhost:{PORT}")
  server.serve_forever()
