"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { ChevronDown, Users } from "lucide-react";
import Link from "next/link";
import { ChatMessageComponent } from "@/app/components/chat-message";
import type { ToolCallEntry } from "@/features/chat/streamingTypes";
import { codingPublicationReviewIssues, codingReason, codingRequest, codingTaskLabel, terminalCodingStatuses, type CodingRun, type CodingView } from "./codingApi";

const states: Record<string, string> = { queued: "Ожидаю запуска.", running: "Работаю над задачей.", succeeded: "Работа завершена.", failed: "Работа остановлена из-за ошибки.", cancelled: "Работа отменена." };

export function CodingRunChat({ run }: { run: CodingRun }) {
  const [expanded, setExpanded] = useState(true);
  const activityId = useId();
  const history = useRef<{ runId: string; cursor: number; events: NonNullable<CodingView["progress"]["events"]> }>({ runId: run.id, cursor: 0, events: [] });
  const query = useQuery({
    queryKey: ["coding-chat-progress", run.id],
    queryFn: async () => {
      const previous = history.current.runId === run.id ? history.current : { runId: run.id, cursor: 0, events: [] };
      let cursor = previous.cursor;
      const events = [...previous.events];
      let page: CodingView;
      do {
        page = await codingRequest<CodingView>(`coding-runs/${run.id}?after=${cursor}`);
        events.push(...(page.progress.events ?? []));
        const next = page.progress.cursor;
        if (next <= cursor) break;
        cursor = next;
      } while ((page.progress.events?.length ?? 0) === 200);
      history.current = { runId: run.id, cursor, events };
      return { ...page, progress: { ...page.progress, cursor, events } };
    },
    refetchInterval: state => terminalCodingStatuses.has(state.state.data?.run.status ?? "") ? false : 3000,
  });
  if (!query.data) return query.error
    ? <p role="alert">Не удалось получить работу субагентов: {query.error.message} <button type="button" className="underline" onClick={() => { void query.refetch(); }}>Повторить</button></p>
    : <p role="status" className="px-4 text-sm text-muted">Загружаю работу команды…</p>;
  const view = query.data;
  const participants = view.progress.tasks.filter(task => task.role !== "coordinator");
  const completed = participants.filter(task => task.status === "succeeded").length;
  return <section aria-label="Работа субагентов" data-coding-run-id={run.id} className="space-y-4">
    <button type="button" aria-expanded={expanded} aria-controls={activityId} onClick={() => setExpanded(value => !value)} className="flex w-full flex-wrap items-center gap-2 rounded-xl border border-border bg-surface/40 px-4 py-3 text-left text-xs text-secondary hover:bg-hover">
      <Users className="h-4 w-4 text-cyan" aria-hidden="true" />
      <span className="font-semibold text-primary">{view.run.team_snapshot?.team_name ?? "Работа команды"}</span>
      <span>{completed}/{participants.length} участников завершили работу</span>
      <span className="ml-auto">{states[view.run.status] ?? view.run.status}</span>
      <ChevronDown className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />
    </button>
    {expanded && <div id={activityId} className="space-y-4">
    {query.error && <p role="alert">Не удалось обновить работу субагентов: {query.error.message}</p>}
    {view.run.team_snapshot && !view.run.team_snapshot.legacy && <CoordinatorMessage view={view} runId={run.id} />}
    {view.progress.tasks.filter(task => task.role === "developer" || task.role === "qa").map(task => {
      const role = task.role as "developer" | "qa";
      const events = (view.progress.events ?? []).filter(event => event.task_id === task.id);
      const calls = new Map<string, ToolCallEntry>();
      for (const event of events) {
        const value = event.payload;
        if (event.type === "agent.tool_started" && value.tool_call_id && value.tool_name) {
          calls.set(value.tool_call_id, { id: value.tool_call_id, tool_name: value.tool_name, arguments: value.arguments, started_at: event.created_at, sequence: event.sequence });
        } else if (event.type === "agent.tool_completed" && value.tool_call_id) {
          const call = calls.get(value.tool_call_id);
          if (call) calls.set(value.tool_call_id, { ...call, completed_at: event.created_at, result_summary: value.result_summary, success: value.success });
        }
      }
      const messages = events.filter(event => event.type === "agent.message" && event.payload.content);
      const text = messages.length ? messages.map(event => `${event.payload.content}${event.payload.truncated ? "\n\nСообщение сокращено." : ""}`).join("\n\n")
        : task.iterations_used === 0 && terminalCodingStatuses.has(view.run.status) ? "Не запускался: Coding остановился до начала моей работы."
          : role === "qa" && task.status === "queued" ? "Проверю изменения независимо, когда координатор передаст candidate." : states[task.status] ?? task.status;
      const running = task.status === "running" && !terminalCodingStatuses.has(view.run.status);
      const deliveryState = running ? "streaming" : task.status === "failed" ? "error" : task.status === "cancelled" ? "cancelled" : task.status === "queued" ? "idle" : "complete";
      return <div key={task.id}>
        <ChatMessageComponent timestamp={new Date(events[0]?.created_at ?? run.created_at)} role="assistant" assistantLabel={task.team_member_label ? `${codingTaskLabel(task)} — ${role === "qa" ? "независимая проверка" : "изменения"}` : role === "qa" ? "QA — независимая проверка" : "Developer — разработка"} subagentRole={role} text={text} copyText={text} toolCalls={[...calls.values()]} isStreaming={running} isComplete={!running && task.status !== "queued"} deliveryState={deliveryState} />
        <p className="mt-2 px-5 text-xs text-muted">{task.iterations_used}/{task.max_iterations} итераций{view.run.reason_code ? ` · ${codingReason(view.run.reason_code)}` : ""} · <Link className="text-cyan hover:underline" href={`/coding-runs/${run.id}`}>Изменения и проверки</Link></p>
      </div>;
    })}
    </div>}
    <MergeRequestMessage receipt={view.receipt} />
  </section>;
}

