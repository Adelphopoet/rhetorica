"use client";

import { useMemo, useState, type ChangeEvent } from "react";
import { Badge, Button, Input, Tabs, TabsList, TabsTrigger } from "@rhetorica/ui";
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

  return (
    <section
      className={[
        "flex h-full min-h-0 w-full flex-col rounded-[28px] border border-[#20304b] bg-[radial-gradient(circle_at_top_left,rgba(91,168,255,0.14),transparent_26%),linear-gradient(180deg,rgba(10,18,31,0.98),rgba(8,13,24,0.98))] p-4 md:p-5",
        className ?? "",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-[#335784] bg-[#0d1a2d] text-[#9fd0ff] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
              <Sparkles className="h-5 w-5" />
            </span>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-[#8ebcff]">
                Scenario catalog
              </p>
              <h3 className="mt-1 text-base font-semibold text-[#eef4ff]">
                Quick launch for repeatable asks
              </h3>
            </div>
          </div>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-[#9eb1cf]">
            Выбирай готовый сценарий, подставляй параметры и запускай его в основной чат без ручного
            копипаста.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="info" className="px-3 py-1">
            {officialScenarios.length} official
          </Badge>
          <Badge variant="info" className="px-3 py-1">
            {personalScenarios.length} personal
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
              Official
            </TabsTrigger>
            <TabsTrigger value="personal" className="gap-2">
              <Shapes className="h-4 w-4" />
              My scenarios
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
          <div className="min-w-[220px] flex-1 sm:max-w-[320px]">
            <Input
              aria-label="Search scenarios"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search scenarios"
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
                disabled={saving}
                className="gap-2 border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]"
              >
                <Plus className="h-4 w-4" />
                New
              </Button>
              {selectedScenario?.scope === "personal" ? (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onEditScenario(selectedScenario)}
                    disabled={saving}
                    className="gap-2 border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]"
                  >
                    <Pencil className="h-4 w-4" />
                    Edit
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onArchiveScenario(selectedScenario.id)}
                    disabled={archivingScenarioId === selectedScenario.id}
                    className="gap-2 border border-[#3a2630] bg-[#170d13] text-[#ffb2bb] hover:bg-[#24121a]"
                  >
                    <Archive className="h-4 w-4" />
                    {archivingScenarioId === selectedScenario.id ? "Archiving..." : "Archive"}
                  </Button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {loading ? (
        <div className="mt-4 rounded-2xl border border-dashed border-[#22324d] bg-[#09111f]/75 p-4 text-sm text-[#8ea3c5]">
          Loading scenarios...
        </div>
      ) : null}

      {!loading && visibleScenarios.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-[#22324d] bg-[#09111f]/75 p-4 text-sm text-[#8ea3c5]">
          {searchQuery.trim()
            ? "Nothing matched that filter."
            : tab === "official"
              ? "Official scenarios will show up here once they are published."
              : "Create your first personal scenario to speed up recurring asks."}
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
                runningScenarioId === scenario.id ||
                cardRunBlockedByMissingInputs;

              return (
                <div
                  key={scenario.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedScenarioId(scenario.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedScenarioId(scenario.id);
                    }
                  }}
                  className={`w-full rounded-[22px] border p-4 text-left transition ${
                    selected
                      ? "border-[#5da8ff] bg-[#10203a] shadow-[0_18px_48px_rgba(15,42,82,0.35)]"
                      : "border-[#1d2b43] bg-[#0a1322] hover:border-[#355078] hover:bg-[#0f192b]"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-[#eef4ff]">{scenario.title}</p>
                        <Badge variant={statusVariant(scenario.status)}>{scenario.status}</Badge>
                      </div>
                      <p className="mt-2 text-sm leading-6 text-[#9eb1cf]">{scenario.summary}</p>
                    </div>
                    <span className="rounded-full border border-[#22324d] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-[#86a0c7]">
                      v{scenario.version}
                    </span>
                  </div>

                  <div className="mt-4 flex items-center justify-between gap-3 border-t border-[#1d2b43] pt-3">
                    <span className="text-xs text-[#7f93b4]">
                      {(scenario.input_schema.required ?? []).length > 0
                        ? `${(scenario.input_schema.required ?? []).length} required input${
                            (scenario.input_schema.required ?? []).length > 1 ? "s" : ""
                          }`
                        : "Ready to run"}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="primary"
                      aria-label={`Run ${scenario.title}`}
                      disabled={cardRunDisabled}
                      className="gap-2 rounded-xl px-4 shadow-[0_12px_30px_rgba(37,99,235,0.28)]"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedScenarioId(scenario.id);
                        if (cardRunDisabled) {
                          return;
                        }
                        onRunScenario(scenario.id, scenarioInputs, cardRunPrompt);
                      }}
                    >
                      <Play className="h-4 w-4" />
                      {runningScenarioId === scenario.id ? "Running..." : "Run now"}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>

          {selectedScenario ? (
            <div className="min-h-0 overflow-y-auto rounded-[24px] border border-[#1d2b43] bg-[linear-gradient(180deg,rgba(11,18,32,0.98),rgba(8,13,24,0.98))] p-4 pb-6 shadow-[0_18px_56px_rgba(0,0,0,0.3)]">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-lg font-semibold text-[#eef4ff]">{selectedScenario.title}</h3>
                    <Badge variant={statusVariant(selectedScenario.status)}>{selectedScenario.status}</Badge>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-[#9eb1cf]">{selectedScenario.description}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="info">v{selectedScenario.version}</Badge>
                  <Badge variant="info">
                    {selectedScenario.tool_policy.allow_run_sql ? "SQL on" : "SQL off"}
                  </Badge>
                </div>
              </div>

              {schemaProperties(selectedScenario).length > 0 ? (
                <div className="mt-4 rounded-2xl border border-[#1f2a3f] bg-[#07101d] p-3 md:p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#687d9e]">
                      Parameters
                    </p>
                    <span className="text-xs text-[#7f93b4]">
                      {(selectedScenario.input_schema.required ?? []).length} required
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
                            className="flex min-h-10 items-center justify-between rounded-xl border border-[#22324d] bg-[#09111f]/75 px-4 py-2 text-sm text-[#d7e6ff]"
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
                            <span className="mb-1.5 block font-medium text-[#a9b8d4]">
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
                              className="flex h-10 w-full rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 text-sm text-[#edf3ff] focus:outline-none focus:ring-2 focus:ring-[#5da8ff]"
                            >
                              <option value="">Select...</option>
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

              <div className="mt-4 rounded-2xl border border-[#22324d] bg-[#09111f]/75 p-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#86a0c7]">
                  Rendered prompt preview
                </p>
                <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[12px] leading-6 text-[#d8e6ff]">
                  {runPrompt}
                </pre>
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <div className="text-xs text-[#7f93b4]">
                  {runBlockedByMissingInputs
                    ? "Fill all required fields to run this scenario."
                    : "Scenario is ready to launch into the main chat flow."}
                </div>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  aria-label="Run scenario"
                  disabled={
                    selectedScenario.status === "archived" ||
                    runningScenarioId === selectedScenario.id ||
                    runBlockedByMissingInputs
                  }
                  onClick={() => onRunScenario(selectedScenario.id, selectedInputs, runPrompt)}
                  className="gap-2 rounded-xl px-5 shadow-[0_16px_34px_rgba(37,99,235,0.34)]"
                >
                  <Play className="h-4 w-4" />
                  {runningScenarioId === selectedScenario.id ? "Running..." : "Run scenario"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
