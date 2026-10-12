"use client";

import { useMemo, useState, type ChangeEvent } from "react";
import { Badge, Button, Input, Spinner, Tabs, TabsList, TabsTrigger } from "@rhetorica/ui";
import {
  Archive,
  BookOpenText,
  Pencil,
  Play,
  Plus,
  Search,
  Shapes,
  Sparkles,
} from "lucide-react";

import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";

type ScenarioCatalogProps = {
  scenarios: ScenarioDefinition[];
  loading: boolean;
  runningScenarioId: string | null;
  launchingScenarioId: string | null;
  authoringTarget: "create" | "edit" | null;
  authoringPending: boolean;
  runDisabled: boolean;
  saving: boolean;
  archivingScenarioId: string | null;
  onRunScenario: (
    scenarioId: string,
    inputs: Record<string, unknown>,
    renderedPrompt: string
  ) => void;
  onCreateScenario: () => void;
  onEditScenario: (scenario: ScenarioDefinition) => void;
  onArchiveScenario: (scenarioId: string) => void;
  className?: string;
};

function statusVariant(status: ScenarioDefinition["status"]): "info" | "success" | "warning" {
  if (status === "published") {
    return "success";
  }
  if (status === "draft") {
    return "warning";
  }
  return "info";
}

function schemaProperties(
  scenario: ScenarioDefinition
): Array<{ name: string; spec: Record<string, unknown> }> {
  const properties = scenario.input_schema.properties ?? {};
  return Object.entries(properties).map(([name, spec]) => ({
    name,
    spec: spec as Record<string, unknown>,
  }));
}

function buildInitialInputs(scenario: ScenarioDefinition): Record<string, unknown> {
  const initial: Record<string, unknown> = {};
  schemaProperties(scenario).forEach(({ name, spec }) => {
    if ("default" in spec) {
      initial[name] = spec.default;
      return;
    }
    const fieldType = spec.type;
    if (fieldType === "boolean") {
      initial[name] = false;
      return;
    }
    initial[name] = "";
  });
  return initial;
}

function renderScenarioPrompt(
  scenario: ScenarioDefinition,
  inputs: Record<string, unknown>
): string {
  return scenario.prompt_template.replace(/{{\s*([a-zA-Z0-9_]+)\s*}}/g, (_match, key: string) => {
    const value = inputs[key];
    if (value === undefined || value === null) {
      return "";
    }
    return String(value);
  });
}

function hasMissingRequiredInputs(
  scenario: ScenarioDefinition,
  inputs: Record<string, unknown>
): boolean {
  if (scenario.execution_kind === "workflow") return false;
  const required = new Set(scenario.input_schema.required ?? []);
  if (required.size === 0) {
    return false;
  }

  return schemaProperties(scenario).some(({ name, spec }) => {
    if (!required.has(name)) {
      return false;
    }

    const value = inputs[name];
    if (value === null || value === undefined) {
      return true;
    }

    if (spec.type === "string") {
      return String(value).trim().length === 0;
    }

    if (spec.type === "number" || spec.type === "integer") {
      return value === "";
    }

    return false;
  });
}

