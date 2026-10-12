"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@rhetorica/ui";
import Link from "next/link";
import { ArrowUpRight, Check, CheckCircle2, ChevronRight, Circle, CircleX, Clock3, GitBranch, LoaderCircle, Pause, RotateCcw, Square, X } from "lucide-react";
import { formatInMoscow } from "@/lib/dateTime";
import { WorkflowNavigation } from "./WorkflowNavigation";
import { ChatMessageComponent } from "@/app/components/chat-message";
import { ResultTable } from "@/app/components/result-table";
import { VisualizationChart, type QueryPreview, type VisualizationSpec } from "@/features/charts";
import type { FileArtifactRef } from "@/features/files/fileApi";
import { terminalStatuses, workflowRequest, workflowUrl, type WorkflowRun, type Version } from "./workflowApi";

export const statusLabels: Record<string, string> = { queued: "В очереди", pending: "Ожидает", running: "Выполняется", waiting: "Ждёт подтверждения", succeeded: "Завершён", failed: "Ошибка", skipped: "Пропущен", cancel_requested: "Отменяется", cancelled: "Отменён", rejected: "Отклонён" };

const statusStyles: Record<string, string> = {
  succeeded: "border-success-border/60 bg-success-subtle/40 text-success",
  failed: "border-danger-border/60 bg-danger-subtle/40 text-danger",
  waiting: "border-warning-border/60 bg-warning-subtle/40 text-warning",
  running: "border-cyan-border/60 bg-cyan-subtle/40 text-cyan",
};
function StatusIcon({ status }: { status: string }) {
  const Icon = status === "succeeded" ? CheckCircle2 : status === "failed" ? CircleX : status === "waiting" ? Pause : ["running", "cancel_requested"].includes(status) ? LoaderCircle : ["cancelled", "rejected"].includes(status) ? X : Circle;
  return <Icon size={16} aria-hidden="true" className={`shrink-0 ${["running", "cancel_requested"].includes(status) ? "animate-spin" : ""}`} />;
}
function durationLabel(milliseconds: unknown) {
  if (typeof milliseconds !== "number" || !Number.isFinite(milliseconds)) return null;
  return milliseconds < 1000 ? `${Math.round(milliseconds)} мс` : `${(milliseconds / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} с`;
}

function findFiles(value: unknown, found = new Map<string, FileArtifactRef>()): FileArtifactRef[] {
  if (Array.isArray(value)) value.forEach(item => findFiles(item, found));
  else if (value && typeof value === "object") {
    const item = value as Record<string, unknown>;
    if (typeof item.id === "string" && typeof item.filename === "string") found.set(item.id, item as FileArtifactRef);
    else Object.values(item).forEach(child => findFiles(child, found));
  }
  return [...found.values()];
}

export function RunResult({ value, run }: { value: Record<string, unknown>; run?: WorkflowRun }) {
  const text = typeof value.answer_markdown === "string" ? value.answer_markdown : typeof value.result === "string" ? value.result : Object.values(value).filter(item => typeof item === "string").join("\n\n");
  const table = Object.values(value).find(item => item && typeof item === "object" && "execution_id" in item) as { execution_id: string } | undefined;
  const preview = run?.steps.find(step => step.outputs?.execution_id === table?.execution_id)?.diagnostics.preview as QueryPreview | undefined;
  const visualization = value.visualization as VisualizationSpec | undefined;
  return <div className="space-y-3">
    {typeof value.run_id === "string" && "candidate" in value && <Link className="text-sm text-cyan" href={`/coding-runs/${encodeURIComponent(value.run_id)}`}>Открыть разработку: diff, QA и публикация</Link>}
    <ChatMessageComponent role="assistant" text={text} copyText={text} files={findFiles(value)} isComplete />
    {preview && <ResultTable columns={preview.columns} rows={preview.rows as (string | number | boolean | null)[][]} />}
    {visualization && preview && <VisualizationChart spec={visualization} preview={preview} />}
    <details className="text-xs text-muted"><summary className="cursor-pointer">Все значения результата · JSON</summary><pre className="max-h-72 overflow-auto whitespace-pre-wrap p-3">{JSON.stringify(value, null, 2)}</pre></details>
  </div>;
}

