/**
 * Repairs the most common way models get Mermaid flowcharts wrong: a quoted
 * label used as the node itself, as in `"Order service" --> "scoring"`.
 * Mermaid needs an id on each node, so each label gets one:
 * `n1["Order service"] --> n2["scoring"]`.
 *
 * Quotes already inside `id["label"]` or an edge label (`|"text"|`) are left
 * alone. Only flowchart and graph diagrams are touched.
 */
export function repairMermaid(source: string): string {
  const header = source.trimStart().split("\n")[0] ?? "";
  if (!/^(flowchart|graph)\b/.test(header)) return source;

  const ids = new Map<string, string>();
  const idFor = (label: string) => {
    let id = ids.get(label);
    if (!id) {
      id = `n${ids.size + 1}`;
      ids.set(label, id);
    }
    return id;
  };

  return source
    .split("\n")
    .map((line) => line.replace(/(?<!\[|\||")"([^"\n]+)"/g, (_match, label: string) => `${idFor(label)}["${label}"]`))
    .join("\n");
}
