import type { ReactNode } from "react";

/** A little open book with a sleepy face, shared by both empty states. */
export function BookMascot({ children }: { children?: ReactNode }) {
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
