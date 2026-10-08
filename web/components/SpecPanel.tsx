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

/** A spec-first session's spec: a slim bar in the session, and the spec
 * itself in a sheet over the main view, with approve and abandon. */
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
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const phase = session.spec_phase as SpecPhase;
  const running = session.status === "running" || session.status === "queued";
  const reviewing = phase === "review" && !running;

  // The spec only changes when a turn ends, so it's fetched again then.
  useEffect(() => {
    api.getSpec(session.id).then(setSpec).catch(() => {});
  }, [session.id, phase, session.turn_count, running]);

  useEffect(() => {
    setOpen(false);
  }, [session.id]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function act(confirmText: string, action: () => Promise<void>) {
    if (!window.confirm(confirmText)) return;
    setBusy(true);
    try {
      await action();
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  const actions = reviewing && (
    <>
      <button type="button" className="btn-primary" disabled={busy}
              onClick={() => act("Approve the spec and start building it?", onApprove)}>
        Approve and build
      </button>
      <button type="button" className="btn-ghost" disabled={busy}
              onClick={() => act("Abandon this spec? The PR is closed and nothing is built.", onAbandon)}>
        Abandon
      </button>
    </>
  );
  const links = (
    <>
      {spec?.url && <a href={spec.url} target="_blank" rel="noreferrer">{spec.path}</a>}
      {spec?.pr_url && <a href={spec.pr_url} target="_blank" rel="noreferrer">PR #{session.pr_number}</a>}
    </>
  );

  return (
    <>
      <div className="spec-bar">
        <span className={`spec-phase spec-phase-${phase}`}>{PHASE_LABEL[phase]}</span>
        {running && phase === "review" && <span className="spec-bar-note">revising from your comments</span>}
        <span className="spec-bar-links">{links}</span>
        <span className="spec-bar-actions">
          {spec?.content && (
            <button type="button" className="btn-ghost" onClick={() => setOpen(true)}>
              View spec
            </button>
          )}
          {actions}
        </span>
      </div>

      {open && spec?.content && (
        <div className="spec-sheet" role="dialog" aria-modal="true" aria-label="Spec">
          <div className="spec-sheet-header">
            <span className={`spec-phase spec-phase-${phase}`}>{PHASE_LABEL[phase]}</span>
            <span className="spec-bar-links">{links}</span>
            <span className="spec-bar-actions">
              {actions}
              <button type="button" className="btn-ghost" onClick={() => setOpen(false)} aria-label="Close">
                ✕
              </button>
            </span>
          </div>
          {reviewing && (
            <p className="spec-sheet-hint">
              For changes, comment on the PR (line comments work) or message the agent; the spec is revised and
              shown here again.
            </p>
          )}
          <div className="spec-sheet-body">
            <Markdown>{spec.content}</Markdown>
          </div>
        </div>
      )}
    </>
  );
}
