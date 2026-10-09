"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CiCheck, SessionCi } from "@/lib/types";

type Pr = NonNullable<SessionCi["pr"]>;

const CI_LABEL: Record<Pr["ci"], string> = {
  passed: "CI passing",
  failed: "CI failing",
  pending: "CI running",
  none: "No CI checks",
  unavailable: "CI not visible",
};

const FAILED = new Set(["failure", "timed_out", "startup_failure"]);

function checkState(c: CiCheck): "passed" | "failed" | "pending" | "skipped" {
  if (c.status !== "completed") return "pending";
  if (c.conclusion && FAILED.has(c.conclusion)) return "failed";
  if (c.conclusion === "skipped" || c.conclusion === "neutral" || c.conclusion === "cancelled") return "skipped";
  return "passed";
}

function Mark({ state }: { state: "passed" | "failed" | "pending" | "skipped" | "none" | "unavailable" }) {
  if (state === "passed") {
    return (
      <svg viewBox="0 0 16 16" className="ci-mark passed" aria-hidden="true">
        <circle cx="8" cy="8" r="7" />
        <path d="M4.8 8.3l2.1 2.1 4.3-4.6" />
      </svg>
    );
  }
  if (state === "failed") {
    return (
      <svg viewBox="0 0 16 16" className="ci-mark failed" aria-hidden="true">
        <circle cx="8" cy="8" r="7" />
        <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" />
      </svg>
    );
  }
  if (state === "pending") {
    return (
      <svg viewBox="0 0 16 16" className="ci-mark pending" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        <path d="M8 2a6 6 0 0 1 6 6" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" className="ci-mark quiet" aria-hidden="true">
      <circle cx="8" cy="8" r="6" />
      <path d="M5 8h6" />
    </svg>
  );
}

/**
 * The session's pull request and its CI checks, under the session header.
 * Checked every 30 seconds, or every 10 while CI is running.
 */
export function CiStatus({ sessionId, prNumber }: { sessionId: number; prNumber: number | null }) {
  const [pr, setPr] = useState<Pr | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setPr(null);
    setError(null);
    setOpen(false);
    if (prNumber == null) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      let next = 30_000;
      try {
        const result = await api.getSessionCi(sessionId);
        if (!alive) return;
        setPr(result.pr);
        setError(result.error ?? null);
        if (result.pr?.ci === "pending") next = 10_000;
      } catch {
        // API unreachable: keep what's shown and try again.
      }
      if (alive) timer = setTimeout(poll, next);
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [sessionId, prNumber]);

  if (prNumber == null) return null;
  if (!pr) {
    return (
      <div className="ci-bar">
        <span className="ci-pr">PR #{prNumber}</span>
        <span className="ci-note">{error ?? "Checking CI…"}</span>
      </div>
    );
  }

  const prState = pr.merged ? "merged" : pr.state === "closed" ? "closed" : pr.draft ? "draft" : "open";
  const counts = pr.checks.reduce(
    (acc, c) => ({ ...acc, [checkState(c)]: (acc[checkState(c)] ?? 0) + 1 }),
    {} as Record<string, number>,
  );
  const detail =
    pr.checks.length === 0
      ? null
      : [
          counts.failed && `${counts.failed} failed`,
          counts.pending && `${counts.pending} running`,
          counts.passed && `${counts.passed} passed`,
          counts.skipped && `${counts.skipped} skipped`,
        ]
          .filter(Boolean)
          .join(", ");

  return (
    <div className="ci-bar">
      <a className="ci-pr" href={pr.url} target="_blank" rel="noreferrer">
        PR #{pr.number}
      </a>
      <span className={`ci-pr-state ci-pr-${prState}`}>{prState}</span>
      <button
        type="button"
        className={`ci-pill ci-${pr.ci}`}
        aria-expanded={open}
        disabled={pr.checks.length === 0}
        title={pr.ci_error ?? `Commit ${pr.sha.slice(0, 7)}`}
        onClick={() => setOpen(!open)}
      >
        <Mark state={pr.ci} />
        {CI_LABEL[pr.ci]}
        {detail && <span className="ci-detail">· {detail}</span>}
      </button>
      {open && pr.checks.length > 0 && (
        <ul className="ci-checks">
          {pr.checks.map((c) => (
            <li key={c.id} className={`ci-check ci-check-${checkState(c)}`}>
              <Mark state={checkState(c)} />
              {c.url ? (
                <a href={c.url} target="_blank" rel="noreferrer">
                  {c.name}
                </a>
              ) : (
                <span>{c.name}</span>
              )}
              <span className="ci-check-result">{c.status === "completed" ? c.conclusion : c.status.replace("_", " ")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
