const sessionRows = document.getElementById("session-rows");
const sessionEmpty = document.getElementById("session-empty");
const detailEmpty = document.getElementById("detail-empty");
const detailContent = document.getElementById("detail-content");
const detailTitle = document.getElementById("detail-title");
const detailTypePill = document.getElementById("detail-type-pill");
const detailMeta = document.getElementById("detail-meta");
const detailLogs = document.getElementById("detail-logs");
const attachRow = document.getElementById("attach-row");
const btnStop = document.getElementById("btn-stop");

let selectedId = null;
let sessions = [];
let repos = [];
let newSessionType = "ask";

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
  let html = `<option value="">${repos.length ? "No repo" : "No repos registered"}</option>`;
  for (const r of repos) {
    const sel = r.full_name === selectedValue ? "selected" : "";
    html += `<option value="${escapeHtml(r.full_name)}" ${sel}>${escapeHtml(r.full_name)}</option>`;
  }
  return html;
}

function renderRepoChips() {
  const el = document.getElementById("repo-chips");
  el.innerHTML = repos.map((r) => `<span class="repo-chip">${escapeHtml(r.full_name)}</span>`).join("");
}

function renderRepoSelects() {
  document.getElementById("new-repo").innerHTML = repoOptionsHtml("");
  document.getElementById("attach-repo").innerHTML = repoOptionsHtml("");
}

function render() {
  sessionRows.innerHTML = "";
  sessionEmpty.classList.toggle("hidden", sessions.length > 0);
  for (const s of sessions) {
    const tr = document.createElement("tr");
    tr.className = s.id === selectedId ? "selected" : "";
    const repoLabel = s.repo
      ? `${escapeHtml(s.repo)}${s.issue_number ? ` #${s.issue_number}` : ""}`
      : `<span class="none">no repo</span>`;
    tr.innerHTML = `
      <td><span class="pill pill-type-${s.session_type}">${s.session_type}</span></td>
      <td class="cell-repo">${repoLabel}</td>
      <td><span class="status status-${s.status}">${s.status}</span></td>
      <td>${s.pr_number ? `<a href="https://github.com/${s.repo}/pull/${s.pr_number}" target="_blank">#${s.pr_number}</a>` : "-"}</td>
      <td>${fmtTime(s.updated_at)}</td>
    `;
    tr.addEventListener("click", () => selectSession(s.id));
    sessionRows.appendChild(tr);
  }
}

async function refreshRepos() {
  repos = await api("/api/repos");
  renderRepoChips();
  renderRepoSelects();
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

  detailTypePill.textContent = session.session_type;
  detailTypePill.className = `pill pill-type-${session.session_type}`;
  detailTitle.textContent = session.repo
    ? `${session.repo}${session.issue_number ? `#${session.issue_number}` : ""} — ${session.issue_title || "(untitled)"}`
    : (session.issue_title || `Session #${session.id}`);
  detailMeta.innerHTML =
    `<span class="status status-${session.status}">${session.status}</span>` +
    (session.pr_number ? ` · PR #${session.pr_number}` : "") +
    ` · ${session.turn_count} turn${session.turn_count === 1 ? "" : "s"}` +
    ` · ${fmtTime(session.last_activity_at)}`;

  attachRow.classList.toggle("hidden", !!session.repo);
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

// -- type toggle ------------------------------------------------------

document.getElementById("type-toggle").addEventListener("click", (e) => {
  const btn = e.target.closest(".type-btn");
  if (!btn) return;
  newSessionType = btn.dataset.type;
  for (const b of document.querySelectorAll(".type-btn")) b.classList.toggle("active", b === btn);
});

// -- new session ------------------------------------------------------

document.getElementById("btn-start-session").addEventListener("click", async () => {
  const repo = document.getElementById("new-repo").value || null;
  const issueVal = document.getElementById("new-issue").value;
  const issue_number = repo && issueVal ? parseInt(issueVal, 10) : null;
  const prompt = document.getElementById("new-prompt").value.trim() || null;
  try {
    const session = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ session_type: newSessionType, repo, issue_number, prompt }),
    });
    document.getElementById("new-issue").value = "";
    document.getElementById("new-prompt").value = "";
    await refreshSessions();
    selectSession(session.id);
  } catch (err) {
    alert(`Could not start session: ${err.message}`);
  }
});

// -- repo registration --------------------------------------------------

const addRepoRow = document.getElementById("add-repo-row");
document.getElementById("btn-add-repo").addEventListener("click", () => {
  addRepoRow.classList.toggle("hidden");
  document.getElementById("add-repo-input").focus();
});
document.getElementById("btn-add-repo-cancel").addEventListener("click", () => {
  addRepoRow.classList.add("hidden");
});
document.getElementById("btn-add-repo-confirm").addEventListener("click", async () => {
  const input = document.getElementById("add-repo-input");
  const full_name = input.value.trim();
  if (!full_name) return;
  try {
    await api("/api/repos", { method: "POST", body: JSON.stringify({ full_name }) });
    input.value = "";
    addRepoRow.classList.add("hidden");
    await refreshRepos();
  } catch (err) {
    alert(`Could not register repo: ${err.message}`);
  }
});

// -- attach repo to an existing session ----------------------------------

document.getElementById("btn-attach").addEventListener("click", async () => {
  if (!selectedId) return;
  const repo = document.getElementById("attach-repo").value;
  if (!repo) return;
  const issueVal = document.getElementById("attach-issue").value;
  const issue_number = issueVal ? parseInt(issueVal, 10) : null;
  try {
    await api(`/api/sessions/${selectedId}/attach`, {
      method: "POST",
      body: JSON.stringify({ repo, issue_number }),
    });
    document.getElementById("attach-issue").value = "";
    await refreshSessions();
  } catch (err) {
    alert(`Could not attach repo: ${err.message}`);
  }
});

// -- message / actions --------------------------------------------------

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

btnStop.addEventListener("click", async () => {
  if (!selectedId) return;
  try {
    await api(`/api/sessions/${selectedId}/stop`, { method: "POST" });
  } catch (err) {
    alert(`Could not stop: ${err.message}`);
  }
  await refreshSessions();
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

refreshRepos();
refreshSessions();
setInterval(refreshSessions, 3000);
setInterval(refreshRepos, 15000);
