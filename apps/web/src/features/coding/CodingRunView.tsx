"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@rhetorica/ui";
import { codingCapabilityLabels, codingCheckLabel, codingPublicationReviewIssues, codingReason, codingRequest, codingTaskLabel, terminalCodingStatuses, type CodingChatEvent, type CodingView } from "./codingApi";

const phaseLabels: Record<string, string> = { preparing: "Подготовка репозитория", developing: "Работа команды", validating: "Проверка candidate", publishing: "Публикация Draft MR", completed: "Завершено" };
const memberStatusLabels: Record<string, string> = { candidate_recorded: "candidate сохранён", passed: "проверка пройдена", failed: "найдены замечания", blocked: "заблокировано" };

export function CodingTeamTimeline({ events }: { events: CodingChatEvent[] }) {
  const items = events.filter(event => event.type === "team.member_started" || event.type === "team.member_completed" || event.type === "team.coordinator_decision");
  if (!items.length) return <p className="mt-3 text-sm text-muted">Координатор ещё не передавал поручения участникам</p>;
  return <ol aria-label="Поручения команды" className="mt-3 space-y-2">{items.map(event => {
    const value = event.payload;
    if (event.type === "team.coordinator_decision") return <li key={event.sequence} className="rounded-lg bg-elevated p-3 text-sm"><strong>Координатор</strong> · {value.decision === "publish" ? "запросил публикацию" : "остановил работу"}{value.summary && <p className="mt-1 text-muted">{value.summary}</p>}</li>;
    if (event.type === "team.member_started") return <li key={event.sequence} className="rounded-lg border border-border p-3 text-sm"><strong>{value.member_label}</strong>{value.capability && <span className="ml-2 text-xs text-muted">{codingCapabilityLabels[value.capability]}</span>}<p className="mt-1 whitespace-pre-wrap text-secondary">Поручение: {value.assignment}</p></li>;
    return <li key={event.sequence} className="ml-5 border-l border-border pl-3 text-sm"><span className={value.status === "passed" || value.status === "candidate_recorded" ? "text-success" : "text-warning"}>{value.member_label}: {memberStatusLabels[value.status ?? ""] ?? value.status}</span>{value.candidate_digest && <span className="ml-2 font-mono text-xs text-muted">{value.candidate_digest.slice(0, 12)}</span>}{value.summary && <p className="mt-1 whitespace-pre-wrap text-muted">{value.summary}</p>}</li>;
  })}</ol>;
}
const checkLabels = { passed: "Пройдено", failed: "Ошибка", blocked: "Заблокировано", not_run: "Не выполнялось" };
export function CodingRunView({ runId }: { runId: string }) {
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const cursor = useRef(0);
  // One request per refresh: the run view and its event log are read together by cursor.
  const eventLog = useRef<{ runId: string; after: number; events: CodingChatEvent[] }>({ runId, after: 0, events: [] });
  const query = useQuery({ queryKey: ["coding-run", runId], queryFn: async () => {
    const log = eventLog.current.runId === runId ? eventLog.current : { runId, after: 0, events: [] };
    let page: CodingView;
    for (;;) {
      page = await codingRequest<CodingView>(`coding-runs/${runId}?after=${log.after}`);
      const fresh = (page.progress.events ?? []).filter(event => event.sequence > log.after);
      log.events.push(...fresh);
      const advanced = page.progress.cursor > log.after;
      if (advanced) log.after = page.progress.cursor;
      if (!advanced || fresh.length < 200) break;
    }
    eventLog.current = log;
    return { ...page, progress: { ...page.progress, events: [...log.events] } };
  }, refetchInterval: query => terminalCodingStatuses.has(query.state.data?.run.status ?? "") ? false : 3000 });
  const candidateDigest = query.data?.run.candidate?.digest;
  const diff = useQuery({ queryKey: ["coding-diff", runId, candidateDigest], queryFn: () => codingRequest<{ diff: string; truncated: boolean }>(`coding-runs/${runId}/diff`), enabled: Boolean(candidateDigest) });
  useEffect(() => { cursor.current = 0; }, [runId]);
  const terminal = terminalCodingStatuses.has(query.data?.run.status ?? "");
  const { refetch } = query;
  useEffect(() => {
    if (terminal) return;
    const stream = new EventSource(`/api/coding/coding-runs/${runId}/events?after=${cursor.current}`);
    const refresh = () => { void refetch(); };
    stream.addEventListener("coding", event => {
      const sequence = Number((event as MessageEvent).lastEventId);
      if (!Number.isFinite(sequence) || sequence <= cursor.current) return;
      cursor.current = sequence; refresh();
    });
    stream.addEventListener("done", () => { stream.close(); refresh(); });
    return () => stream.close();
  }, [runId, terminal, refetch]);
  if (query.isPending) return <p role="status">Загрузка coding run…</p>;
  if (query.error) return <div role="alert"><p>{query.error.message}</p><Button onClick={() => void query.refetch()}>Повторить</Button></div>;
  if (!query.data) return null;
  const { run, progress, report, receipt } = query.data;
  return <div className="mx-auto max-w-5xl space-y-6">
    <Link className="text-sm text-cyan" href={run.conversation_id ? `/?conversation=${encodeURIComponent(run.conversation_id)}` : "/"}>← Вернуться в чат</Link>
    <header className="rounded-2xl border border-border p-5"><p className="text-xs text-cyan">Разработка кода</p><h1 className="mt-2 text-2xl font-semibold">{run.repository.project_id}</h1><p className="mt-2">{phaseLabels[run.phase] ?? run.phase} · {run.status}</p><p className="mt-2 text-sm text-muted">Целевая ветка: {run.repository.target_branch} · база {run.repository.base_sha.slice(0, 12)}</p><p className="mt-2 text-sm text-muted">Срок выполнения: {new Date(run.deadline).toLocaleString("ru-RU")}</p>{run.team_snapshot && <p className="mt-2 text-sm text-muted">Команда: {run.team_snapshot.team_name || "Developer и QA"} · ревизия {run.team_snapshot.revision} · {run.team_snapshot.members.map(member => member.label).join(", ")}</p>}
      {run.reason_code && <p role="status" className="mt-3 text-warning">{codingReason(run.reason_code)}</p>}
      {codingPublicationReviewIssues(query.data).map(issue => <p key={issue} role="alert" className="mt-2 text-warning">{issue}</p>)}
      {run.partial_result?.summary && <p className="mt-3 text-sm">{run.partial_result.summary}</p>}
      {!terminal && <Button className="mt-4" size="sm" variant="ghost" disabled={cancelling || run.status === "cancel_requested"} onClick={async () => { setCancelling(true); setError(""); try { await codingRequest(`coding-runs/${runId}/cancel`, "POST"); await query.refetch(); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка отмены"); } finally { setCancelling(false); } }}>Отменить задачу</Button>}
      {error && <p role="alert" className="text-danger">{error}</p>}
    </header>
    <section className="rounded-2xl border border-border p-5"><h2 className="font-semibold">Задачи и бюджет</h2><ul className="mt-3 space-y-2">{progress.tasks.map(task => <li key={task.id} className={task.parent_task_id ? "ml-5 border-l border-border pl-3" : ""}><span>{codingTaskLabel(task)} · {task.status}</span><span className="ml-3 text-sm text-muted">{task.iterations_used}/{task.max_iterations} итераций</span>{task.deadline && <span className="ml-3 text-xs text-muted">до {new Date(task.deadline).toLocaleTimeString("ru-RU")}</span>}</li>)}</ul>{progress.budgets.map(budget => <p key={budget.budget_owner_agent_id} className="mt-3 text-sm text-muted">Общий бюджет: {budget.reserved_iterations}/{budget.max_iterations} · осталось {Math.max(0, Math.ceil((new Date(budget.deadline).getTime() - Date.now()) / 1000))} с</p>)}</section>
    <section className="rounded-2xl border border-border p-5"><h2 className="font-semibold">Поручения команды</h2><CodingTeamTimeline events={progress.events ?? []} /></section>
    <section className="rounded-2xl border border-border p-5"><h2 className="font-semibold">Проверки</h2>{!report && <p className="mt-3 text-sm text-muted">Отчёт проверки пока не сформирован</p>}<ul className="mt-3 space-y-3">{report?.checks.map((check, i) => <li key={`${check.name}:${i}`} className="rounded-lg bg-elevated p-3"><div className="flex flex-wrap justify-between gap-2"><strong className="text-sm">{codingCheckLabel(check.name, run.team_snapshot)}</strong><span data-status={check.status} className={check.status === "passed" ? "text-success" : "text-warning"}>{checkLabels[check.status]} · {check.status}</span></div>{check.reason && <p className="mt-1 text-sm text-muted">{check.reason}</p>}{check.exit_code != null && <p className="mt-1 text-xs text-muted">Exit code: {check.exit_code}</p>}</li>)}</ul></section>
    <section className="rounded-2xl border border-border p-5"><h2 className="font-semibold">Изменения</h2>{run.candidate ? <><p className="mt-2 break-all font-mono text-xs text-muted">Candidate: {run.candidate.digest}</p><p className="mt-2 text-sm">{run.candidate.changed_paths.join(", ") || "Изменений нет"}</p>{diff.error && <p role="alert">{diff.error.message}</p>}<pre className="mt-3 max-h-[500px] overflow-auto rounded-lg bg-elevated p-4 text-xs">{diff.data?.diff ?? "Загрузка diff…"}</pre>{diff.data?.truncated && <p className="text-xs text-warning">Diff сокращён из-за размера</p>}</> : <p className="mt-3 text-sm text-muted">Candidate пока не готов</p>}</section>
    <section className="rounded-2xl border border-border p-5"><h2 className="font-semibold">Публикация</h2>{receipt ? <><p className="mt-2 text-sm">{receipt.state} · {receipt.branch_name}</p>{receipt.commit_sha && <p className="mt-2 break-all font-mono text-xs">Commit: {receipt.commit_sha}</p>}{receipt.reason_code && <p className="mt-2 text-warning">{codingReason(receipt.reason_code)}</p>}{receipt.merge_request_url ? <a className="mt-3 inline-block text-cyan" href={receipt.merge_request_url} target="_blank" rel="noreferrer">Открыть Draft MR ↗</a> : <p className="mt-2 text-sm text-warning">Draft MR ещё не создан; частичный receipt сохранён</p>}</> : <p className="mt-3 text-sm text-muted">{codingReason(run.reason_code) || "Публикация пока не выполнялась"}</p>}{report?.checks.some(c => c.status === "not_run") && <p className="mt-3 text-sm text-warning">Есть невыполненные проверки — ограничения указаны выше и в Draft MR</p>}</section>
  </div>;
}
