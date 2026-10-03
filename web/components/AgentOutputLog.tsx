import { parseAgentLog, type ToolEvent } from "@/lib/parseAgentLog";
import { Markdown } from "./Markdown";

export function AgentOutputLog({ content }: { content: string }) {
  const segments = parseAgentLog(content);
  return (
    <div className="agent-output">
      {segments.map((seg, i) => {
        if (seg.kind === "plain") {
          return (
            <pre className="agent-plain" key={i}>
              {seg.text}
            </pre>
          );
        }
        if (seg.event.type === "text") {
          return (
            <div className="agent-text" key={i}>
              <Markdown>{seg.event.text}</Markdown>
            </div>
          );
        }
        return <ToolUseBlock key={i} event={seg.event} />;
      })}
    </div>
  );
}

function ToolUseBlock({ event }: { event: ToolEvent }) {
  const failed = event.status === "error";
  const body = event.diff || event.error || event.output;
  return (
    <details className={"tool-block" + (failed ? " tool-failed" : "")} open={failed}>
      <summary>
        <span className="tool-name">{event.tool ?? "tool"}</span>
        <span className="tool-title">{event.title || ""}</span>
        {failed && <span className="tool-badge">rejected</span>}
      </summary>
      {body && <pre className={event.diff ? "tool-diff" : "tool-output"}>{body}</pre>}
    </details>
  );
}
