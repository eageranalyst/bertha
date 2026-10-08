"use client";

import { useEffect, useId, useState } from "react";
import { repairMermaid } from "@/lib/mermaid";
import { useTheme } from "@/lib/theme";

/**
 * Renders one Mermaid diagram as inline SVG. Mermaid is loaded on first use so
 * the rest of the UI doesn't pay for it. If the source doesn't parse, the
 * common mistakes are repaired and it's tried again; if that fails too, the
 * code is shown with the error.
 */
export function MermaidDiagram({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // useId gives ":r0:"-style ids, which aren't valid as DOM ids for mermaid.
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  // Drawn again when the theme changes: mermaid bakes its colours into the SVG.
  const { theme } = useTheme();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let lastError: unknown = null;
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, theme: theme === "light" ? "default" : "dark", securityLevel: "strict" });
        // Parse first: a failed render makes mermaid print its own error box into
        // the page, which a later successful attempt wouldn't remove.
        const attempts = [...new Set([chart, repairMermaid(chart)])];
        let source: string | null = null;
        for (const candidate of attempts) {
          try {
            await mermaid.parse(candidate);
            source = candidate;
            break;
          } catch (err) {
            lastError = err;
          }
        }
        if (source !== null) {
          const rendered = await mermaid.render(id, source);
          if (!cancelled) {
            setSvg(rendered.svg);
            setError(null);
          }
          return;
        }
      } catch (err) {
        lastError = err;
      }
      if (!cancelled) {
        setSvg(null);
        setError(lastError instanceof Error ? lastError.message : String(lastError));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id, theme]);

  if (error) {
    return (
      <div className="mermaid-error">
        <div className="mermaid-error-message">Diagram could not be rendered: {error}</div>
        <pre>{chart}</pre>
      </div>
    );
  }
  if (!svg) return <div className="mermaid-loading">Rendering diagram…</div>;
  return <div className="mermaid-diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}
