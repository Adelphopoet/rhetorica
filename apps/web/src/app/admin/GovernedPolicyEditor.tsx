"use client";

import * as React from "react";

import { Alert, Button, Input } from "@rhetorica/ui";
import { GitBranch, Plus, RefreshCw, Trash2 } from "lucide-react";

import type { AgentProfile } from "@/features/agents/agentsApi";
import type {
  GitLabConnectorItem,
  GitLabWriteScope,
  GovernedAgentPolicy,
} from "./types";

type PolicyTarget =
  | { kind: "agent"; agent: AgentProfile }
  | { kind: "default-assistant" };

type PolicyError = {
  status: number;
  message: string;
  currentRevision?: number;
};

function emptyScope(connectors: GitLabConnectorItem[]): GitLabWriteScope {
  return {
    connector_id: connectors[0]?.id ?? "",
    project_id: "",
    target_branches: ["main"],
    allowed_paths: ["src"],
  };
}

function parseList(value: string) {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

function explainError(payload: unknown, status: number): PolicyError {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return { status, message: detail };
    if (Array.isArray(detail)) {
      const first = detail[0];
      if (first && typeof first === "object" && "msg" in first && typeof first.msg === "string") {
        return { status, message: first.msg };
      }
    }
    if (detail && typeof detail === "object") {
      const conflict = detail as { reason_code?: unknown; current_revision?: unknown };
      if (conflict.reason_code === "policy_revision_conflict") {
        return {
          status,
          message: "Политика изменена в другой сессии. Ваш черновик сохранён",
          currentRevision:
            typeof conflict.current_revision === "number" ? conflict.current_revision : undefined,
        };
      }
    }
  }
  return { status, message: `Запрос политики завершился с кодом ${status}` };
}

