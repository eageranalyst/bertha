"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Session, SessionSpec, SpecPhase } from "@/lib/types";
import { Markdown } from "./Markdown";

const PHASE_LABEL: Record<SpecPhase, string> = {
  drafting: "Writing the spec",
  review: "Spec awaiting your review",
  building: "Building the approved spec",
  built: "Built from the approved spec",
  abandoned: "Abandoned",
};

/** A spec-first session's spec, where to review it, and approve or abandon. */
export function SpecPanel({
  session,
  onApprove,
  onAbandon,
}: {
  session: Session;
  onApprove: () => Promise<void>;
  onAbandon: () => Promise<void>;
}) {
  const [spec, setSpec] = useState<SessionSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const phase = session.spec_phase as SpecPhase;
  const running = session.status === "running" || session.status === "queued";

  // The spec only changes when a turn ends, so it's fetched again then.
  useEffect(() => {
    api.getSpec(session.id).then(setSpec).catch(() => {});
  }, [session.id, phase, session.turn_count, running]);

  async function act(confirmText: string, action: () => Promise<void>) {
    if (!window.confirm(confirmText)) return;
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  const reviewing = phase === "review" && !running;

  return (
    <details className="spec-panel" open={phase === "review" || phase === "drafting"}>
      <summary>
        <span className={`spec-phase spec-phase-${phase}`}>{PHASE_LABEL[phase]}</span>
        {running && phase === "review" ? " · revising from your comments" : ""}
        {spec?.url && (
          <>
            {" · "}
            <a href={spec.url} target="_blank" rel="noreferrer">{spec.path}</a>
          </>
        )}
        {spec?.pr_url && (
          <>
            {" · "}
            <a href={spec.pr_url} target="_blank" rel="noreferrer">PR #{session.pr_number}</a>
          </>
        )}
      </summary>
      {reviewing && (
        <div className="spec-actions">
          <button type="button" className="btn-primary" disabled={busy}
                  onClick={() => act("Approve the spec and start building it?", onApprove)}>
            Approve and build
          </button>
          <button type="button" className="btn-ghost" disabled={busy}
                  onClick={() => act("Abandon this spec? The PR is closed and nothing is built.", onAbandon)}>
            Abandon
          </button>
          <span className="skill-form-hint">
            For changes, comment on the PR (line comments work), or message the agent below.
          </span>
        </div>
      )}
      {spec?.content ? (
        <div className="spec-body">
          <Markdown>{spec.content}</Markdown>
        </div>
      ) : (
        <p className="skill-form-hint">
          {phase === "drafting" ? "The spec shows here once the first turn pushes it." : "No spec on the branch."}
        </p>
      )}
    </details>
  );
}
