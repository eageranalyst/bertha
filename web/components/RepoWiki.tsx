"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { WikiPage, WikiResponse } from "@/lib/types";
import { Markdown } from "./Markdown";
import { WikiNotIndexed, WikiPageMissing } from "./WikiNotFound";
import { WikiStatusPill } from "./WikiStatusPill";

const HOME_SLUG = "overview";

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

/** api.ts throws "<status> <body>" for failed requests. */
function isNotFound(err: unknown) {
  return err instanceof Error && err.message.startsWith("404");
}

export function RepoWiki({ repoId }: { repoId: number }) {
  const [data, setData] = useState<WikiResponse | null>(null);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [page, setPage] = useState<WikiPage | null>(null);
  const [missingSlug, setMissingSlug] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const index = data?.index ?? null;
  const indexing = index?.status === "indexing";
  const pages = useMemo(() => data?.pages ?? [], [data]);

  const refresh = useCallback(async () => {
    try {
      setData(await api.getWiki(repoId));
    } catch (err) {
      setError(errorText(err));
    }
  }, [repoId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Only poll while a build is running; otherwise the page is static.
  useEffect(() => {
    if (!indexing) return;
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [indexing, refresh]);

  const current =
    pages.find((p) => p.slug === selectedSlug) ??
    pages.find((p) => p.slug === HOME_SLUG) ??
    pages[0] ??
    null;
  const currentSlug = current?.slug ?? null;
  const currentUpdatedAt = current?.updated_at ?? null;

  // Refetch the body when the page changes, or when a rebuild rewrote it.
  useEffect(() => {
    if (!currentSlug) {
      setPage(null);
      setMissingSlug(null);
      return;
    }
    let alive = true;
    api
      .getWikiPage(repoId, currentSlug)
      .then((p) => {
        if (!alive) return;
        setPage(p);
        setMissingSlug(null);
      })
      .catch((err) => {
        if (!alive) return;
        if (isNotFound(err)) {
          setPage(null);
          setMissingSlug(currentSlug);
        } else {
          setError(errorText(err));
        }
      });
    return () => {
      alive = false;
    };
  }, [repoId, currentSlug, currentUpdatedAt]);

  const sections = useMemo(() => {
    const groups: { name: string; pages: typeof pages }[] = [];
    for (const p of pages) {
      let group = groups.find((g) => g.name === p.section);
      if (!group) {
        group = { name: p.section, pages: [] };
        groups.push(group);
      }
      group.pages.push(p);
    }
    return groups;
  }, [pages]);

  async function startIndex(force: boolean) {
    setStarting(true);
    setError(null);
    try {
      setData(await api.indexWiki(repoId, force));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setStarting(false);
    }
  }

  const busy = starting || indexing;
  const commit = index?.commit_sha?.slice(0, 7);

  return (
    <div className="wiki">
      <div className="wiki-toolbar">
        <div className="wiki-status">
          <WikiStatusPill status={index?.status ?? null} />
          <span className="wiki-progress">
            {indexing
              ? (index?.progress ?? "Working…")
              : index
                ? `${pages.length} page${pages.length === 1 ? "" : "s"}${commit ? ` · commit ${commit}` : ""}`
                : "Not indexed yet"}
          </span>
        </div>
        <div className="wiki-actions">
          {index ? (
            <>
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => startIndex(false)}>
                Re-index
              </button>
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => startIndex(true)}>
                Rebuild
              </button>
            </>
          ) : (
            <button type="button" className="btn-primary" disabled={busy} onClick={() => startIndex(false)}>
              Index wiki
            </button>
          )}
        </div>
      </div>

      {index && (
        <div className="wiki-times">
          <span>Created {fmtTime(index.created_at)}</span>
          <span>Last indexed {fmtTime(index.last_indexed_at)}</span>
          <span>Updated {fmtTime(index.updated_at)}</span>
        </div>
      )}

      {index?.status === "failed" && index.error && (
        <div className="wiki-error">Last build failed: {index.error}</div>
      )}
      {error && <div className="wiki-error">{error}</div>}

      {pages.length === 0 ? (
        indexing ? (
          <div className="empty-state">{index?.progress ?? "Indexing…"}</div>
        ) : (
          <WikiNotIndexed onIndex={() => startIndex(false)} busy={busy} />
        )
      ) : (
        <div className="wiki-body">
          <nav className="wiki-nav" aria-label="Wiki pages">
            {sections.map((section) => (
              <div key={section.name} className="wiki-nav-section">
                <div className="wiki-nav-heading">{section.name}</div>
                {section.pages.map((p) => (
                  <button
                    key={p.slug}
                    type="button"
                    className={"wiki-nav-link" + (p.slug === currentSlug ? " active" : "")}
                    onClick={() => setSelectedSlug(p.slug)}
                  >
                    {p.title}
                  </button>
                ))}
              </div>
            ))}
          </nav>

          <article className="wiki-article">
            {missingSlug ? (
              <WikiPageMissing slug={missingSlug} onHome={() => setSelectedSlug(HOME_SLUG)} />
            ) : page ? (
              <>
                <h2 className="wiki-title">{page.title}</h2>
                <div className="wiki-page-meta">Updated {fmtTime(page.updated_at)}</div>
                <Markdown onInternalLink={setSelectedSlug}>{page.body}</Markdown>
              </>
            ) : (
              <div className="empty-state">Loading…</div>
            )}
          </article>
        </div>
      )}
    </div>
  );
}
