import type { ReactNode } from "react";

/** A little open book with a sleepy face, shared by both empty states. */
function Mascot({ children }: { children?: ReactNode }) {
  return (
    <svg className="wiki-mascot" viewBox="0 0 120 96" aria-hidden="true">
      <ellipse cx="60" cy="88" rx="38" ry="4" className="wiki-mascot-shadow" />
      <path d="M60 22 C44 12 26 12 12 18 V76 C26 70 44 70 60 80 Z" className="wiki-mascot-page" />
      <path d="M60 22 C76 12 94 12 108 18 V76 C94 70 76 70 60 80 Z" className="wiki-mascot-page wiki-mascot-page-right" />
      <path d="M22 34 q6 -4 12 0" className="wiki-mascot-line" />
      <path d="M22 44 q6 -4 12 0" className="wiki-mascot-line" />
      <path d="M86 34 q6 -4 12 0" className="wiki-mascot-line" />
      <path d="M86 44 q6 -4 12 0" className="wiki-mascot-line" />
      <path d="M44 46 q4 3 8 0" className="wiki-mascot-face" />
      <path d="M68 46 q4 3 8 0" className="wiki-mascot-face" />
      <circle cx="38" cy="54" r="3" className="wiki-mascot-blush" />
      <circle cx="82" cy="54" r="3" className="wiki-mascot-blush" />
      {children}
    </svg>
  );
}

export function WikiNotIndexed({ onIndex, busy }: { onIndex: () => void; busy: boolean }) {
  return (
    <div className="wiki-empty">
      <Mascot>
        <path d="M96 14 l2 5 l5 2 l-5 2 l-2 5 l-2 -5 l-5 -2 l5 -2 z" className="wiki-mascot-sparkle" />
      </Mascot>
      <h3 className="wiki-empty-title">Nothing written here yet</h3>
      <p className="wiki-empty-body">
        This repo hasn&apos;t been read yet. Indexing clones it, scans the code, and has the local
        model write a wiki from what it finds. It takes a few minutes.
      </p>
      <button type="button" className="btn-primary" disabled={busy} onClick={onIndex}>
        Index this repo
      </button>
    </div>
  );
}

export function WikiPageMissing({ slug, onHome }: { slug: string; onHome: () => void }) {
  return (
    <div className="wiki-empty">
      <Mascot>
        <path d="M30 20 l8 0 M34 16 l0 8" className="wiki-mascot-sparkle" />
      </Mascot>
      <h3 className="wiki-empty-title">Page not found</h3>
      <p className="wiki-empty-body">
        Nothing in this wiki is called <code>{slug}</code>. It may have been dropped on the last
        rebuild, or the link is out of date.
      </p>
      <button type="button" className="btn-ghost" onClick={onHome}>
        Back to the overview
      </button>
    </div>
  );
}
