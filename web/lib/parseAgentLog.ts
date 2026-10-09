// The container's stdout for a code turn is a mix of our own echo
// lines (git/push output, PR URL, FACTORY_* markers) and OpenCode's
// NDJSON event stream. This turns that raw blob into segments a
// component can render sensibly instead of dumping it as one wall of
// text -- plain lines stay plain (ANSI codes stripped), and
// recognized OpenCode events (tool calls, model text) become
// structured blocks. step_start/step_finish and empty text events are
// pure noise and are dropped entirely; anything else JSON-shaped but
// unrecognized falls back to being shown as a plain line rather than
// silently disappearing.

// Matches a real ESC byte (\x1b) or U+241B, the visible "escape
// symbol" glyph some terminals/paste paths substitute for it.
const ANSI_RE = /[\x1b␛]\[[0-9;]*m/g;

/** One item of the agent's to-do list (OpenCode's todowrite tool). */
export interface Todo {
  content: string;
  status: "pending" | "in_progress" | "completed" | "cancelled" | string;
  priority?: string;
}

export interface ToolEvent {
  type: "tool_use";
  tool?: string;
  title?: string;
  status?: string;
  output?: string;
  error?: string;
  diff?: string;
  /** The whole list after a todowrite call. */
  todos?: Todo[];
  /** What the agent passed the tool: a path, pattern, command, issue number... */
  input?: Record<string, unknown>;
}

export interface TextEvent {
  type: "text";
  text: string;
}

/** OpenCode's summary when it compacts a long conversation to free context. */
export interface CompactionEvent {
  type: "compaction";
  text: string;
}

export type AgentEvent = ToolEvent | TextEvent | CompactionEvent;

// OpenCode doesn't mark compaction summaries, but always writes them from one
// fixed template; these headings together only appear in that.
export function isCompactionSummary(text: string): boolean {
  return text.startsWith("## Objective") && text.includes("## Work State") && text.includes("## Next Move");
}

export type AgentLogSegment = { kind: "plain"; text: string } | { kind: "event"; event: AgentEvent };

function toToolEvent(obj: any): ToolEvent {
  const part = obj?.part ?? {};
  const state = part.state ?? {};
  const diff =
    typeof state.metadata?.diff === "string" ? state.metadata.diff : state.metadata?.filediff?.patch;
  return {
    type: "tool_use",
    tool: part.tool,
    title: state.title,
    status: state.status,
    output: state.output ?? state.metadata?.output,
    error: state.error,
    diff,
    todos: part.tool === "todowrite" ? toTodos(state.input?.todos ?? state.metadata?.todos) : undefined,
    input: state.input && typeof state.input === "object" ? state.input : undefined,
  };
}

function toTodos(value: unknown): Todo[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value
    .filter((t) => t && typeof t.content === "string")
    .map((t) => ({ content: t.content, status: String(t.status ?? "pending"), priority: t.priority }));
}

/**
 * The agent's most recent to-do list in these turns' outputs (oldest first),
 * and whether its conversation was compacted since: after a compaction the
 * agent may carry on without updating a list it can no longer see.
 */
export function latestTodos(outputs: string[]): { todos: Todo[]; stale: boolean } | null {
  let todos: Todo[] | null = null;
  let stale = false;
  for (const output of outputs) {
    for (const seg of parseAgentLog(output)) {
      if (seg.kind !== "event") continue;
      if (seg.event.type === "tool_use" && seg.event.todos) {
        todos = seg.event.todos;
        stale = false;
      } else if (seg.event.type === "compaction" && todos) {
        stale = true;
      }
    }
  }
  return todos ? { todos, stale } : null;
}

export function parseAgentLog(raw: string): AgentLogSegment[] {
  const segments: AgentLogSegment[] = [];
  let plainBuffer: string[] = [];

  function flushPlain() {
    const text = plainBuffer.join("\n").trim();
    if (text) segments.push({ kind: "plain", text });
    plainBuffer = [];
  }

  for (const rawLine of raw.split("\n")) {
    const line = rawLine.replace(ANSI_RE, "");
    const trimmed = line.trim();
    if (!trimmed) continue;

    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      let obj: unknown;
      try {
        obj = JSON.parse(trimmed);
      } catch {
        obj = null;
      }
      const type = (obj as { type?: string } | null)?.type;
      if (type === "step_start" || type === "step_finish") {
        continue; // noise, drop entirely
      }
      if (type === "text") {
        const text = ((obj as { part?: { text?: string } }).part?.text ?? "").trim();
        if (!text) continue; // empty text event, drop entirely
        flushPlain();
        segments.push({
          kind: "event",
          event: isCompactionSummary(text) ? { type: "compaction", text } : { type: "text", text },
        });
        continue;
      }
      if (type === "tool_use") {
        flushPlain();
        segments.push({ kind: "event", event: toToolEvent(obj) });
        continue;
      }
      // JSON but not a recognized shape -- fall through to plain so
      // nothing silently vanishes.
    }

    plainBuffer.push(line);
  }
  flushPlain();
  return segments;
}
