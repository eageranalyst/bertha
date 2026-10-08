"use client";

import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { SecurityStatus } from "@/lib/types";

function Row({ ok, label, children }: { ok: boolean | null; label: string; children: ReactNode }) {
  const mark = ok === null ? "?" : ok ? "✓" : "!";
  return (
    <div className={"security-row " + (ok === null ? "unknown" : ok ? "ok" : "warn")}>
      <span className="security-mark">{mark}</span>
      <div>
        <div className="settings-label">{label}</div>
        <div className="settings-help">{children}</div>
      </div>
    </div>
  );
}

export function SecurityPanel() {
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSecurity().then(setStatus).catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <div className="wiki-error">Could not load the security status: {error}</div>;
  if (!status) return <div className="empty-state">Checking…</div>;

  const token = status.token;
  const unprotected = status.repos.filter((r) => r.default_branch_protected === false);

  return (
    <div className="settings-list">
      <Row ok={status.egress_isolation} label="Network isolation">
        {status.egress_isolation
          ? `Agent containers can't reach this machine or the internet, except the model server and: ${status.egress_allow.join(", ")}. Add hosts with FACTORY_EGRESS_ALLOW.`
          : "Off (FACTORY_EGRESS=open): agent containers can reach this machine, including Bertha's API, and the whole internet."}
      </Row>
      <Row ok={true} label="Agent user">
        The agent runs as {status.agent_user}. Pushing and opening PRs happen outside the agent.
      </Row>
      <Row ok={true} label="Container limits">
        {status.container_limits.memory_gb} GB memory, {status.container_limits.cpus} CPUs and{" "}
        {status.container_limits.pids} processes per container. Memory and CPUs are set above.
      </Row>
      <Row
        ok={token ? token.kind === "fine-grained" && token.broad_scopes.length === 0 : null}
        label="GitHub token"
      >
        {token
          ? token.kind === "fine-grained"
            ? `A fine-grained token, limited to the repos and permissions chosen when it was made. ${token.expires ? `Expires ${token.expires}.` : "It doesn't expire; consider setting an expiry."}`
            : `A classic token with ${token.scopes.join(", ") || "no"} scopes. It can act on every repo the bot can reach. Replace it with a fine-grained token limited to the registered repos, with Contents and Pull requests set to read and write.`
          : `Couldn't check: ${status.token_error}`}
      </Row>
      <Row
        ok={status.repos.length === 0 ? null : unprotected.length === 0}
        label="Branch protection"
      >
        {unprotected.length > 0
          ? `Not protected: ${unprotected.map((r) => r.full_name).join(", ")}. Without protection, nothing stops an agent's change being merged unreviewed. In each repo's Settings → Branches, require a pull request review before merging into the default branch.`
          : status.repos.length === 0
            ? "No repos registered."
            : "Every registered repo's default branch is protected."}
      </Row>
    </div>
  );
}
