const sessionRows = document.getElementById("session-rows");
const sessionEmpty = document.getElementById("session-empty");
const detailEmpty = document.getElementById("detail-empty");
const composeContent = document.getElementById("compose-content");
const detailContent = document.getElementById("detail-content");
const detailTitle = document.getElementById("detail-title");
const detailRef = document.getElementById("detail-ref");
const detailTypePill = document.getElementById("detail-type-pill");
const detailMeta = document.getElementById("detail-meta");
const detailLogs = document.getElementById("detail-logs");
const btnStop = document.getElementById("btn-stop");

let selectedId = null; // number (real session) | "draft" | null
let draftActive = false;
let sessions = [];
let repos = [];

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function fmtTime(ts) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function repoOptionsHtml(selectedValue) {
  let html = `<option value="">No repo</option>`;
  for (const r of repos) {
    const sel = r.full_name === selectedValue ? "selected" : "";
    html += `<option value="${escapeHtml(r.full_name)}" ${sel}>${escapeHtml(r.full_name)}</option>`;
  }
  return html;
}

function renderRepoChips() {
  document.getElementById("repo-chips").innerHTML =
    repos.map((r) => `<span class="repo-chip">${escapeHtml(r.full_name)}</span>`).join("");
}

function renderRepoSelect() {
  document.getElementById("compose-repo").innerHTML = repoOptionsHtml("");
}

// -- view switching ----------------------------------------------------

function showEmpty() {
  detailEmpty.classList.remove("hidden");
  composeContent.classList.add("hidden");
  detailContent.classList.add("hidden");
}
function showCompose() {
  detailEmpty.classList.add("hidden");
  composeContent.classList.remove("hidden");
  detailContent.classList.add("hidden");
}
function showDetail() {
  detailEmpty.classList.add("hidden");
  composeContent.classList.add("hidden");
  detailContent.classList.remove("hidden");
}

// -- session list -------------------------------------------------------

function render() {
  sessionRows.innerHTML = "";
  sessionEmpty.classList.toggle("hidden", sessions.length > 0 || draftActive);

  if (draftActive) {
    const row = document.createElement("div");
    row.className = "session-row is-draft" + (selectedId === "draft" ? " selected" : "");
    row.innerHTML = `
      <div class="session-row-top">
        <span class="session-row-title">New session</span>
      </div>
      <div class="session-row-meta"><span class="ref">draft</span></div>
    `;
    row.addEventListener("click", () => selectDraft());
    sessionRows.appendChild(row);
  }

  for (const s of sessions) {
    const row = document.createElement("div");
    row.className = "session-row" + (s.id === selectedId ? " selected" : "");
    const repoLabel = s.repo ? escapeHtml(s.repo) : "no repo";
    const prBit = s.pr_number
      ? ` <span class="sep">·</span> <a href="https://github.com/${s.repo}/pull/${s.pr_number}" target="_blank" onclick="event.stopPropagation()">PR #${s.pr_number}</a>`
      : "";
    row.innerHTML = `
      <div class="session-row-top">
        <span class="pill pill-type-${s.session_type}">${s.session_type}</span>
        <span class="session-row-title">${escapeHtml(s.title || "(untitled)")}</span>
        <span class="status status-${s.status}">${s.status}</span>
      </div>
      <div class="session-row-meta">
        <span class="ref">${s.ref}</span>
        <span class="sep">·</span>
        <span>${repoLabel}</span>
        ${prBit}
      </div>
    `;
    row.addEventListener("click", () => selectSession(s.id));
    sessionRows.appendChild(row);
  }
}

async function refreshRepos() {
  repos = await api("/api/repos");
  renderRepoChips();
  renderRepoSelect();
}

async function refreshSessions() {
  sessions = await api("/api/sessions");
  render();
  if (typeof selectedId === "number") await refreshDetail();
}

// -- draft (unsaved) session --------------------------------------------

function selectDraft() {
  selectedId = "draft";
  render();
  showCompose();
}

