"use client";

import { useCallback, useMemo, useState } from "react";
import { Button, Card, CardContent, Input, DropdownSelect, Textarea } from "@rhetorica/ui";
import { ChevronDown, Code2, Plus, ShieldCheck, Trash2, Users } from "lucide-react";
import type { AgentProfile } from "@/features/agents/agentsApi";
import {
  CodingRequestError,
  codingCapabilityLabels,
  codingRequest,
  type CodingTeamCapability,
  type CodingTeamDetail,
  type CodingTeamMember,
  type CodingTeamRevision,
} from "./codingApi";

const DEFAULT_ASSISTANT = "__default_assistant__";
type SkillOption = { id: string; name: string; title?: string | null; version_digest?: string | null; status?: string };
type Draft = { teamId: string | null; expectedRevision: number; name: string; coordinator: string; coordinatorInstruction: string; members: CodingTeamMember[]; activate: boolean };

const emptyMember = (index: number): CodingTeamMember => ({ id: `member-${index}`, agent_profile_id: "", label: "", instruction: "", capability: index === 1 ? "candidate" : "review", skill_version_bindings: [] });
const newDraft = (): Draft => ({ teamId: null, expectedRevision: 0, name: "", coordinator: "", coordinatorInstruction: "", members: [emptyMember(1), emptyMember(2)], activate: true });

