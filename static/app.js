const sessionRows = document.getElementById("session-rows");
const detailEmpty = document.getElementById("detail-empty");
const detailContent = document.getElementById("detail-content");
const detailTitle = document.getElementById("detail-title");
const detailMeta = document.getElementById("detail-meta");
const detailLogs = document.getElementById("detail-logs");

let selectedId = null;
let sessions = [];

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

function render() {
  sessionRows.innerHTML = "";
  for (const s of sessions) {
    const tr = document.createElement("tr");
    tr.className = s.id === selectedId ? "selected" : "";
    tr.innerHTML = `
      <td>${s.repo}</td>
      <td>#${s.issue_number}</td>
      <td><span class="status status-${s.status}">${s.status}</span></td>
      <td>${s.pr_number ? `<a href="https://github.com/${s.repo}/pull/${s.pr_number}" target="_blank">#${s.pr_number}</a>` : "-"}</td>
      <td>${fmtTime(s.updated_at)}</td>
    `;
    tr.addEventListener("click", () => selectSession(s.id));
    sessionRows.appendChild(tr);
  }
}

async function refreshSessions() {
  sessions = await api("/api/sessions");
  render();
  if (selectedId) await refreshDetail();
}

async function selectSession(id) {
  selectedId = id;
  render();
  await refreshDetail();
}

async function refreshDetail() {
  if (!selectedId) return;
  let session;
  try {
    session = await api(`/api/sessions/${selectedId}`);
  } catch {
    selectedId = null;
    detailEmpty.classList.remove("hidden");
    detailContent.classList.add("hidden");
    return;
  }
  const logs = await api(`/api/sessions/${selectedId}/logs`);

  detailEmpty.classList.add("hidden");
  detailContent.classList.remove("hidden");
  detailTitle.textContent = `${session.repo}#${session.issue_number} — ${session.issue_title}`;
  detailMeta.textContent = `status: ${session.status}` +
    (session.pr_number ? ` · PR #${session.pr_number}` : "") +
    ` · turns: ${session.turn_count}` +
    ` · last activity: ${fmtTime(session.last_activity_at)}`;

  const atBottom = detailLogs.scrollTop + detailLogs.clientHeight >= detailLogs.scrollHeight - 20;
  detailLogs.textContent = logs
    .map((l) => `[${new Date(l.created_at * 1000).toLocaleTimeString()}] (${l.kind}) ${l.content}`)
    .join("\n\n");
  if (atBottom) detailLogs.scrollTop = detailLogs.scrollHeight;
}

document.getElementById("new-session-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const repo = document.getElementById("new-repo").value.trim();
  const issue_number = parseInt(document.getElementById("new-issue").value, 10);
  try {
    const session = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ repo, issue_number }),
    });
    document.getElementById("new-repo").value = "";
    document.getElementById("new-issue").value = "";
    await refreshSessions();
    selectSession(session.id);
  } catch (err) {
    alert(`Could not start session: ${err.message}`);
  }
});

document.getElementById("message-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedId) return;
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

document.getElementById("btn-close").addEventListener("click", async () => {
  if (!selectedId) return;
  await api(`/api/sessions/${selectedId}/close`, { method: "POST" });
  await refreshSessions();
});

document.getElementById("btn-archive").addEventListener("click", async () => {
  if (!selectedId) return;
  await api(`/api/sessions/${selectedId}/archive`, { method: "POST" });
  await refreshSessions();
});

document.getElementById("btn-reopen").addEventListener("click", async () => {
  if (!selectedId) return;
  await api(`/api/sessions/${selectedId}/reopen`, { method: "POST" });
  await refreshSessions();
});

refreshSessions();
setInterval(refreshSessions, 3000);
