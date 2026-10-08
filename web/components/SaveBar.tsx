"use client";

import { useEffect, type ReactNode } from "react";

/**
 * Save and Discard for a settings page, with whether there's anything to
 * save. Pages put one at the top and one at the bottom, so it's in reach
 * wherever you are on a long page.
 */
export function SaveBar({
  dirty,
  busy,
  saved,
  onSave,
  onDiscard,
  children,
}: {
  dirty: boolean;
  busy: boolean;
  /** Just saved, and nothing changed since. */
  saved: boolean;
  onSave: () => void;
  onDiscard: () => void;
  /** More buttons, after Discard: "Reset to defaults", say. */
  children?: ReactNode;
}) {
  return (
    <div className="save-bar">
      <button type="button" className="btn-primary" disabled={busy || !dirty} onClick={onSave}>
        {busy ? "Saving…" : "Save changes"}
      </button>
      <button type="button" className="btn-ghost" disabled={busy || !dirty} onClick={onDiscard}>
        Discard
      </button>
      {children}
      {dirty ? (
        <span className="save-bar-status unsaved">Unsaved changes</span>
      ) : (
        saved && <span className="save-bar-status">Saved.</span>
      )}
    </div>
  );
}

/** Asks before the page is closed or reloaded with changes not saved. */
export function useUnsavedWarning(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
}