export function WorkflowRunView({ runId, workspace, compact = false }: { runId: string; workspace: string; compact?: boolean }) {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [acting, setActing] = useState(false);
  const key = ["scenario-run", runId, workspace];
  const query = useQuery({ queryKey: key, queryFn: () => workflowRequest<WorkflowRun>(`scenario-runs/${runId}?workspace_id=${encodeURIComponent(workspace)}`), refetchInterval: query => terminalStatuses.has(query.state.data?.status ?? "") ? false : 10000 });
  const versions = useQuery({ queryKey: ["scenario-versions", query.data?.scenario_id, workspace], enabled: Boolean(query.data && !query.data.is_test), queryFn: () => workflowRequest<Version[]>(`scenarios/${query.data!.scenario_id}/versions?workspace_id=${encodeURIComponent(workspace)}`) });
  const terminal = terminalStatuses.has(query.data?.status ?? "");
  useEffect(() => {
    if (terminal) return;
    const events = new EventSource(workflowUrl(`scenario-runs/${runId}/events?workspace_id=${encodeURIComponent(workspace)}`));
    const refresh = () => { void client.invalidateQueries({ queryKey: ["scenario-run", runId, workspace] }); };
    events.addEventListener("scenario", refresh);
    events.addEventListener("done", () => { refresh(); events.close(); });
    return () => events.close();
  }, [client, runId, workspace, terminal]);
  async function action(path: string, body?: unknown) {
    if (acting) return;
    try { setActing(true); setError(""); await workflowRequest(path, "POST", body); await client.invalidateQueries({ queryKey: key }); }
    catch (err) { setError(err instanceof Error ? err.message : "Ошибка операции"); }
    finally { setActing(false); }
  }
  if (!query.data) return <section className="space-y-8">{!compact && <WorkflowNavigation current="Выполнение сценария" />}<div className="rounded-2xl border border-border bg-surface p-8">{query.error ? <><p role="alert" className="text-danger">{query.error.message}</p><Button variant="ghost" className="mt-4" onClick={() => void query.refetch()}>Повторить загрузку</Button></> : <p role="status" className="flex items-center gap-3 text-muted"><LoaderCircle size={18} className="animate-spin" />Загружаю выполнение…</p>}</div></section>;
  const run = query.data;
  const definition = Array.isArray(versions.data) ? versions.data.find(item => item.version === run.version)?.workflow : undefined;
  const stepTitle = (id: string) => definition?.nodes.find(node => node.id === id)?.title ?? id;
  const order = new Map(definition?.nodes.map((node, index) => [node.id, index]));
  const steps = [...run.steps].sort((a, b) => (order.get(a.node_id) ?? Infinity) - (order.get(b.node_id) ?? Infinity));
  const step = steps.find(item => item.node_id === selected) ?? steps.find(item => ["waiting", "failed", "running"].includes(item.status)) ?? steps.at(-1);
  const completed = run.steps.filter(item => ["succeeded", "skipped"].includes(item.status)).length;
  const total = definition?.nodes.length ?? run.steps.length;
  const progress = total ? Math.min(100, Math.round(completed / total * 100)) : 0;
  const statusStyle = statusStyles[run.status] ?? "border-border-strong bg-hover/60 text-secondary";
  const guidance = run.status === "waiting" ? "Нужно ваше подтверждение. Выберите ожидающий шаг, чтобы продолжить" : run.status === "succeeded" ? "Все доступные результаты и файлы собраны ниже" : run.status === "failed" ? "Выполнение остановлено. Откройте шаг с ошибкой, чтобы увидеть подробности" : terminal ? "Выполнение остановлено. Данные завершённых шагов доступны ниже" : "Сценарий выполняется в фоне. Можно вернуться на главную — запуск продолжится";
  return <section className="space-y-6">
    {!compact && <WorkflowNavigation current="Выполнение сценария" />}
    <header className="rounded-2xl border border-border bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1"><p className="text-xs font-medium uppercase tracking-widest text-cyan">{run.is_test ? "Тест черновика" : "Выполнение сценария"}</p><h1 className="mt-2 break-words text-2xl font-semibold tracking-tight">{run.scenario_title}</h1><div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted"><span className="flex items-center gap-1.5"><GitBranch size={13} />Версия {run.version}</span><span className="flex items-center gap-1.5"><Clock3 size={13} />{formatInMoscow(run.created_at, { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }, "ru-RU")} MSK</span><details><summary className="cursor-pointer hover:text-primary">ID запуска</summary><code className="mt-2 block break-all text-[11px]">{run.id}</code></details></div></div>
        <div className="flex flex-wrap items-center gap-2"><span className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-medium ${statusStyle}`} role="status"><StatusIcon status={run.status} />{statusLabels[run.status] ?? run.status}</span>{!terminal && <Button size="sm" variant="ghost" disabled={acting || run.status === "cancel_requested"} onClick={() => void action(`scenario-runs/${runId}/cancel?workspace_id=${encodeURIComponent(workspace)}`)}><Square size={13} />Отменить</Button>}{compact && <Link className="inline-flex items-center gap-1 p-2 text-sm text-cyan" href={`/scenario-runs/${runId}`}>Подробнее <ArrowUpRight size={15} /></Link>}</div>
      </div>
      <div className="mt-6 border-t border-border pt-5"><div className="mb-2 flex justify-between gap-3 text-xs"><span className="text-muted">Прогресс выполнения</span><span className="text-secondary">{completed} из {total} шагов</span></div><div role="progressbar" aria-label="Прогресс выполнения" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} className="h-1.5 overflow-hidden rounded-full bg-hover"><div className={`h-full rounded-full transition-[width] duration-500 ${run.status === "failed" ? "bg-danger" : run.status === "succeeded" ? "bg-success" : "bg-cyan"}`} style={{ width: `${progress}%` }} /></div><p className="mt-3 text-xs leading-5 text-muted">{guidance}</p></div>
      {!compact && <div className="mt-4 flex flex-wrap items-center gap-4 text-sm"><Link href={`/scenarios/${run.scenario_id}/run`} className="inline-flex min-h-9 items-center gap-2 text-cyan hover:text-cyan"><RotateCcw size={14} />Новый запуск</Link></div>}
    </header>
    {(error || run.error || query.error) && <p role="alert" className="rounded-xl border border-danger-border/60 bg-danger-subtle/30 p-4 text-sm text-danger">{error || run.error || `Не удалось обновить данные: ${query.error?.message}`}</p>}
    {run.result && <section aria-label="Результат сценария" className="rounded-2xl border border-success-border/50 bg-surface p-5 sm:p-6"><h2 className="mb-4 flex items-center gap-2 font-semibold"><CheckCircle2 size={18} className="text-success" />Результат сценария</h2><RunResult value={run.result} run={run} /></section>}
    <div className="grid items-start gap-5 md:grid-cols-[260px_minmax(0,1fr)]">
      <nav aria-label="Шаги выполнения" className="rounded-2xl border border-border bg-surface p-3"><h2 className="px-2 pb-3 pt-1 text-xs font-semibold uppercase tracking-wider text-secondary">Шаги выполнения</h2><div className="flex gap-2 overflow-x-auto md:max-h-[65vh] md:flex-col md:overflow-y-auto">{steps.map((item, index) => <button key={item.node_id} onClick={() => setSelected(item.node_id)} aria-current={step?.node_id === item.node_id ? "step" : undefined} className={`flex min-w-[200px] items-center gap-3 rounded-xl border p-3 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan md:min-w-0 ${step?.node_id === item.node_id ? "border-cyan-border bg-cyan-subtle/30" : "border-transparent hover:bg-hover/60"}`}><span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border ${statusStyles[item.status] ?? "border-border-strong text-secondary"}`}>{item.status === "succeeded" ? <Check size={14} /> : ["running", "waiting", "failed"].includes(item.status) ? <StatusIcon status={item.status} /> : <span className="text-xs">{index + 1}</span>}</span><span className="min-w-0 flex-1"><span className="line-clamp-2 break-words font-medium" title={stepTitle(item.node_id)}>{stepTitle(item.node_id)}</span><span className="mt-1 block text-xs text-secondary">{statusLabels[item.status] ?? item.status}</span></span>{step?.node_id === item.node_id && <ChevronRight size={14} className="shrink-0 text-cyan" />}</button>)}</div>{!run.steps.length && <p className="px-2 py-4 text-sm text-secondary">{terminal ? "Нет выполненных шагов" : "Шаги появятся после начала выполнения"}</p>}</nav>
      {step ? <section aria-label="Подробности шага" className="min-w-0 space-y-4 rounded-2xl border border-border bg-surface p-5 sm:p-6">
        <header className="flex items-start justify-between gap-3 border-b border-border pb-4"><div className="min-w-0"><h2 className="break-words font-semibold">{stepTitle(step.node_id)}</h2><p className="mt-1 break-all font-mono text-[11px] text-secondary">{step.node_id}</p></div>{durationLabel(step.diagnostics.duration_ms) && <span className="shrink-0 rounded-lg bg-hover px-2 py-1 text-xs text-secondary">{durationLabel(step.diagnostics.duration_ms)}</span>}</header>
        {step.status === "waiting" && <div className="rounded-xl border border-warning-border/60 bg-warning-subtle/20 p-4"><p className="mb-2 flex items-center gap-2 text-sm font-medium text-warning"><Pause size={15} />Ожидает вашего решения</p><p className="whitespace-pre-wrap break-words text-sm leading-6 text-secondary">{String(step.diagnostics.message ?? "Подтвердите продолжение")}</p><div className="mt-4 flex flex-wrap gap-2"><Button disabled={acting} onClick={() => void action(`scenario-runs/${runId}/steps/${step.node_id}/approval`, { workspace_id: workspace, approved: true })}>Продолжить</Button><Button disabled={acting} variant="ghost" onClick={() => void action(`scenario-runs/${runId}/steps/${step.node_id}/approval`, { workspace_id: workspace, approved: false })}>Отклонить запуск</Button></div></div>}
        {step.error && <p role="alert" className="rounded-lg bg-danger-subtle/20 p-3 text-sm text-danger">{step.error}</p>}
        {typeof step.diagnostics.coding_run_id === "string" && <Link className="text-sm text-cyan" href={`/coding-runs/${encodeURIComponent(step.diagnostics.coding_run_id)}`}>Открыть разработку: diff, QA и публикация</Link>}
        {step.outputs ? <RunResult value={step.outputs} run={run} /> : !step.error && step.status !== "waiting" && <p className="py-4 text-sm text-secondary">{step.status === "running" ? "Шаг выполняется. Результат появится автоматически" : step.status === "skipped" ? "Этот шаг пропущен по условию сценария" : "У этого шага пока нет результата"}</p>}
        <details className="border-t border-border pt-4"><summary className="cursor-pointer text-xs font-medium text-secondary hover:text-primary">Технические подробности</summary><div className="mt-3 space-y-2">{[["Входы", step.inputs], ["Выходы", step.outputs], ["SQL, код, инструменты и токены", step.diagnostics]].map(([title, data]) => <details key={String(title)} className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-sm text-secondary">{String(title)}</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-secondary">{JSON.stringify(data, null, 2)}</pre></details>)}</div></details>
      </section> : <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-secondary"><Clock3 size={24} className="mx-auto mb-3 text-secondary" />{terminal ? "Подробности шагов недоступны для этого запуска" : "Ожидаем начало выполнения. Страница обновится автоматически"}</div>}
    </div>
  </section>;
}

export function ConversationScenarioRuns({ conversationId, workspace }: { conversationId: string | null; workspace: string }) {
  const query = useQuery({ queryKey: ["conversation-scenario-runs", conversationId, workspace], enabled: Boolean(conversationId), queryFn: () => workflowRequest<WorkflowRun[]>(`scenario-runs?workspace_id=${encodeURIComponent(workspace)}&conversation_id=${conversationId}`), refetchInterval: 4000 });
  if (!query.data?.length) return null;
  return <div className="max-h-60 space-y-2 overflow-auto border-b border-border px-6 py-3">{query.data.map(run => <Link key={run.id} href={`/scenario-runs/${run.id}`} className="flex items-center justify-between rounded-lg border border-border-strong bg-elevated p-3 text-sm"><span>◇ {run.scenario_title} · v{run.version}</span><span className="text-cyan">{statusLabels[run.status]} ↗</span></Link>)}</div>;
}
