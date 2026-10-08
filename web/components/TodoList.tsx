"use client";

import { useEffect, useState } from "react";
import type { Todo } from "@/lib/parseAgentLog";

const STATUS_LABEL: Record<string, string> = {
  completed: "Done",
  in_progress: "In progress",
  pending: "To do",
  cancelled: "Cancelled",
};

function StatusMark({ status }: { status: string }) {
  if (status === "completed") {
    return (
      <svg viewBox="0 0 16 16" className="todo-mark done" aria-hidden="true">
        <circle cx="8" cy="8" r="7" />
        <path d="M4.8 8.3l2.1 2.1 4.3-4.6" />
      </svg>
    );
  }
  if (status === "in_progress") {
    return (
      <svg viewBox="0 0 16 16" className="todo-mark active" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" />
        <circle cx="8" cy="8" r="3" className="todo-mark-dot" />
      </svg>
    );
  }
  if (status === "cancelled") {
    return (
      <svg viewBox="0 0 16 16" className="todo-mark cancelled" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" />
        <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" className="todo-mark pending" aria-hidden="true">
      <circle cx="8" cy="8" r="6.5" />
    </svg>
  );
}

export function todoProgress(todos: Todo[]) {
  const counted = todos.filter((t) => t.status !== "cancelled");
  return { done: counted.filter((t) => t.status === "completed").length, total: counted.length };
}

/** The list itself, one row per item with its status mark. */
export function TodoChecklist({ todos }: { todos: Todo[] }) {
  return (
    <ul className="todo-list">
      {todos.map((t, i) => (
        <li key={i} className={`todo-item todo-${t.status}`}>
          <StatusMark status={t.status} />
          <span className="todo-text">{t.content}</span>
          <span className="visually-hidden">({STATUS_LABEL[t.status] ?? t.status})</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The agent's current to-do list, floating over the top right of the
 * transcript. Open while a turn runs; collapses to a progress pill.
 */
export function TodoFloat({ todos, running, sessionId }: { todos: Todo[]; running: boolean; sessionId: number }) {
  const [open, setOpen] = useState(running);
  // A new session, or a turn starting, opens it again.
  useEffect(() => setOpen(running), [running, sessionId]);
  const { done, total } = todoProgress(todos);
  const current = todos.find((t) => t.status === "in_progress");

  return (
    <aside className={"todo-float" + (open ? " open" : "")} aria-label="Agent's plan">
      <button type="button" className="todo-float-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="todo-float-title">Plan</span>
        <span className="todo-float-count">
          {done}/{total}
        </span>
        <span className="todo-float-bar" aria-hidden="true">
          <span style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </span>
        <span className="todo-float-toggle" aria-hidden="true">
          {open ? "–" : "+"}
        </span>
      </button>
      {!open && current && <div className="todo-float-current">{current.content}</div>}
      {open && (
        <div className="todo-float-body">
          <TodoChecklist todos={todos} />
        </div>
      )}
    </aside>
  );
}