export function GovernedPolicyEditor({
  workspaceId,
  target,
  agents,
  gitlabConnectors,
}: {
  workspaceId: string;
  target: PolicyTarget;
  agents: AgentProfile[];
  gitlabConnectors: GitLabConnectorItem[];
}) {
  const endpoint =
    target.kind === "agent"
      ? `/api/admin/agents/${target.agent.id}/governed-policy`
      : "/api/admin/default-assistant-policy";
  const [policy, setPolicy] = React.useState<GovernedAgentPolicy | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<PolicyError | null>(null);

  const activeAgents = React.useMemo(
    () =>
      agents.filter(
        (agent) =>
          agent.is_active &&
          agent.workspace_id === workspaceId &&
          (target.kind !== "agent" || agent.id !== target.agent.id),
      ),
    [agents, target, workspaceId],
  );
  const activeAgentIds = React.useMemo(
    () => new Set(activeAgents.map((agent) => agent.id)),
    [activeAgents],
  );
  const unavailableSubagents = React.useMemo(() => {
    if (!policy) return [];
    const agentsById = new Map(agents.map((agent) => [agent.id, agent]));
    return policy.allowed_subagent_ids
      .filter((agentId) => !activeAgentIds.has(agentId))
      .map((agentId) => ({
        id: agentId,
        title: agentsById.get(agentId)?.title,
      }));
  }, [activeAgentIds, agents, policy]);
  const directConnectors = React.useMemo(() => {
    const active = gitlabConnectors.filter(
      (connector) => connector.is_active && connector.workspace_id === workspaceId,
    );
    if (target.kind === "default-assistant") return active;
    const names = new Set(target.agent.allowed_gitlab_connector_names);
    return active.filter((connector) => names.has(connector.name));
  }, [gitlabConnectors, target, workspaceId]);

  const load = React.useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${endpoint}?${new URLSearchParams({ workspace_id: workspaceId })}`, {
        cache: "no-store",
        signal,
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) throw explainError(payload, response.status);
      setPolicy(payload as GovernedAgentPolicy);
    } catch (caught) {
      if (signal?.aborted) return;
      setError(
        typeof caught === "object" && caught !== null && "status" in caught
          ? (caught as PolicyError)
          : { status: 0, message: caught instanceof Error ? caught.message : "Не удалось загрузить политику" },
      );
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [endpoint, workspaceId]);

  React.useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function update(next: GovernedAgentPolicy) {
    setPolicy(next);
    setError(null);
  }

  function removeSubagentReference(subagentId: string) {
    if (!policy) return;
    update({
      ...policy,
      allowed_subagent_ids: policy.allowed_subagent_ids.filter((id) => id !== subagentId),
      delegation_grants: policy.delegation_grants.filter(
        (grant) => grant.subagent_id !== subagentId,
      ),
    });
  }

  async function save() {
    if (!policy) return;
    if (
      !Number.isInteger(policy.max_iterations) ||
      policy.max_iterations < 1 ||
      !Number.isInteger(policy.max_runtime_seconds) ||
      policy.max_runtime_seconds < 1
    ) {
      setError({
        status: 422,
        message: "Лимиты итераций и времени должны быть целыми положительными числами",
      });
      return;
    }
    setSaving(true);
    setError(null);
    const body = {
      revision: policy.revision,
      gitlab_write_scopes: policy.gitlab_write_scopes,
      allowed_subagent_ids: policy.allowed_subagent_ids,
      delegation_grants: policy.delegation_grants,
      max_iterations: policy.max_iterations,
      max_runtime_seconds: policy.max_runtime_seconds,
    };
    try {
      const response = await fetch(`${endpoint}?${new URLSearchParams({ workspace_id: workspaceId })}`, {
        method: "PUT",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) throw explainError(payload, response.status);
      setPolicy(payload as GovernedAgentPolicy);
    } catch (caught) {
      setError(
        typeof caught === "object" && caught !== null && "status" in caught
          ? (caught as PolicyError)
          : { status: 0, message: caught instanceof Error ? caught.message : "Не удалось сохранить политику" },
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="text-sm text-secondary">Загрузка политики…</p>;
  if (!policy) {
    return (
      <div className="space-y-3">
        <Alert variant="danger" title="Политика недоступна" description={error?.message ?? "Неизвестная ошибка"} />
        <Button type="button" variant="outline" onClick={() => void load()}>
          <RefreshCw className="h-4 w-4" /> Повторить
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-border-strong bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium text-primary">Политика выполнения кода</p>
          <p className="mt-1 text-sm text-secondary">
            Версия {policy.revision}. При пустом списке областей записи публикация запрещена
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => void load()} disabled={saving}>
          <RefreshCw className="h-4 w-4" /> Обновить
        </Button>
      </div>

      {error ? (
        <Alert
          variant="danger"
          title={error.status === 409 ? "Политика изменена другим пользователем" : "Не удалось сохранить политику"}
          description={`${error.message}${error.currentRevision ? ` Текущая версия: ${error.currentRevision}` : ""}`}
        />
      ) : null}

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm font-medium text-primary">
          Максимум итераций
          <Input
            className="mt-2"
            type="number"
            min={1}
            value={policy.max_iterations}
            onChange={(event) => update({ ...policy, max_iterations: Number(event.target.value) })}
          />
        </label>
        <label className="text-sm font-medium text-primary">
          Время выполнения, с
          <Input
            className="mt-2"
            type="number"
            min={1}
            value={policy.max_runtime_seconds}
            onChange={(event) => update({ ...policy, max_runtime_seconds: Number(event.target.value) })}
          />
        </label>
      </div>
      <p className="text-xs text-secondary">
        Это верхние границы для агента. В одном запуске основной агент, разработчик, QA и разрешённые
        субагенты делят лимиты основного агента. Среда выполнения и профиль окружения
        могут только уменьшить итоговые лимиты
      </p>

      <ScopeList
        title="Области прямой публикации в GitLab"
        scopes={policy.gitlab_write_scopes}
        connectors={directConnectors}
        onChange={(gitlab_write_scopes) => update({ ...policy, gitlab_write_scopes })}
      />

      <div>
        <p className="text-sm font-medium text-primary">Разрешённые субагенты</p>
        <p className="mt-1 text-xs text-secondary">
          Выбор разрешает только делегирование. Для публикации нужно отдельное разрешение ниже
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {activeAgents.map((agent) => {
            const selected = policy.allowed_subagent_ids.includes(agent.id);
            return (
              <button
                key={agent.id}
                type="button"
                onClick={() => {
                  if (selected) {
                    removeSubagentReference(agent.id);
                    return;
                  }
                  update({
                    ...policy,
                    allowed_subagent_ids: [...policy.allowed_subagent_ids, agent.id],
                  });
                }}
                className={`rounded-full border px-3 py-2 text-sm ${
                  selected
                    ? "border-accent/50 bg-accent/[0.18] text-primary"
                    : "border-border-strong text-secondary"
                }`}
              >
                {agent.title}
              </button>
            );
          })}
          {activeAgents.length === 0 ? <span className="text-sm text-secondary">Нет подходящих активных агентов</span> : null}
        </div>
        {unavailableSubagents.length > 0 ? (
          <div className="mt-3 rounded-xl border border-warning/40 bg-warning/[0.08] p-3">
            <p className="text-sm font-medium text-primary">Недоступные субагенты</p>
            <p className="mt-1 text-xs text-secondary">
              Эти агенты неактивны, удалены или недоступны в рабочем пространстве.
              Удалите ссылки на них перед сохранением политики
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {unavailableSubagents.map((subagent) => {
                const label = subagent.title ?? subagent.id;
                return (
                  <span
                    key={subagent.id}
                    className="inline-flex items-center gap-2 rounded-full border border-warning/40 px-3 py-2 text-sm text-primary"
                  >
                    Недоступен: {label}
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 text-xs text-secondary hover:text-primary"
                      aria-label={`Удалить недоступного субагента ${label}`}
                      onClick={() => removeSubagentReference(subagent.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Удалить
                    </button>
                  </span>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>

      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-primary">Разрешения на делегирование</p>
            <p className="mt-1 text-xs text-secondary">Каждое разрешение ограничено политиками основного агента и субагента</p>
          </div>
          <select
            aria-label="Добавить разрешение на делегирование"
            className="h-10 rounded-lg border border-control-border bg-surface px-3 text-sm text-primary"
            value=""
            onChange={(event) => {
              const subagentId = event.target.value;
              if (!subagentId) return;
              update({
                ...policy,
                delegation_grants: [
                  ...policy.delegation_grants,
                  { subagent_id: subagentId, gitlab_write_scopes: [] },
                ],
              });
            }}
          >
            <option value="">Добавить разрешение…</option>
            {activeAgents
              .filter(
                (agent) =>
                  policy.allowed_subagent_ids.includes(agent.id) &&
                  !policy.delegation_grants.some((grant) => grant.subagent_id === agent.id),
              )
              .map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}
          </select>
        </div>
        {policy.delegation_grants.map((grant, grantIndex) => {
          const child = activeAgents.find((agent) => agent.id === grant.subagent_id);
          const childNames = new Set(child?.allowed_gitlab_connector_names ?? []);
          const grantConnectors = directConnectors.filter((connector) => childNames.has(connector.name));
          return (
            <div key={grant.subagent_id} className="rounded-xl border border-border p-3">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm font-medium text-primary">{child?.title ?? grant.subagent_id}</p>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => update({
                    ...policy,
                    delegation_grants: policy.delegation_grants.filter((_, index) => index !== grantIndex),
                  })}
                >
                  <Trash2 className="h-4 w-4" /> Удалить разрешение
                </Button>
              </div>
              <ScopeList
                title="Области делегированной публикации"
                scopes={grant.gitlab_write_scopes}
                connectors={grantConnectors}
                onChange={(gitlab_write_scopes) => update({
                  ...policy,
                  delegation_grants: policy.delegation_grants.map((item, index) =>
                    index === grantIndex ? { ...item, gitlab_write_scopes } : item,
                  ),
                })}
              />
            </div>
          );
        })}
      </div>

      <div className="flex justify-end">
        <Button type="button" onClick={() => void save()} isLoading={saving}>
          Сохранить политику
        </Button>
      </div>
    </div>
  );
}

function ScopeList({
  title,
  scopes,
  connectors,
  onChange,
}: {
  title: string;
  scopes: GitLabWriteScope[];
  connectors: GitLabConnectorItem[];
  onChange: (scopes: GitLabWriteScope[]) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm font-medium text-primary">
          <GitBranch className="h-4 w-4" /> {title}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={connectors.length === 0}
          onClick={() => onChange([...scopes, emptyScope(connectors)])}
        >
          <Plus className="h-4 w-4" /> Добавить область
        </Button>
      </div>
      {connectors.length === 0 ? (
        <p className="text-sm text-secondary">Нет активного подключения GitLab, разрешённого обеим сторонам</p>
      ) : null}
      {scopes.map((scope, index) => (
        <div key={`${index}:${scope.connector_id}`} className="grid gap-3 rounded-xl border border-border p-3 md:grid-cols-2">
          <label className="text-xs text-secondary">
            Подключение
            <select
              className="mt-1 h-10 w-full rounded-lg border border-control-border bg-surface px-3 text-sm text-primary"
              value={scope.connector_id}
              onChange={(event) => onChange(scopes.map((item, itemIndex) =>
                itemIndex === index ? { ...item, connector_id: event.target.value } : item,
              ))}
            >
              {connectors.map((connector) => <option key={connector.id} value={connector.id}>{connector.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-secondary">
            Путь или ID проекта
            <Input className="mt-1" value={scope.project_id} onChange={(event) => onChange(scopes.map((item, itemIndex) =>
              itemIndex === index ? { ...item, project_id: event.target.value } : item,
            ))} />
          </label>
          <label className="text-xs text-secondary">
            Ветки через запятую
            <Input className="mt-1" value={scope.target_branches.join(", ")} onChange={(event) => onChange(scopes.map((item, itemIndex) =>
              itemIndex === index ? { ...item, target_branches: parseList(event.target.value) } : item,
            ))} />
          </label>
          <label className="text-xs text-secondary">
            Разрешённые пути через запятую
            <Input className="mt-1" value={scope.allowed_paths.join(", ")} onChange={(event) => onChange(scopes.map((item, itemIndex) =>
              itemIndex === index ? { ...item, allowed_paths: parseList(event.target.value) } : item,
            ))} />
          </label>
          <div className="md:col-span-2 flex justify-end">
            <Button type="button" variant="ghost" size="sm" onClick={() => onChange(scopes.filter((_, itemIndex) => itemIndex !== index))}>
              <Trash2 className="h-4 w-4" /> Удалить область
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