export function ScenarioCatalog({
  scenarios,
  loading,
  runningScenarioId,
  launchingScenarioId,
  authoringTarget,
  authoringPending,
  runDisabled,
  saving,
  archivingScenarioId,
  onRunScenario,
  onCreateScenario,
  onEditScenario,
  onArchiveScenario,
  className,
}: ScenarioCatalogProps) {
  const officialScenarios = useMemo(
    () => scenarios.filter((scenario) => scenario.scope === "official"),
    [scenarios]
  );
  const personalScenarios = useMemo(
    () => scenarios.filter((scenario) => scenario.scope === "personal"),
    [scenarios]
  );
  const [tab, setTab] = useState<"official" | "personal">("official");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [inputsByScenarioId, setInputsByScenarioId] = useState<Record<string, Record<string, unknown>>>({});

  const visibleScenarios = useMemo(() => {
    const source = tab === "official" ? officialScenarios : personalScenarios;
    const query = searchQuery.trim().toLowerCase();
    if (!query) {
      return source;
    }

    return source.filter((scenario) =>
      [scenario.title, scenario.summary, scenario.description, scenario.slug]
        .join(" ")
        .toLowerCase()
        .includes(query)
    );
  }, [officialScenarios, personalScenarios, searchQuery, tab]);

  const selectedScenario =
    visibleScenarios.find((scenario) => scenario.id === selectedScenarioId) ?? visibleScenarios[0] ?? null;

  const selectedInputs = selectedScenario
    ? inputsByScenarioId[selectedScenario.id] ?? buildInitialInputs(selectedScenario)
    : {};
  const runPrompt = selectedScenario ? renderScenarioPrompt(selectedScenario, selectedInputs) : "";
  const runBlockedByMissingInputs = selectedScenario
    ? hasMissingRequiredInputs(selectedScenario, selectedInputs)
    : true;
  const scenarioRunBusyId = launchingScenarioId ?? runningScenarioId;
  const createPending = authoringPending && authoringTarget === "create";
  const editPending = authoringPending && authoringTarget === "edit";

  return (
    <section
      className={[
        "flex h-full min-h-0 w-full flex-col rounded-[28px] border border-border-strong bg-[radial-gradient(circle_at_top_left,rgb(var(--color-accent)/0.14),transparent_26%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] p-4 md:p-5",
        className ?? "",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-accent-border bg-elevated text-accent shadow-[inset_0_1px_0_rgb(var(--color-highlight)/0.05)]">
              <Sparkles className="h-5 w-5" />
            </span>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-accent">
                Каталог сценариев
              </p>
              <h3 className="mt-1 text-base font-semibold text-primary">
                Быстрый запуск повторяющихся запросов
              </h3>
            </div>
          </div>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-secondary">
            Выбирай готовый сценарий, подставляй параметры и запускай его в основной чат без ручного
            копипаста.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="info" className="px-3 py-1">
            {officialScenarios.length} официальных
          </Badge>
          <Badge variant="info" className="px-3 py-1">
            {personalScenarios.length} личных
          </Badge>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <Tabs
          defaultValue="official"
          value={tab}
          onValueChange={(value) => setTab(value as "official" | "personal")}
        >
          <TabsList className="justify-start">
            <TabsTrigger value="official" className="gap-2">
              <BookOpenText className="h-4 w-4" />
              Официальные
            </TabsTrigger>
            <TabsTrigger value="personal" className="gap-2">
              <Shapes className="h-4 w-4" />
              Мои сценарии
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
          <div className="min-w-[220px] flex-1 sm:max-w-[320px]">
            <Input
              aria-label="Поиск сценариев"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Поиск сценариев"
              icon={<Search className="h-4 w-4" />}
            />
          </div>

          {tab === "personal" ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={onCreateScenario}
                isLoading={createPending}
                disabled={saving || editPending}
                className="gap-2 border border-border-strong bg-surface text-primary hover:bg-elevated"
              >
                {createPending ? null : <Plus className="h-4 w-4" />}
                {createPending ? "Открываю редактор…" : "Создать"}
              </Button>
              {selectedScenario?.scope === "personal" ? (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onEditScenario(selectedScenario)}
                    isLoading={editPending}
                    disabled={saving || createPending}
                    className="gap-2 border border-border-strong bg-surface text-primary hover:bg-elevated"
                  >
                    {editPending ? null : <Pencil className="h-4 w-4" />}
                    {editPending ? "Открываю редактор…" : "Изменить"}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onArchiveScenario(selectedScenario.id)}
                    isLoading={archivingScenarioId === selectedScenario.id}
                    disabled={createPending || editPending}
                    className="gap-2 border border-purple-border bg-purple-subtle text-danger hover:bg-purple-subtle"
                  >
                    {archivingScenarioId === selectedScenario.id ? null : <Archive className="h-4 w-4" />}
                    {archivingScenarioId === selectedScenario.id ? "Архивирование…" : "В архив"}
                  </Button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {loading ? (
        <div role="status" className="mt-4 flex items-center gap-2 rounded-2xl border border-dashed border-border-strong bg-surface/75 p-4 text-sm text-secondary">
          <Spinner size="md" className="text-secondary" />
          Загружаю сценарии…
        </div>
      ) : null}

      {!loading && visibleScenarios.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-border-strong bg-surface/75 p-4 text-sm text-secondary">
          {searchQuery.trim()
            ? "По фильтру ничего не найдено"
            : tab === "official"
              ? "Официальные сценарии появятся здесь после публикации"
              : "Создайте первый личный сценарий, чтобы ускорить повторяющиеся запросы"}
        </div>
      ) : null}

      {!loading && visibleScenarios.length > 0 ? (
        <div className="mt-4 grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(300px,0.88fr)_minmax(0,1.12fr)]">
          <div className="min-h-0 space-y-3 overflow-y-auto pr-1 pb-4">
            {visibleScenarios.map((scenario) => {
              const selected = selectedScenario?.id === scenario.id;
              const scenarioInputs = inputsByScenarioId[scenario.id] ?? buildInitialInputs(scenario);
              const cardRunBlockedByMissingInputs = hasMissingRequiredInputs(
                scenario,
                scenarioInputs
              );
              const cardRunPrompt = renderScenarioPrompt(scenario, scenarioInputs);
              const cardRunDisabled =
                scenario.status === "archived" ||
                (scenario.execution_kind === "workflow" && scenario.status !== "published") ||
                scenarioRunBusyId === scenario.id ||
                runDisabled ||
                cardRunBlockedByMissingInputs;

              return (
                <div
                  key={scenario.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedScenarioId(scenario.id)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return;
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedScenarioId(scenario.id);
                    }
                  }}
                  className={`w-full rounded-[22px] border p-4 text-left transition ${
                    selected
                      ? "border-accent bg-elevated shadow-[0_18px_48px_rgb(var(--color-shadow)/calc(0.35*var(--shadow-opacity-scale)))]"
                      : "border-border bg-surface hover:border-border-strong hover:bg-elevated"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-primary">{scenario.title}</p>
                        <Badge variant={statusVariant(scenario.status)}>{scenario.status}</Badge>
                      </div>
                      <p className="mt-2 text-sm leading-6 text-secondary">{scenario.summary}</p>
                    </div>
                    <span className="rounded-full border border-border-strong px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-secondary">
                      v{scenario.version}
                    </span>
                  </div>

                  <div className="mt-4 flex items-center justify-between gap-3 border-t border-border pt-3">
                    <span className="text-xs text-secondary">
                      {(scenario.input_schema.required ?? []).length > 0
                        ? `Обязательные параметры: ${(scenario.input_schema.required ?? []).length}`
                        : "Готов к запуску"}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="primary"
                      aria-label={scenario.execution_kind === "workflow" ? `К запуску ${scenario.title}` : `Запустить сценарий ${scenario.title}`}
                      isLoading={scenarioRunBusyId === scenario.id}
                      disabled={cardRunDisabled}
                      className="gap-2 rounded-xl px-4 shadow-[0_12px_30px_rgb(var(--color-accent)/0.28)]"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedScenarioId(scenario.id);
                        if (cardRunDisabled) {
                          return;
                        }
                        onRunScenario(scenario.id, scenarioInputs, cardRunPrompt);
                      }}
                    >
                      {scenarioRunBusyId === scenario.id ? null : <Play className="h-4 w-4" />}
                      {scenarioRunBusyId === scenario.id ? "Открываю запуск…" : scenario.execution_kind === "workflow" ? "К запуску" : "Запустить"}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>

          {selectedScenario ? (
            <div className="min-h-0 overflow-y-auto rounded-[24px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] p-4 pb-6 shadow-[0_18px_56px_rgb(var(--color-shadow)/calc(0.3*var(--shadow-opacity-scale)))]">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-lg font-semibold text-primary">{selectedScenario.title}</h3>
                    <Badge variant={statusVariant(selectedScenario.status)}>{selectedScenario.status}</Badge>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-secondary">{selectedScenario.description}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="info">v{selectedScenario.version}</Badge>
                  <Badge variant="info">
                    {selectedScenario.execution_kind === "workflow" ? "Сценарий" : selectedScenario.tool_policy.allow_run_sql ? "SQL включён" : "SQL выключен"}
                  </Badge>
                </div>
              </div>

              {selectedScenario.execution_kind !== "workflow" && schemaProperties(selectedScenario).length > 0 ? (
                <div className="mt-4 rounded-2xl border border-border bg-surface p-3 md:p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">
                      Параметры
                    </p>
                    <span className="text-xs text-secondary">
                      Обязательных: {(selectedScenario.input_schema.required ?? []).length}
                    </span>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    {schemaProperties(selectedScenario).map(({ name, spec }) => {
                      const value = selectedInputs[name];
                      const title = String(spec.title ?? name);
                      const fieldType = spec.type;
                      const isRequired = (selectedScenario.input_schema.required ?? []).includes(name);
                      const options = Array.isArray(spec.enum) ? spec.enum : null;

                      if (fieldType === "boolean") {
                        return (
                          <label
                            key={name}
                            className="flex min-h-10 items-center justify-between rounded-xl border border-border-strong bg-surface/75 px-4 py-2 text-sm text-primary"
                          >
                            <span>{title}</span>
                            <input
                              type="checkbox"
                              checked={Boolean(value)}
                              onChange={(event) =>
                                setInputsByScenarioId((current) => ({
                                  ...current,
                                  [selectedScenario.id]: {
                                    ...selectedInputs,
                                    [name]: event.target.checked,
                                  },
                                }))
                              }
                            />
                          </label>
                        );
                      }

                      if (options) {
                        return (
                          <label key={name} className="block text-sm">
                            <span className="mb-1.5 block font-medium text-secondary">
                              {title} {isRequired ? "*" : ""}
                            </span>
                            <select
                              value={String(value ?? "")}
                              onChange={(event) =>
                                setInputsByScenarioId((current) => ({
                                  ...current,
                                  [selectedScenario.id]: {
                                    ...selectedInputs,
                                    [name]: event.target.value,
                                  },
                                }))
                              }
                              className="flex h-10 w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                            >
                              <option value="">Выберите…</option>
                              {options.map((option) => (
                                <option key={String(option)} value={String(option)}>
                                  {String(option)}
                                </option>
                              ))}
                            </select>
                          </label>
                        );
                      }

                      return (
                        <Input
                          key={name}
                          label={`${title}${isRequired ? " *" : ""}`}
                          type={fieldType === "number" || fieldType === "integer" ? "number" : "text"}
                          value={String(value ?? "")}
                          onChange={(event: ChangeEvent<HTMLInputElement>) =>
                            setInputsByScenarioId((current) => ({
                              ...current,
                              [selectedScenario.id]: {
                                ...selectedInputs,
                                [name]:
                                  fieldType === "number" || fieldType === "integer"
                                    ? event.target.value === ""
                                      ? ""
                                      : Number(event.target.value)
                                    : event.target.value,
                              },
                            }))
                          }
                          placeholder={title}
                        />
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {selectedScenario.execution_kind === "workflow" ? <div className="mt-4 rounded-2xl border border-cyan-border/60 bg-cyan-subtle/20 p-4">
                <p className="text-sm font-medium text-cyan">{selectedScenario.status === "published" ? "Настройте запуск" : "Сначала опубликуйте сценарий"}</p>
                <p className="mt-2 text-sm leading-6 text-muted">{selectedScenario.status === "published" ? "На следующем экране вы выберете версию, заполните параметры и прикрепите файлы. После запуска здесь же откроются прогресс и результаты" : "Откройте редактор, проверьте шаги и опубликуйте готовую версию"}</p>
              </div> : <div className="mt-4 rounded-2xl border border-border-strong bg-surface/75 p-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary">
                  Предпросмотр сформированного запроса
                </p>
                <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[12px] leading-6 text-primary">
                  {runPrompt}
                </pre>
              </div>}

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <div className="text-xs text-secondary">
                  {selectedScenario.execution_kind === "workflow" ? "Параметры заполняются один раз — на странице запуска" : runBlockedByMissingInputs
                    ? "Заполните все обязательные поля, чтобы запустить сценарий"
                    : "Сценарий готов к запуску в основном чате"}
                </div>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  aria-label={selectedScenario.execution_kind === "workflow" ? "К запуску сценария" : "Запустить сценарий"}
                  isLoading={scenarioRunBusyId === selectedScenario.id}
                  disabled={
                    selectedScenario.status === "archived" ||
                    (selectedScenario.execution_kind === "workflow" && selectedScenario.status !== "published") ||
                    scenarioRunBusyId === selectedScenario.id ||
                    runDisabled ||
                    runBlockedByMissingInputs
                  }
                  onClick={() => onRunScenario(selectedScenario.id, selectedInputs, runPrompt)}
                  className="gap-2 rounded-xl px-5 shadow-[0_16px_34px_rgb(var(--color-accent)/0.34)]"
                >
                  {scenarioRunBusyId === selectedScenario.id ? null : <Play className="h-4 w-4" />}
                  {scenarioRunBusyId === selectedScenario.id ? "Открываю запуск…" : selectedScenario.execution_kind === "workflow" ? "К запуску" : "Запустить сценарий"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
