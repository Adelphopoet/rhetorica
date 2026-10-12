"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore, type Dispatch, type SetStateAction } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Code2, Settings2 } from "lucide-react";
import { Button } from "@rhetorica/ui";
import { CodingResourceFields } from "./CodingResourceFields";
import { codingReason, codingRequest, missingCodingSettings, terminalCodingStatuses, type CodingResources, type CodingRun, type CodingSelection, type CodingTeamOption } from "./codingApi";

export type CodingChatPlan = { id: string; conversation_id: string; resources: CodingSelection; draft: { task: string; steps: string[]; developer_brief: string; qa_brief: string; acceptance_criteria: string[] }; run_id: string | null };
const emptySelection = (): CodingSelection => ({ coding_team_id: null, gitlab_connector_id: "", project_id: "", target_branch: "", environment_profile_id: "" });
const codingModeEvent = "rhetorica:coding-mode";
function subscribeCodingMode(notify: () => void) {
  window.addEventListener("storage", notify); window.addEventListener(codingModeEvent, notify);
  return () => { window.removeEventListener("storage", notify); window.removeEventListener(codingModeEvent, notify); };
}
const readCodingMode = () => localStorage.getItem("rhetorica-coding-mode") === "true";
const serverCodingMode = () => false;
function saveCodingMode(enabled: boolean) {
  localStorage.setItem("rhetorica-coding-mode", String(enabled));
  window.dispatchEvent(new Event(codingModeEvent));
}

export function useCodingChat(conversationId: string | null, rootAgentId: string | null, workspaceId = "default", teamId: string | null = null) {
  const preferredEnabled = useSyncExternalStore(subscribeCodingMode, readCodingMode, serverCodingMode);
  const [override, setOverride] = useState<{ key: string; value: CodingSelection } | null>(null);
  const useWorkspaceDefaults = preferredEnabled && !conversationId && !rootAgentId;
  const resources = useQuery({ queryKey: ["coding-resources", workspaceId, rootAgentId, useWorkspaceDefaults], queryFn: () => codingRequest<CodingResources>(`coding-capabilities?use_workspace_defaults=${useWorkspaceDefaults}${rootAgentId ? `&root_agent_id=${encodeURIComponent(rootAgentId)}` : ""}`) });
  const enabled = preferredEnabled && resources.data?.can_start_coding !== false;
  const selectionKey = JSON.stringify([workspaceId, rootAgentId, teamId, resources.data?.root_agent_id, resources.data?.launch_defaults]);
  // A team chosen in the agent selector is the coordinator's team for this chat.
  const defaults: CodingSelection = { ...emptySelection(), ...resources.data?.launch_defaults, ...(teamId ? { coding_team_id: teamId } : {}), root_agent_id: rootAgentId ?? resources.data?.root_agent_id ?? resources.data?.launch_defaults?.root_agent_id };
  const selection = override?.key === selectionKey ? override.value : defaults;
  const setSelection: Dispatch<SetStateAction<CodingSelection>> = next => setOverride(previous => ({ key: selectionKey, value: typeof next === "function" ? next(previous?.key === selectionKey ? previous.value : defaults) : next }));
  const plan = useQuery({ queryKey: ["coding-plan", workspaceId, conversationId], queryFn: () => codingRequest<CodingChatPlan | null>(`coding-plans/${conversationId}`), enabled: enabled && Boolean(conversationId), refetchInterval: 3000 });
  const runs = useQuery({ queryKey: ["chat-coding-runs", workspaceId, conversationId], queryFn: async () => {
    const result = await codingRequest<CodingRun[]>(`coding-runs?conversation_id=${conversationId}`);
    if (!Array.isArray(result)) throw new Error("Некорректный список задач разработки");
    return result;
  }, enabled: Boolean(conversationId), refetchInterval: query => query.state.data?.some(run => !terminalCodingStatuses.has(run.status)) ? 5000 : false });
  const missing = !resources.data ? [resources.error?.message ?? "Загрузка ресурсов"] : missingCodingSettings(selection, resources.data);
  const teamOptions = useQuery({ queryKey: ["coding-team-options", workspaceId], queryFn: async () => {
    const result = await codingRequest<CodingTeamOption[]>("coding-team-options");
    return Array.isArray(result) ? result.filter(team => team.available) : [];
  } });
  return { enabled, enable: () => saveCodingMode(true), disable: () => saveCodingMode(false), toggle: () => saveCodingMode(!enabled), resources, selection, setSelection, missing, plan, runs, teams: teamOptions.data ?? [] };
}

