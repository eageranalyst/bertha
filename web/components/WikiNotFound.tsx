import { BookMascot } from "@/components/images/BookMascot";

export function WikiNotIndexed({ onIndex, busy }: { onIndex: () => void; busy: boolean }) {
  return (
    <div className="wiki-empty">
      <BookMascot>
        <path d="M96 14 l2 5 l5 2 l-5 2 l-2 5 l-2 -5 l-5 -2 l5 -2 z" className="wiki-mascot-sparkle" />
      </BookMascot>
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
      <BookMascot>
        <path d="M30 20 l8 0 M34 16 l0 8" className="wiki-mascot-sparkle" />
      </BookMascot>
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
