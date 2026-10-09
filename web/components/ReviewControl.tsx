"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { statusLabel } from "@/lib/format";
import type { Session } from "@/lib/types";
import { QcMark } from "./images/QcMark";

/**
 * Code review for a session's pull request: run QC on it, and see the
 * latest review. A review is its own session, linked from here.
 */
export function ReviewControl({ session }: { session: Session }) {
  const [review, setReview] = useState<Session | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reviewId = session.review_session_id ?? null;
  useEffect(() => {
    setReview(null);
    if (reviewId == null) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const current = await api.getSession(reviewId);
        if (!alive) return;
        setReview(current);
        if (current.status === "running" || current.status === "queued") timer = setTimeout(poll, 5_000);
      } catch {
        // Try again on the next change.
      }
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [reviewId, session.updated_at]);

  if (session.pr_number == null || session.review_of) return null;
  const busy = review != null && (review.status === "running" || review.status === "queued");

  async function start() {
    setStarting(true);
    setError(null);
    try {
      setReview(await api.startReview(session.id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="review-control">
      <button type="button" className="btn-ghost review-button" disabled={busy || starting} onClick={start}>
        <QcMark />
        {busy ? "QC is reviewing…" : review ? "Review again" : "Run code review"}
      </button>
      {review && (
        <Link href={`/sessions/${review.ref}`} className="review-link">
          Latest review · {statusLabel(review.status)}
        </Link>
      )}
      {error && <span className="review-error">{error}</span>}
    </div>
  );
}

/** On a review session: which pull request, and whose, it checks. */
export function ReviewOf({ session }: { session: Session }) {
  const [parent, setParent] = useState<Session | null>(null);
  useEffect(() => {
    if (!session.review_of) return;
    api.getSession(session.review_of).then(setParent).catch(() => {});
  }, [session.review_of]);
  if (!session.review_of) return null;
  return (
    <div className="review-of">
      <QcMark />
      QC&apos;s review of{" "}
      {parent ? (
        <>
          PR #{parent.pr_number} from <Link href={`/sessions/${parent.ref}`}>{parent.title || parent.ref}</Link>.
          Findings are posted on the PR, and Bertha fixes them in that session.
        </>
      ) : (
        "a pull request."
      )}
    </div>
  );
}
