import { parseAgentLog, type ToolEvent } from "@/lib/parseAgentLog";
import { Markdown } from "./Markdown";
import { MessageRow, MessageBubble } from "./ChatPrimitives";

const TOOL_ICONS: Record<string, string> = {
  bash: "▶",
  edit: "✎",
  write: "✎",
  read: "📄",
  grep: "🔍",
  glob: "🔍",
  list: "📁",
};

export function AgentOutputLog({ content, time }: { content: string; time: string }) {
  const segments = parseAgentLog(content);
  return (
    <MessageRow role="agent" label="Agent" time={time}>
      <div className="agent-turn">
        {segments.map((seg, i) => {
          if (seg.kind === "plain") {
            return <pre className="console-block" key={i}>{seg.text}</pre>;
          }
          if (seg.event.type === "text") {
            // This is the agent *speaking*, as distinct from the tool
            // calls around it -- same bubble treatment as an ask
            // response gets, so it reads as "the agent said" rather
            // than blending into the surrounding log noise.
            return (
              <MessageBubble key={i}>
                <Markdown>{seg.event.text}</Markdown>
              </MessageBubble>
            );
          }
          return <ActionChip key={i} event={seg.event} />;
        })}
      </div>
    </MessageRow>
  );
}

function ActionChip({ event }: { event: ToolEvent }) {
  const failed = event.status === "error";
  const icon = TOOL_ICONS[event.tool ?? ""] ?? "⚙";
  const body = event.diff || event.error || event.output;
  return (
    <details className={"action-chip" + (failed ? " action-failed" : "")} open={failed}>
      <summary>
        <span className="action-icon">{icon}</span>
        <span className="action-tool">{event.tool ?? "tool"}</span>
        <span className="action-title">{event.title || ""}</span>
        {failed && <span className="tool-badge">rejected</span>}
      </summary>
      {body && <pre className={event.diff ? "tool-diff" : "tool-output"}>{body}</pre>}
    </details>
  );
}
