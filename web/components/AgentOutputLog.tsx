import { parseAgentLog, type ToolEvent } from "@/lib/parseAgentLog";
import { Markdown } from "./Markdown";
import { MessageRow, MessageBubble } from "./ChatPrimitives";
import { TodoChecklist, todoProgress } from "./TodoList";

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
          if (seg.event.type === "compaction") {
            // OpenCode summarised the conversation to free up context. It's the
            // agent's own notes, not a reply, so it sits collapsed.
            return (
              <details className="compaction-marker" key={i}>
                <summary>Context compacted: the agent summarised its progress to continue</summary>
                <div className="compaction-body">
                  <Markdown>{seg.event.text}</Markdown>
                </div>
              </details>
            );
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
          if (seg.event.todos) {
            return <PlanChip key={i} todos={seg.event.todos} />;
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

/** A to-do list update: the progress, and the list when opened. */
function PlanChip({ todos }: { todos: NonNullable<ToolEvent["todos"]> }) {
  const { done, total } = todoProgress(todos);
  return (
    <details className="action-chip plan-chip">
      <summary>
        <span className="action-icon">☑</span>
        <span className="action-tool">plan</span>
        <span className="action-title">
          Updated · {done}/{total} done
        </span>
      </summary>
      <div className="plan-chip-body">
        <TodoChecklist todos={todos} />
      </div>
    </details>
  );
}
