"use client";
import { Input, DropdownSelect } from "@rhetorica/ui";
import { codingCapabilityLabels, codingReason, type CodingResources, type CodingSelection } from "./codingApi";

export function CodingResourceFields({ value, resources, onChange, legacy = false }: { value: CodingSelection; resources: CodingResources; onChange: (value: CodingSelection) => void; legacy?: boolean }) {
  const teams = resources.teams ?? [];
  const team = teams.find(t => t.id === value.coding_team_id);
  const memberIds = legacy ? [value.developer_agent_id, value.qa_agent_id] : (team?.members ?? []).map(m => m.agent_profile_id);
  const specialist = resources.specialists.filter(a => memberIds.includes(a.id));
  const gitlab = resources.gitlab_connectors.filter(c => specialist.every(a => a.gitlab_connectors.includes(c.name)));
  const databases = resources.database_connectors.filter(c => specialist.every(a => a.database_connectors.includes(c.name)));
  const field = (key: keyof CodingSelection, title: string, options: { id: string; title: string }[]) => <DropdownSelect label={title} aria-label={title} value={(value[key] as string | null | undefined) ?? ""} onValueChange={selected => onChange({ ...value, [key]: selected || null })} options={[{ value: "", label: "Выберите…" }, ...options.map(item => ({ value: item.id, label: item.title }))]} />;
  return <div className="grid gap-3 sm:grid-cols-2">
    {legacy ? <>
      {!resources.specialists.length && <p role="status" className="text-xs text-warning sm:col-span-2">У выбранного координатора нет доступных специалистов. Выберите coding-координатора или настройте разрешения на вызов агентов.</p>}
      {field("developer_agent_id", "Developer", resources.specialists)}
      {field("qa_agent_id", "QA", resources.specialists.filter(a => a.id !== value.developer_agent_id))}
    </> : <div className="space-y-2 sm:col-span-2">
      {!teams.length && <p role="status" className="text-xs text-warning">У выбранного координатора нет активной команды coding-агентов. Администратор настраивает команды в разделе «Управление».</p>}
      <DropdownSelect label="Команда coding-агентов" aria-label="Команда coding-агентов" value={value.coding_team_id ?? ""} onValueChange={selected => onChange({ ...value, coding_team_id: selected || null, developer_agent_id: null, qa_agent_id: null })} options={[{ value: "", label: "Выберите…" }, ...teams.map(t => ({ value: t.id, label: t.available ? `${t.name}${t.revision ? ` · ревизия ${t.revision}` : ""}` : `${t.name} — недоступна` }))]} />
      {team && !team.available && <p role="status" className="text-xs text-warning">Команда недоступна: {codingReason(team.unavailable_reason, team.unavailable_member)}</p>}
      {team?.members && <ul aria-label="Участники команды" className="flex flex-wrap gap-2 text-xs">{team.members.map(member => <li key={member.id} className="rounded-full border border-border px-2 py-1"><span className="font-medium">{member.label}</span><span className="ml-1 text-muted">· {codingCapabilityLabels[member.capability]}</span></li>)}</ul>}
    </div>}
    {field("gitlab_connector_id", "GitLab-коннектор", gitlab.map(c => ({ ...c, title: c.name })))}
    {field("environment_profile_id", "Окружение", resources.environments.map(e => ({ id: e.id, title: `${e.id} · v${e.revision}` })))}
    <label className="space-y-1 text-xs text-muted sm:col-span-2">Проект GitLab<Input aria-label="Проект GitLab" value={value.project_id} placeholder="group/project" onChange={event => onChange({ ...value, project_id: event.target.value })} /></label>
    <label className="space-y-1 text-xs text-muted">Целевая ветка<Input aria-label="Целевая ветка" value={value.target_branch} onChange={event => onChange({ ...value, target_branch: event.target.value })} /></label>
    <label className="space-y-1 text-xs text-muted">Путь dbt-проекта<Input aria-label="Путь dbt-проекта" value={value.dbt_project_subdir ?? "."} onChange={event => onChange({ ...value, dbt_project_subdir: event.target.value })} /></label>
    {field("database_connector_id", "DWH для dbt-проверок", databases.map(c => ({ ...c, title: c.name })))}
  </div>;
}
