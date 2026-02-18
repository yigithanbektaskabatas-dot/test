const ROOM_ID = "arena";

const joinView = document.getElementById("joinView");
const gameView = document.getElementById("gameView");
const nameInput = document.getElementById("nameInput");
const joinBtn = document.getElementById("joinBtn");
const joinStatus = document.getElementById("joinStatus");
const leaveBtn = document.getElementById("leaveBtn");

const scoreBoard = document.getElementById("scoreBoard");
const readyBtn = document.getElementById("readyBtn");
const countdown = document.getElementById("countdown");
const questionCard = document.getElementById("questionCard");
const questionText = document.getElementById("questionText");
const commentText = document.getElementById("commentText");
const statusText = document.getElementById("statusText");

const answerForm = document.getElementById("answerForm");
const answerInput = document.getElementById("answerInput");
const answerBtn = answerForm.querySelector("button");

let playerName = "";
let pollTimer = null;
let joined = false;

function getStorage(key) {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function setStorage(key, val) {
  try {
    localStorage.setItem(key, val);
  } catch {
    // no-op
  }
}

nameInput.value = getStorage("quiz_name");

function setStatus(text) {
  statusText.textContent = text;
}

function setJoinStatus(text) {
  joinStatus.textContent = text;
}

function renderScores(scores) {
  scoreBoard.innerHTML = "";
  for (const row of scores) {
    const item = document.createElement("div");
    item.className = "score-item";
    item.textContent = `${row.name}: ${row.score}`;
    scoreBoard.appendChild(item);
  }
}

function applyState(state) {
  renderScores(state.scores || []);

  if (state.phase === "lobby") {
    countdown.textContent = "-";
    readyBtn.disabled = false;
    answerInput.disabled = true;
    answerBtn.disabled = true;
    questionCard.classList.add("hidden");
    setStatus("Rakibini bekle.");
  }

  if (state.phase === "countdown") {
    countdown.textContent = String(state.countdown || 0);
    readyBtn.disabled = true;
    answerInput.disabled = true;
    answerBtn.disabled = true;
    questionCard.classList.add("hidden");
    setStatus("Hazir. Geri sayim basladi.");
  }

  if (state.phase === "question") {
    countdown.textContent = String(state.questionCountdown || 0);
    readyBtn.disabled = true;
    answerInput.disabled = false;
    answerBtn.disabled = false;
    if (state.question) {
      questionText.textContent = state.question.question || "";
      commentText.textContent = state.question.hostComment || "";
      questionCard.classList.remove("hidden");
    }
    setStatus("Cevabi ilk dogru veren +1 puan.");
  }

  if (state.phase === "round_end") {
    countdown.textContent = "-";
    readyBtn.disabled = false;
    answerInput.disabled = true;
    answerBtn.disabled = true;
    questionCard.classList.remove("hidden");
    if (state.question) {
      questionText.textContent = state.question.question || "";
      commentText.textContent = state.question.winner
        ? `Kazanan: ${state.question.winner}`
        : "Tur bitti.";
    }
    setStatus("Yeni tur icin Hazir bas.");
  }
}

async function fetchState() {
  if (!joined) return;
  try {
    const res = await fetch(
      `/api/state?roomId=${encodeURIComponent(ROOM_ID)}&playerName=${encodeURIComponent(playerName)}`
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Durum alinamadi.");
    applyState(data);
  } catch (err) {
    setStatus(`Hata: ${err.message}`);
  }
}

async function joinGame() {
  const n = nameInput.value.trim();
  if (!n) {
    alert("Adini yazman lazim.");
    return;
  }

  playerName = n;
  joinBtn.disabled = true;
  setJoinStatus("Baglaniliyor...");

  try {
    const res = await fetch("/api/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roomId: ROOM_ID, playerName }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Baglanamadi.");

    joined = true;
    setStorage("quiz_name", playerName);

    joinView.classList.add("hidden");
    gameView.classList.remove("hidden");

    applyState(data);
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(fetchState, 1000);
    await fetchState();
  } catch (err) {
    setJoinStatus(`Hata: ${err.message}`);
    joinBtn.disabled = false;
  }
}

async function leaveGame() {
  if (!joined || !playerName) return;
  try {
    await fetch("/api/leave", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roomId: ROOM_ID, playerName }),
    });
  } catch {
    // no-op
  }

  joined = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  gameView.classList.add("hidden");
  joinView.classList.remove("hidden");
  setJoinStatus("Rakibini bekle.");
}

async function markReady() {
  if (!joined) return;
  try {
    readyBtn.disabled = true;
    const res = await fetch("/api/ready", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roomId: ROOM_ID, playerName }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Hazir olmadi.");
    applyState(data);
  } catch (err) {
    setStatus(`Hata: ${err.message}`);
    readyBtn.disabled = false;
  }
}

async function submitAnswer(event) {
  event.preventDefault();
  const answer = answerInput.value.trim();
  if (!answer) return;

  answerInput.value = "";
  answerInput.disabled = true;
  answerBtn.disabled = true;

  try {
    const res = await fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roomId: ROOM_ID, playerName, answer }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Cevap gonderilemedi.");
    applyState(data);
  } catch (err) {
    setStatus(`Hata: ${err.message}`);
  }
}

joinBtn.addEventListener("click", joinGame);
leaveBtn.addEventListener("click", leaveGame);
readyBtn.addEventListener("click", markReady);
answerForm.addEventListener("submit", submitAnswer);

window.addEventListener("beforeunload", () => {
  if (!joined || !playerName) return;
  const payload = JSON.stringify({ roomId: ROOM_ID, playerName });
  navigator.sendBeacon("/api/leave", payload);
});

// Bilerek auto-join yok: oyuncu her giriste Hazirim ile oyuna girer.