function startDraft() {
  draftActive = true;
  document.getElementById("compose-prompt").value = "";
  document.getElementById("compose-repo").value = "";
  selectDraft();
}

function discardDraft() {
  draftActive = false;
  if (selectedId === "draft") selectedId = null;
  render();
  showEmpty();
}

async function submitDraft(sessionType) {
  const prompt = document.getElementById("compose-prompt").value.trim() || null;
  const repo = document.getElementById("compose-repo").value || null;
  try {
    const session = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ session_type: sessionType, repo, prompt }),
    });
    draftActive = false;
    await refreshSessions();
    await selectSession(session.id);
  } catch (err) {
    alert(`Could not start session: ${err.message}`);
  }
}

// -- real session detail -------------------------------------------------

async function selectSession(id) {
  draftActive = false; // navigating away from any pending draft discards it
  selectedId = id;
  render();
  await refreshDetail();
}

async function refreshDetail() {
  if (typeof selectedId !== "number") return;
  let session;
  try {
    session = await api(`/api/sessions/${selectedId}`);
  } catch {
    selectedId = null;
    showEmpty();
    return;
  }
  const logs = await api(`/api/sessions/${selectedId}/logs`);

  showDetail();

  detailTypePill.textContent = session.session_type;
  detailTypePill.className = `pill pill-type-${session.session_type}`;
  detailRef.textContent = session.ref;
  detailTitle.textContent = session.title || "(untitled)";
  detailMeta.innerHTML =
    `<span class="status status-${session.status}">${session.status}</span>` +
    (session.repo ? ` · ${escapeHtml(session.repo)}` : " · no repo") +
    (session.pr_number ? ` · PR #${session.pr_number}` : "") +
    ` · ${session.turn_count} turn${session.turn_count === 1 ? "" : "s"}` +
    ` · ${fmtTime(session.last_activity_at)}`;

  btnStop.classList.toggle("hidden", session.status !== "running");

  const atBottom = detailLogs.scrollTop + detailLogs.clientHeight >= detailLogs.scrollHeight - 20;
  detailLogs.innerHTML = logs
    .map((l) => {
      const t = new Date(l.created_at * 1000).toLocaleTimeString();
      return `<span class="log-line"><span class="log-kind">[${t}] (${l.kind})</span> ${escapeHtml(l.content)}</span>`;
    })
    .join("");
  if (atBottom) detailLogs.scrollTop = detailLogs.scrollHeight;
}

// -- wiring ---------------------------------------------------------------

document.getElementById("btn-new-session").addEventListener("click", startDraft);
document.getElementById("btn-start-ask").addEventListener("click", () => submitDraft("ask"));
document.getElementById("btn-start-code").addEventListener("click", () => submitDraft("code"));

document.getElementById("message-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (typeof selectedId !== "number") return;
  const textarea = document.getElementById("message-text");
  const text = textarea.value.trim();
  if (!text) return;
  await api(`/api/sessions/${selectedId}/message`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  textarea.value = "";
  await refreshDetail();
});

btnStop.addEventListener("click", async () => {
  if (typeof selectedId !== "number") return;
  try {
    await api(`/api/sessions/${selectedId}/stop`, { method: "POST" });
  } catch (err) {
    alert(`Could not stop: ${err.message}`);
  }
  await refreshSessions();
});

document.getElementById("btn-close").addEventListener("click", async () => {
  if (typeof selectedId !== "number") return;
  await api(`/api/sessions/${selectedId}/close`, { method: "POST" });
  await refreshSessions();
});

document.getElementById("btn-archive").addEventListener("click", async () => {
  if (typeof selectedId !== "number") return;
  await api(`/api/sessions/${selectedId}/archive`, { method: "POST" });
  await refreshSessions();
});

document.getElementById("btn-reopen").addEventListener("click", async () => {
  if (typeof selectedId !== "number") return;
  await api(`/api/sessions/${selectedId}/reopen`, { method: "POST" });
  await refreshSessions();
});

refreshRepos();
refreshSessions();
setInterval(refreshSessions, 3000);
setInterval(refreshRepos, 15000);