const avatarTones = ["bg-accent/10 text-accent", "bg-success/10 text-success", "bg-warning/10 text-warning", "bg-elevated text-secondary"];
function MemberAvatar({ profileId, review }: { profileId: string; review: boolean }) {
  const tone = Array.from(profileId).reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0) % avatarTones.length;
  const Icon = review ? ShieldCheck : Code2;
  return <span aria-hidden="true" className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${avatarTones[tone]}`}><Icon className="h-4 w-4" /></span>;
}

function nextMemberId(members: CodingTeamMember[]) {
  let index = members.length + 1;
  while (members.some(member => member.id === `member-${index}`)) index += 1;
  return index;
}

/** Client hints only: the server repeats every check and owns the decision. */
export function teamDraftWarnings(draft: Draft, agents: AgentProfile[]): Record<string, string[]> {
  const warnings: Record<string, string[]> = {};
  const coordinator = agents.find(agent => agent.id === draft.coordinator);
  draft.members.forEach(member => {
    const list: string[] = [];
    const agent = agents.find(item => item.id === member.agent_profile_id);
    if (!member.label.trim()) list.push("Укажите имя участника");
    if (!agent) list.push("Выберите профиль агента");
    else {
      if (!agent.is_active) list.push("Профиль отключён");
      if (!agent.allowed_gitlab_connector_names.length) list.push("Нет разрешённого GitLab-коннектора");
      else if (coordinator && !agent.allowed_gitlab_connector_names.some(name => coordinator.allowed_gitlab_connector_names.includes(name))) list.push("Нет общего GitLab-коннектора с координатором");
      if (member.agent_profile_id === draft.coordinator) list.push("Координатор не может быть участником");
    }
    if (draft.members.filter(item => item.agent_profile_id && item.agent_profile_id === member.agent_profile_id).length > 1) list.push("Профиль уже используется другим участником");
    if (list.length) warnings[member.id] = list;
  });
  return warnings;
}

export function CodingTeamsPanel({ initialTeams, agents, skills }: { initialTeams: CodingTeamDetail[]; agents: AgentProfile[]; skills: SkillOption[] }) {
  // The server page supplies the first list; the panel reloads only after a change.
  const [teams, setTeams] = useState<CodingTeamDetail[]>(initialTeams);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ message: string; memberId: string | null } | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await codingRequest<CodingTeamDetail[]>("coding-agent-teams");
      if (!Array.isArray(result)) throw new Error("Некорректный список команд coding-агентов");
      setTeams(result);
    }
    catch (caught) { setError({ message: caught instanceof Error ? caught.message : "Не удалось загрузить команды", memberId: null }); }
    finally { setLoading(false); }
  }, []);

  const coordinators = useMemo(() => [{ value: DEFAULT_ASSISTANT, label: "Ассистент по умолчанию" }, ...agents.map(agent => ({ value: agent.id, label: agent.title }))], [agents]);
  const agentTitle = (id: string) => id === DEFAULT_ASSISTANT ? "Ассистент по умолчанию" : agents.find(agent => agent.id === id)?.title ?? id;
  const warnings = draft ? teamDraftWarnings(draft, agents) : {};

  function edit(detail: CodingTeamDetail, revision: CodingTeamRevision) {
    setError(null); setMessage("");
    setDraft({ teamId: detail.team.id, expectedRevision: detail.team.latest_revision, name: detail.team.name, coordinator: revision.coordinator_agent_profile_id, coordinatorInstruction: revision.coordinator_instruction, members: revision.members.map(member => ({ ...member, instruction: member.instruction ?? "", skill_version_bindings: member.skill_version_bindings ?? [] })), activate: true });
  }
  function updateMember(id: string, patch: Partial<CodingTeamMember>) {
    setDraft(current => current && { ...current, members: current.members.map(member => member.id === id ? { ...member, ...patch } : member) });
  }
  function toggleSkill(member: CodingTeamMember, skill: SkillOption) {
    const bound = member.skill_version_bindings ?? [];
    const next = bound.some(binding => binding.skill_id === skill.id) ? bound.filter(binding => binding.skill_id !== skill.id) : [...bound, { skill_id: skill.id, version_digest: skill.version_digest ?? "" }];
    updateMember(member.id, { skill_version_bindings: next });
  }

  async function activate(teamId: string, revisionId: string) {
    setSaving(true); setError(null); setMessage("");
    try {
      await codingRequest(`coding-agent-teams/${teamId}/revisions/${revisionId}/activate`, "POST");
      setMessage("Ревизия активирована. Новые запуски сохранят её снимок");
      await load();
    } catch (caught) { setError({ message: caught instanceof Error ? caught.message : "Не удалось активировать", memberId: caught instanceof CodingRequestError ? caught.memberId : null }); }
    finally { setSaving(false); }
  }

  async function save() {
    if (!draft) return;
    setSaving(true); setError(null); setMessage("");
    try {
      const revision = await codingRequest<CodingTeamRevision>("coding-agent-teams", "POST", {
        team_id: draft.teamId,
        expected_revision: draft.expectedRevision,
        name: draft.name,
        coordinator_agent_profile_id: draft.coordinator,
        coordinator_instruction: draft.coordinatorInstruction,
        members: draft.members.map(member => ({ ...member, label: member.label.trim(), instruction: member.instruction ?? "" })),
      });
      if (draft.activate) await codingRequest(`coding-agent-teams/${revision.team_id}/revisions/${revision.id}/activate`, "POST");
      setMessage(draft.activate ? `Ревизия ${revision.revision} сохранена и активирована` : `Ревизия ${revision.revision} сохранена. Активируйте её для новых запусков`);
      setDraft(null);
      await load();
    } catch (caught) {
      setError({ message: caught instanceof Error ? caught.message : "Не удалось сохранить команду", memberId: caught instanceof CodingRequestError ? caught.memberId : null });
      if (caught instanceof CodingRequestError && caught.status === 409) await load();
    } finally { setSaving(false); }
  }

  const invalid = !draft || !draft.name.trim() || !draft.coordinator || !draft.members.length || Object.keys(warnings).length > 0;
  return (
    <Card className="min-w-0 rounded-lg bg-none shadow-none">
      <CardContent className="space-y-4 p-3 sm:p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-accent/20 bg-accent/10 text-accent">
              <Users className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold">Coding teams</h3>
                <span className="rounded-md bg-elevated px-1.5 py-0.5 text-xs tabular-nums text-secondary">{teams.length}</span>
              </div>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-secondary">
                Координатор распределяет работу между участниками. Каждый агент использует свои разрешения на GitLab, DWH и навыки; делегирование ограничено политикой координатора
              </p>
            </div>
          </div>
          {!draft && (
            <Button type="button" size="sm" className="shadow-none" onClick={() => { setError(null); setMessage(""); setDraft(newDraft()); }}>
              <Plus className="h-4 w-4" />Новая команда
            </Button>
          )}
        </div>
        {loading && <p role="status" className="text-sm text-muted">Загрузка команд…</p>}
        {error && <p role="alert" className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger">{error.message}</p>}
        {message && <p role="status" className="text-sm text-success">{message}</p>}

        {draft && (
          <section aria-label="Редактор команды" className="min-w-0 rounded-xl border border-accent/25 bg-page">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                <h4 className="text-sm font-semibold">{draft.teamId ? "Редактирование команды" : "Новая команда"}</h4>
              </div>
              <span className="text-xs text-muted">{draft.teamId ? `На основе ревизии ${draft.expectedRevision}` : "Настройте координатора и участников"}</span>
            </div>

            <div className="space-y-5 p-3 sm:p-4">
              <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                  <Input label="Название команды" aria-label="Название команды" value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} placeholder="Например, разработка dbt" />
                  <DropdownSelect
                    label="Координатор" aria-label="Координатор" className="rounded-lg shadow-none"
                    value={draft.coordinator} onValueChange={selected => setDraft({ ...draft, coordinator: selected })}
                    options={[{ value: "", label: "Выберите координатора" }, ...coordinators]}
                  />
                </div>
                <Textarea
                  label="Инструкция координатора" aria-label="Инструкция координатора" rows={5} className="min-h-[132px]"
                  value={draft.coordinatorInstruction} onChange={event => setDraft({ ...draft, coordinatorInstruction: event.target.value })}
                  placeholder="Например: сначала поручай разработку, затем независимую проверку"
                />
              </div>

              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
                  <div>
                    <h4 className="text-sm font-semibold">Участники <span className="ml-1 text-xs font-normal tabular-nums text-muted">{draft.members.length} / 24</span></h4>
                    <p className="mt-1 text-xs text-secondary">Профиль, задача и навыки каждого агента</p>
                  </div>
                  <Button type="button" variant="outline" size="sm" disabled={draft.members.length >= 24} onClick={() => setDraft({ ...draft, members: [...draft.members, emptyMember(nextMemberId(draft.members))] })}>
                    <Plus className="h-4 w-4" />Добавить участника
                  </Button>
                </div>

                <ol className="grid items-start gap-2 xl:grid-cols-2">
                  {draft.members.map((member, index) => {
                    const agent = agents.find(item => item.id === member.agent_profile_id);
                    const allowedSkills = skills.filter(skill => agent?.allowed_workspace_skill_ids.includes(skill.id) && skill.version_digest);
                    const failed = error?.memberId === member.id;

                    return (
                      <li key={member.id} aria-label={`Участник ${member.label || member.id}`} className={`min-w-0 rounded-lg border bg-surface ${failed ? "border-danger" : "border-border"}`}>
                        <details open={failed || undefined} className="group/member">
                          <summary aria-label={`Настроить участника ${member.id}`} className="flex cursor-pointer list-none items-center gap-2.5 rounded-lg p-3 outline-none hover:bg-elevated/50 focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden">
                            <MemberAvatar profileId={member.agent_profile_id || member.id} review={member.capability === "review"} />
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium" title={member.label || agent?.title}>{member.label || agent?.title || `Участник ${index + 1}`}</p>
                              <p className="truncate text-xs text-secondary" title={agent?.title}>{agent?.title || "Профиль не выбран"} · {codingCapabilityLabels[member.capability]}</p>
                            </div>
                            {warnings[member.id] && <span title={warnings[member.id].join(" · ")} className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning"><span className="sr-only">Требует настройки</span></span>}
                            {member.required_review && <ShieldCheck aria-label="Обязательная проверка" className="h-3.5 w-3.5 shrink-0 text-success" />}
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted transition group-open/member:rotate-180" />
                          </summary>
                        <div className="space-y-3 border-t border-border p-3">
                          <div className="grid gap-3 sm:grid-cols-2">
                            <Input label="Роль в команде" aria-label={`Имя участника ${member.id}`} value={member.label} onChange={event => updateMember(member.id, { label: event.target.value })} placeholder="Например, разработчик" />
                            <DropdownSelect label="Профиль агента" aria-label={`Профиль участника ${member.id}`} className="rounded-lg shadow-none" value={member.agent_profile_id} onValueChange={selected => updateMember(member.id, { agent_profile_id: selected, skill_version_bindings: [] })} options={[{ value: "", label: "Выберите профиль" }, ...agents.map(item => ({ value: item.id, label: item.title }))]} />
                            <div className="sm:col-span-2">
                              <DropdownSelect label="Возможность" aria-label={`Возможность участника ${member.id}`} className="rounded-lg shadow-none" value={member.capability} onValueChange={selected => updateMember(member.id, { capability: selected as CodingTeamCapability, required_review: false })} options={(Object.keys(codingCapabilityLabels) as CodingTeamCapability[]).map(value => ({ value, label: codingCapabilityLabels[value] }))} />
                            </div>
                          </div>
                          {member.capability === "review" && (
                            <label className="flex items-start gap-2 rounded-md border border-success/20 bg-success/5 px-2.5 py-2 text-xs leading-5 text-primary">
                              <input className="mt-0.5 h-4 w-4 shrink-0 accent-accent" type="checkbox" aria-label={`Обязательная проверка ${member.id}`} checked={member.required_review ?? false} onChange={event => updateMember(member.id, { required_review: event.target.checked })} />
                              Обязательная проверка перед публикацией
                            </label>
                          )}
                          <Textarea label="Инструкция поручения" aria-label={`Инструкция участника ${member.id}`} rows={2} className="min-h-20" value={member.instruction ?? ""} onChange={event => updateMember(member.id, { instruction: event.target.value })} placeholder="Что участник должен сделать или проверить" />
                          {agent && (
                            <fieldset className="min-w-0 space-y-2 border-t border-border pt-2">
                              <legend className="pr-2 text-xs font-medium text-secondary">Навыки участника</legend>
                              <p className="text-xs leading-5 text-muted">Без выбора доступны все активные навыки профиля. Выбранные навыки фиксируются в этой ревизии</p>
                              {allowedSkills.length ? (
                                <div className="flex flex-wrap gap-1.5">
                                  {allowedSkills.map(skill => {
                                    const selected = (member.skill_version_bindings ?? []).some(binding => binding.skill_id === skill.id);
                                    return (
                                      <label key={skill.id} className={`flex max-w-full cursor-pointer items-start gap-2 rounded-md border px-2 py-1.5 text-xs leading-5 transition ${selected ? "border-accent/40 bg-accent/10 text-primary" : "border-border text-secondary hover:bg-elevated"}`}>
                                        <input className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent" type="checkbox" checked={selected} onChange={() => toggleSkill(member, skill)} />
                                        <span className="min-w-0 [overflow-wrap:anywhere]">{skill.title || skill.name}</span>
                                      </label>
                                    );
                                  })}
                                </div>
                              ) : <p className="text-xs text-muted">Нет навыков с доступной версией для выбора. Участник получит активные навыки своего профиля</p>}
                            </fieldset>
                          )}
                          {warnings[member.id] && (
                            <ul role="status" className="space-y-1 rounded-md bg-warning/5 px-2.5 py-2 text-xs leading-5 text-warning">
                              {warnings[member.id].map(item => <li key={item}>{item}</li>)}
                            </ul>
                          )}
                          {failed && <p role="alert" className="text-xs text-danger">{error?.message}</p>}
                          <div className="flex items-center justify-between gap-2 border-t border-border pt-2">
                            <p className="text-[11px] text-muted">ID: <span className="font-mono">{member.id}</span></p>
                            <Button type="button" size="sm" variant="ghost" className="h-8 px-2 text-secondary hover:bg-danger/10 hover:text-danger" disabled={draft.members.length <= 1} onClick={() => setDraft({ ...draft, members: draft.members.filter(item => item.id !== member.id) })}>
                              <Trash2 className="h-3.5 w-3.5" />Убрать
                            </Button>
                          </div>
                        </div>
                        </details>
                      </li>
                    );
                  })}
                </ol>
              </div>
            </div>

            <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-b-xl border-t border-border bg-surface/95 px-3 py-3 backdrop-blur-xl sm:px-4">
              <label className="flex items-center gap-2 text-xs text-secondary">
                <input className="h-4 w-4 accent-accent" type="checkbox" checked={draft.activate} onChange={event => setDraft({ ...draft, activate: event.target.checked })} />
                Активировать после проверки
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={() => setDraft(null)}>Отмена</Button>
                <Button type="button" size="sm" className="shadow-none" disabled={invalid || saving} onClick={() => void save()}>
                  {saving ? "Сохраняю…" : draft.teamId ? "Сохранить новую ревизию" : "Создать команду"}
                </Button>
              </div>
            </div>
          </section>
        )}

        {!loading && !teams.length && !draft && <p className="rounded-lg border border-dashed border-border p-3 text-sm text-muted">Команд пока нет. Существующие пресеты Developer/QA перенесены в «Default coding team» при обновлении</p>}
        <ul className="space-y-2">
          {teams.map(detail => {
            const active = detail.revisions.find(revision => revision.id === detail.team.active_revision_id);
            return (
              <li key={detail.team.id} className="space-y-3 rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm font-semibold">{detail.team.name}</p>
                    <p className="mt-1 text-xs text-secondary">{active ? `Ревизия ${active.revision} · координатор ${agentTitle(active.coordinator_agent_profile_id)}` : "Нет активной ревизии"}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {active && <span className="rounded-md border border-success/20 bg-success/10 px-2 py-0.5 text-[11px] text-success">Активна</span>}
                    {detail.revisions[0] && <Button type="button" size="sm" variant="outline" disabled={saving || Boolean(draft)} onClick={() => edit(detail, detail.revisions[0])}>Изменить</Button>}
                  </div>
                </div>
                {active && (
                  <ul aria-label={`Участники ${detail.team.name}`} className="flex flex-wrap gap-2 text-xs">
                    {active.members.map(member => (
                      <li key={member.id} className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-page px-2.5 py-1.5 leading-5 [overflow-wrap:anywhere]">
                        <MemberAvatar profileId={member.agent_profile_id} review={member.capability === "review"} /><span className="font-medium text-primary">{member.label}</span><span className="text-secondary"> · {agentTitle(member.agent_profile_id)} · {codingCapabilityLabels[member.capability]}{member.required_review ? " · обязательная" : ""}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <details className="border-t border-border pt-2">
                  <summary className="cursor-pointer text-xs text-secondary transition hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">История ревизий ({detail.revisions.length})</summary>
                  <ol className="mt-2 divide-y divide-border">
                    {detail.revisions.map(revision => (
                      <li key={revision.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-xs">
                        <span>Ревизия {revision.revision} · {new Date(revision.created_at).toLocaleString("ru-RU")} · {revision.members.length} участн.</span>
                        {revision.is_active ? <span className="text-success">активна</span> : <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={() => void activate(detail.team.id, revision.id)}>Активировать</Button>}
                      </li>
                    ))}
                  </ol>
                </details>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