export function CodingModeControl({ coding, loading }: { coding: ReturnType<typeof useCodingChat>; loading: boolean }) {
  const [settingsOpen, setSettingsOpen] = useState(false), [hintOpen, setHintOpen] = useState(false);
  const hintId = useId(), panelId = useId();
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!settingsOpen) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Element | null;
      const selectTriggerId = target?.closest?.("[data-dropdown-select-trigger]")?.getAttribute("data-dropdown-select-trigger");
      const selectTrigger = selectTriggerId ? document.getElementById(selectTriggerId) : null;
      // Portalled select options still belong to the resource panel.
      if (!container.current?.contains(target) && !container.current?.contains(selectTrigger)) setSettingsOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setSettingsOpen(false); };
    document.addEventListener("pointerdown", dismiss); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [settingsOpen]);
  if (coding.resources.data?.can_start_coding !== true) return null;
  const hint = <>Coding: сначала обсудим задачу и план. Команда coding-агентов начнёт после твоего подтверждения.{coding.missing.length > 0 && <> Не настроено: {coding.missing.join(", ")}. Открой настройки рядом с Coding.</>}</>;
  return <div ref={container} className="relative flex items-center gap-1">
    <div className="relative" onMouseEnter={() => setHintOpen(true)} onMouseLeave={() => setHintOpen(false)}>
      <button type="button" aria-label="Режим кодинга" aria-pressed={coding.enabled} aria-describedby={hintOpen ? hintId : undefined} disabled={loading} onFocus={() => setHintOpen(true)} onBlur={() => setHintOpen(false)} onKeyDown={event => { if (event.key === "Escape") setHintOpen(false); }} onClick={coding.toggle} className={`inline-flex h-9 items-center gap-2 rounded-full border px-3 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan/50 ${coding.enabled ? "border-cyan/50 bg-cyan/10 text-cyan shadow-sm" : "border-border-strong bg-surface text-secondary hover:border-accent-border hover:text-primary"}`}><Code2 className="h-4 w-4" />Coding</button>
      {hintOpen && <div id={hintId} role="tooltip" className="absolute bottom-full right-0 z-40 mb-3 w-[min(330px,calc(100vw-32px))] rounded-xl border border-border-strong bg-surface p-3 text-xs leading-relaxed text-secondary shadow-xl">{hint}</div>}
    </div>
    {coding.enabled && <button type="button" aria-label="Настройки кодинга" aria-expanded={settingsOpen} aria-controls={panelId} onClick={() => { setSettingsOpen(!settingsOpen); setHintOpen(false); }} className="rounded-full p-2 text-muted transition hover:bg-hover hover:text-primary focus-visible:ring-2 focus-visible:ring-accent"><Settings2 className="h-4 w-4" /></button>}
    {coding.enabled && settingsOpen && <div id={panelId} role="dialog" aria-label="Ресурсы разработки" className="absolute bottom-full right-0 z-30 mb-3 max-h-[65vh] w-[min(460px,calc(100vw-32px))] space-y-4 overflow-auto rounded-2xl border border-border-strong bg-surface p-5 shadow-2xl"><div className="space-y-1"><p className="font-semibold">Ресурсы разработки</p><Link className="text-xs text-cyan hover:underline" href="/workspaces">Общие настройки coding для workspace →</Link></div>{coding.resources.data.defaults_unavailable_reason && <p role="status" className="text-xs text-warning">Пресет недоступен: {codingReason(coding.resources.data.defaults_unavailable_reason, coding.resources.data.defaults_unavailable_member)}</p>}<CodingResourceFields value={coding.selection} resources={coding.resources.data} onChange={coding.setSelection} />{coding.missing.length > 0 && <p role="status" className="rounded-lg bg-warning/10 p-3 text-xs text-warning">Не настроено: {coding.missing.join(", ")}</p>}<div className="flex justify-end"><Button type="button" onClick={() => setSettingsOpen(false)}>Готово</Button></div></div>}
  </div>;
}

export function CodingChatConfirmation({ coding, busy }: { coding: ReturnType<typeof useCodingChat>; busy: boolean }) {
  const [confirming, setConfirming] = useState(false), [failure, setFailure] = useState<{ planId: string | null; message: string } | null>(null);
  const plan = coding.plan.data;
  // A failure belongs to the plan it was raised for; a replaced or cleared plan drops it.
  const error = failure && failure.planId === (plan?.id ?? null) ? failure.message : "";
  if ((!plan || plan.run_id) && !error) return null;
  const confirm = async () => {
    if (!plan) return;
    setConfirming(true); setFailure(null);
    try { await codingRequest(`coding-plans/${plan.conversation_id}/${plan.id}/confirm`, "POST"); await Promise.all([coding.plan.refetch(), coding.runs.refetch()]); }
    catch (e) { setFailure({ planId: plan.id, message: e instanceof Error ? e.message : "Ошибка подтверждения" }); await coding.plan.refetch(); }
    finally { setConfirming(false); }
  };
  return <div className="flex flex-wrap items-center gap-2 px-4 py-1 text-sm">
    {plan && !plan.run_id && <Button size="sm" disabled={busy || confirming} title={`${plan.resources.project_id} → ${plan.resources.target_branch}`} onClick={confirm}><Code2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />{confirming ? "Запускаю…" : "Начать разработку"}</Button>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </div>;
}
