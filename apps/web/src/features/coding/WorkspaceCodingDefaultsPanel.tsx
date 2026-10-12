"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardContent, DropdownSelect } from "@rhetorica/ui";
import { CodingResourceFields } from "./CodingResourceFields";
import { codingRequest, missingCodingSettings, type CodingResources, type CodingSelection } from "./codingApi";

type Settings = { workspace_id: string; revision: number; resources: CodingSelection | null };
type View = { settings: Settings; coordinators: { id: string; title: string }[] };
const blank: CodingSelection = { root_agent_id: null, coding_team_id: null, gitlab_connector_id: "", project_id: "", target_branch: "main", environment_profile_id: "", dbt_project_subdir: "." };

export function WorkspaceCodingDefaultsPanel({ workspaceId, canManage }: { workspaceId: string; canManage: boolean }) {
  const client = useQueryClient();
  const view = useQuery({ queryKey: ["coding-workspace-settings", workspaceId], queryFn: () => codingRequest<View>("coding-workspace-settings") });
  const [selection, setSelection] = useState<CodingSelection>(blank);
  const [saving, setSaving] = useState(false), [message, setMessage] = useState("");
  useEffect(() => { if (view.data) setSelection(view.data.settings.resources ?? blank); }, [view.data]);
  const resources = useQuery({ queryKey: ["coding-resources", workspaceId, selection.root_agent_id], queryFn: () => codingRequest<CodingResources>(`coding-capabilities?root_agent_id=${encodeURIComponent(selection.root_agent_id!)}`), enabled: Boolean(selection.root_agent_id) });
  const missing = missingCodingSettings(selection, resources.data ? { ...resources.data, enabled: true } : undefined);
  async function save(clear = false) {
    if (!view.data) return;
    setSaving(true); setMessage("");
    try {
      await codingRequest<Settings>("coding-workspace-settings", "PUT", { expected_revision: view.data.settings.revision, resources: clear ? null : selection });
      await client.invalidateQueries({ queryKey: ["coding-workspace-settings", workspaceId] });
      await client.invalidateQueries({ queryKey: ["coding-resources", workspaceId] });
      setMessage("Настройки сохранены. Новые чаты используют этот пресет.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить настройки"); }
    finally { setSaving(false); }
  }
  return <Card><CardContent className="space-y-4 pt-6">
    <div><h2 className="font-semibold">Coding · настройки workspace</h2><p className="mt-1 text-sm text-secondary">Координатор, команда coding-агентов и проект подставляются в новых чатах при включении Coding. Каждый запуск сохраняет снимок активной ревизии команды. В чате можно изменить ресурсы для конкретной задачи.</p></div>
    {view.isLoading && <p role="status">Загрузка настроек…</p>}
    {view.error && <p role="alert">{view.error.message}</p>}
    {view.data && <fieldset disabled={!canManage || saving} className="space-y-3">
      <DropdownSelect label="Coding-координатор" aria-label="Coding-координатор" value={selection.root_agent_id ?? ""} onValueChange={selected => setSelection({ ...selection, root_agent_id: selected || null, coding_team_id: null, developer_agent_id: null, qa_agent_id: null })} options={[{ value: "", label: "Выберите…" }, ...view.data.coordinators.map(agent => ({ value: agent.id, label: agent.title }))]} />
      {resources.data && <CodingResourceFields value={selection} resources={resources.data} onChange={setSelection} />}
      {resources.error && <p role="alert">{resources.error.message}</p>}
      {canManage && <div className="flex gap-2"><Button type="button" disabled={!selection.root_agent_id || !resources.data || missing.length > 0 || saving} onClick={() => save()}>Сохранить настройки coding</Button><Button type="button" variant="ghost" disabled={!view.data.settings.resources || saving} onClick={() => save(true)}>Сбросить пресет</Button></div>}
    </fieldset>}
    {!canManage && <p className="text-xs text-muted">Пресет меняет владелец workspace или администратор.</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
  </CardContent></Card>;
}
