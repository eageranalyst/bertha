import type { ReactNode } from "react";
import { describeTool, failureReason, groupLabel, type ToolKind } from "@/lib/describeTool";
import { parseAgentLog, type AgentLogSegment, type ToolEvent } from "@/lib/parseAgentLog";
import { Markdown } from "./Markdown";
import { MessageRow, MessageBubble } from "./ChatPrimitives";
import { TodoChecklist, todoProgress } from "./TodoList";

// A run of this many calls to the same tool, back to back, shows as one line.
const GROUP_AT = 3;

const KIND_ICONS: Record<ToolKind, ReactNode> = {
  search: <><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14" /></>,
  read: <><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3M6 8h4M6 11h4" /></>,
  edit: <><path d="M3 13l1-3 7-7 2 2-7 7z" /><path d="M9.5 4.5l2 2" /></>,
  run: <><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M5 7l2 1.5L5 10M8.5 10.5h2.5" /></>,
  github: <><circle cx="4.5" cy="4" r="1.5" /><circle cx="4.5" cy="12" r="1.5" /><circle cx="11.5" cy="6" r="1.5" /><path d="M4.5 5.5v5M11.5 7.5c0 2-2 2.5-5.5 3.5" /></>,
  wiki: <><path d="M2.5 3.5c2-1 4-1 5.5.5 1.5-1.5 3.5-1.5 5.5-.5v9c-2-1-4-1-5.5.5-1.5-1.5-3.5-1.5-5.5-.5z" /><path d="M8 4v9" /></>,
  helper: <><circle cx="8" cy="5.5" r="2.5" /><path d="M3.5 13.5c.5-2.5 2.3-4 4.5-4s4 1.5 4.5 4" /></>,
  other: <><circle cx="8" cy="8" r="2" /><path d="M8 2v2M8 12v2M2 8h2M12 8h2M3.8 3.8l1.4 1.4M10.8 10.8l1.4 1.4M3.8 12.2l1.4-1.4M10.8 5.2l1.4-1.4" /></>,
};

function KindIcon({ kind }: { kind: ToolKind }) {
  return (
    <svg viewBox="0 0 16 16" className={`action-icon kind-${kind}`} aria-hidden="true">
      {KIND_ICONS[kind]}
    </svg>
  );
}

type Item =
  | { kind: "segment"; seg: AgentLogSegment }
  | { kind: "group"; events: ToolEvent[] };

/** Segments with back-to-back calls to the same tool folded into groups. */
function groupRuns(segments: AgentLogSegment[]): Item[] {
  const items: Item[] = [];
  let run: ToolEvent[] = [];
  const flush = () => {
    if (run.length >= GROUP_AT) items.push({ kind: "group", events: run });
    else for (const event of run) items.push({ kind: "segment", seg: { kind: "event", event } });
    run = [];
  };
  for (const seg of segments) {
    const tool = seg.kind === "event" && seg.event.type === "tool_use" && !seg.event.todos ? seg.event : null;
    if (tool && (run.length === 0 || run[0].tool === tool.tool)) {
      run.push(tool);
      continue;
    }
    flush();
    if (tool) run.push(tool);
    else items.push({ kind: "segment", seg });
  }
  flush();
  return items;
}

/** One turn's output, said by Bertha, or by QC in a review session. */
export function AgentOutputLog({ content, time, qc = false }: { content: string; time: string; qc?: boolean }) {
  const items = groupRuns(parseAgentLog(content));
  return (
    <MessageRow role={qc ? "qc" : "agent"} label={qc ? "QC" : "Bertha"} time={time}>
      <div className="agent-turn">
        {items.map((item, i) => {
          if (item.kind === "group") return <ActionGroup key={i} events={item.events} />;
          const seg = item.seg;
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

/** One line in words: what it did, to what, and how it went. */
function ActionLine({ event }: { event: ToolEvent }) {
  const d = describeTool(event);
  const failed = failureReason(event);
  return (
    <>
      <KindIcon kind={d.kind} />
      <span className="action-text">
        <span className="action-verb">{d.verb}</span>
        {d.target && <code className="action-target">{d.target}</code>}
        {d.where && <span className="action-where">{d.where}</span>}
      </span>
      {failed ? (
        <span className="tool-badge">{failed}</span>
      ) : (
        d.result && <span className="action-result">{d.result}</span>
      )}
    </>
  );
}

function ActionChip({ event }: { event: ToolEvent }) {
  const failed = event.status === "error";
  const body = event.diff || event.error || event.output;
  return (
    <details className={"action-chip" + (failed ? " action-failed" : "")} open={failed}>
      <summary title={event.tool}>
        <ActionLine event={event} />
      </summary>
      {body && <pre className={event.diff ? "tool-diff" : "tool-output"}>{body}</pre>}
    </details>
  );
}

/** A run of the same call: "Read 10 issues: #110, #108, …", each one inside. */
function ActionGroup({ events }: { events: ToolEvent[] }) {
  const descriptions = events.map(describeTool);
  const failed = events.filter((e) => e.status === "error").length;
  const targets = [...new Set(descriptions.map((d) => d.target).filter(Boolean))] as string[];
  const shown = targets.slice(0, 4);
  return (
    <details className={"action-chip action-group" + (failed ? " action-failed" : "")}>
      <summary title={events[0].tool}>
        <KindIcon kind={descriptions[0].kind} />
        <span className="action-text">
          <span className="action-verb">{groupLabel(descriptions[0], events.length)}</span>
          {shown.map((t) => (
            <code className="action-target" key={t}>
              {t}
            </code>
          ))}
          {targets.length > shown.length && <span className="action-where">and {targets.length - shown.length} more</span>}
        </span>
        {failed > 0 && <span className="tool-badge">{failed} failed</span>}
      </summary>
      <div className="action-group-body">
        {events.map((e, i) => (
          <ActionChip key={i} event={e} />
        ))}
      </div>
    </details>
  );
}

/** A to-do list update: the progress, and the list when opened. */
function PlanChip({ todos }: { todos: NonNullable<ToolEvent["todos"]> }) {
  const { done, total } = todoProgress(todos);
  return (
    <details className="action-chip plan-chip">
      <summary>
        <svg viewBox="0 0 16 16" className="action-icon kind-plan" aria-hidden="true">
          <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
          <path d="M5 8.2l2 2 4-4.4" />
        </svg>
        <span className="action-text">
          <span className="action-verb">Updated the plan</span>
          <span className="action-where">
            {done} of {total} done
          </span>
        </span>
      </summary>
      <div className="plan-chip-body">
        <TodoChecklist todos={todos} />
      </div>
    </details>
  );
}
