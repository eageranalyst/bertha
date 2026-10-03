"use client";

import { useEffect, useId, useState } from "react";

/**
 * Renders one Mermaid diagram as inline SVG. Mermaid is loaded on
 * first use so the rest of the UI doesn't pay for it. If the source
 * doesn't parse, the code is shown as-is with the error.
 */
export function MermaidDiagram({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // useId gives ":r0:"-style ids, which aren't valid as DOM ids for mermaid.
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
        const rendered = await mermaid.render(id, chart);
        if (!cancelled) {
          setSvg(rendered.svg);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setSvg(null);
          setError((err as Error).message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

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