function MergeRequestMessage({ receipt }: Pick<CodingView, "receipt">) {
  if (receipt?.state !== "mr_recorded") return null;
  const mergeRequestUrl = safeHttpUrl(receipt.merge_request_url);
  if (mergeRequestUrl === null) return null;
  const text = [
    "Команда завершила работу. Draft Merge Request создан.",
    `Ветка публикации: \`${receipt.branch_name}\`.`,
    `Ссылка на Draft MR: <${mergeRequestUrl}>`,
  ].join("\n\n");
  return <ChatMessageComponent role="assistant" assistantLabel="Публикация" text={text} copyText={text} isComplete deliveryState="complete" />;
}

function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

const memberStatus: Record<string, string> = { candidate_recorded: "candidate сохранён", passed: "проверка пройдена", failed: "найдены замечания", blocked: "заблокировано" };

/** The coordinator's turn in the same message format as its members: assignments are its tool calls. */
function CoordinatorMessage({ view, runId }: { view: CodingView; runId: string }) {
  const task = view.progress.tasks.find(item => item.role === "coordinator");
  const team = view.run.team_snapshot!;
  const events = view.progress.events ?? [];
  const calls = new Map<string, ToolCallEntry>();
  for (const event of events) {
    const value = event.payload;
    const key = value.event_key ?? String(event.sequence);
    if (event.type === "team.member_started") {
      calls.set(key, { id: key, tool_name: value.member_label ?? "Участник", arguments: { поручение: `${value.assignment ?? ""}${value.assignment_truncated ? " …(в журнале сокращено, участнику передано полностью)" : ""}` }, started_at: event.created_at, sequence: event.sequence });
    } else if (event.type === "team.member_completed") {
      const call = calls.get(key);
      const status = memberStatus[value.status ?? ""] ?? value.status ?? "";
      if (call) calls.set(key, { ...call, completed_at: event.created_at, result_summary: value.summary ? `${status}: ${value.summary}` : status, success: value.status === "passed" || value.status === "candidate_recorded" });
    }
  }
  const coordinatorEvents = events.filter(event => event.task_id === task?.id);
  const messages = coordinatorEvents.filter(event => event.type === "agent.message" && event.payload.content).map(event => event.payload.content);
  const decision = coordinatorEvents.find(event => event.type === "team.coordinator_decision")?.payload;
  const text = [...messages, decision ? `${decision.decision === "publish" ? "Решение: публиковать." : "Решение: остановить работу."}${decision.summary ? `\n\n${decision.summary}` : ""}` : "", ...codingPublicationReviewIssues(view)].filter(Boolean).join("\n\n")
    || (task?.status === "queued" ? "Готовлю поручения участникам команды." : states[task?.status ?? ""] ?? "");
  const running = task?.status === "running" && !terminalCodingStatuses.has(view.run.status);
  const deliveryState = running ? "streaming" : task?.status === "failed" ? "error" : task?.status === "cancelled" ? "cancelled" : task?.status === "queued" ? "idle" : "complete";
  return <div>
    <ChatMessageComponent timestamp={new Date(coordinatorEvents[0]?.created_at ?? view.run.created_at)} role="assistant" assistantLabel={`Координатор — команда «${team.team_name}», ревизия ${team.revision}`} text={text} copyText={text} toolCalls={[...calls.values()]} isStreaming={running} isComplete={!running && task?.status !== "queued"} deliveryState={deliveryState} />
    <p className="mt-2 px-5 text-xs text-muted">{task ? `${task.iterations_used}/${task.max_iterations} итераций` : ""}{view.run.reason_code ? ` · ${codingReason(view.run.reason_code)}` : ""} · <Link className="text-cyan hover:underline" href={`/coding-runs/${runId}`}>Изменения и проверки</Link></p>
  </div>;
}

export function ConversationCodingRuns({ runs }: { runs: CodingRun[] }) {
  return <>{runs.map(run => <CodingRunChat key={run.id} run={run} />)}</>;
}
