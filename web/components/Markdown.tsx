import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { MermaidDiagram } from "./MermaidDiagram";

/** Flattens React children back to the source text they were rendered from. */
function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

/**
 * Renders Markdown. Pass `onInternalLink` when links between pages of
 * the same wiki should navigate in place rather than open a new tab.
 * A ```mermaid fence renders as a diagram.
 */
export function Markdown({
  children,
  onInternalLink,
}: {
  children: string;
  onInternalLink?: (slug: string) => void;
}) {
  const components: Components = {
    a({ href, children: linkChildren }) {
      const isInternal = href !== undefined && !/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith("#");
      if (isInternal && onInternalLink) {
        return (
          <a
            href={`#${href}`}
            onClick={(e) => {
              e.preventDefault();
              onInternalLink(href);
            }}
          >
            {linkChildren}
          </a>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {linkChildren}
        </a>
      );
    },
    pre({ children: preChildren }) {
      const child = Array.isArray(preChildren) ? preChildren[0] : preChildren;
      if (isValidElement<{ className?: string; children?: ReactNode }>(child)) {
        const language = String(child.props.className ?? "");
        if (language.includes("language-mermaid")) {
          return <MermaidDiagram chart={textOf(child.props.children).trim()} />;
        }
      }
      return <pre>{preChildren}</pre>;
    },
  };

  return (
    <div className="log-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
