"use client";

import { useEffect, useState } from "react";
import { Button, Input, Textarea } from "@rhetorica/ui";
import { X } from "lucide-react";

import type { SaveScenarioPayload, ScenarioDefinition } from "@/features/scenarios/scenariosApi";

type ScenarioEditorModalProps = {
  isOpen: boolean;
  workspaceName: string;
  scope: "official" | "personal";
  saving: boolean;
  scenario?: ScenarioDefinition | null;
  onClose: () => void;
  onSubmit: (payload: SaveScenarioPayload, scenarioId?: string) => void;
};

type EditorState = {
  title: string;
  summary: string;
  description: string;
  promptTemplate: string;
  parameterNames: string;
  status: "draft" | "published";
  allowRunSql: boolean;
};

function buildEditorState(scenario?: ScenarioDefinition | null): EditorState {
  if (!scenario) {
    return {
      title: "",
      summary: "",
      description: "",
      promptTemplate: "",
      parameterNames: "",
      status: "published",
      allowRunSql: true,
    };
  }

  return {
    title: scenario.title,
    summary: scenario.summary,
    description: scenario.description,
    promptTemplate: scenario.prompt_template,
    parameterNames: Object.keys(scenario.input_schema.properties ?? {}).join(", "),
    status: scenario.status === "archived" ? "draft" : scenario.status,
    allowRunSql: scenario.tool_policy.allow_run_sql,
  };
}

function buildInputSchema(parameterNames: string): Record<string, unknown> {
  const fields = parameterNames
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    type: "object",
    properties: Object.fromEntries(
      fields.map((field) => [
        field,
        {
          type: "string",
          title: field
            .split("_")
            .map((chunk) => chunk.charAt(0).toUpperCase() + chunk.slice(1))
            .join(" "),
        },
      ])
    ),
    required: fields,
  };
}

export function ScenarioEditorModal({
  isOpen,
  workspaceName,
  scope,
  saving,
  scenario,
  onClose,
  onSubmit,
}: ScenarioEditorModalProps) {
  const [editor, setEditor] = useState<EditorState>(() => buildEditorState(scenario));

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- The modal stays mounted between opens; reset the draft when editing a different scenario.
    setEditor(buildEditorState(scenario));
  }, [isOpen, scenario]);

  if (!isOpen) {
    return null;
  }

  const submitEditor = () => {
    const payload: SaveScenarioPayload = {
      workspace_id: workspaceName,
      scope,
      title: editor.title.trim(),
      summary: editor.summary.trim(),
      description: editor.description.trim(),
      prompt_template: editor.promptTemplate.trim(),
      input_schema: buildInputSchema(editor.parameterNames),
      tool_policy: {
        allow_run_sql: editor.allowRunSql,
        sql_guard_required: true,
        allowed_tools: [],
        post_answer_checklist: [],
      },
      default_connector_bindings: {
        run_sql: editor.allowRunSql,
      },
      status: editor.status,
    };
    onSubmit(payload, scenario?.id);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#02050c]/72 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-[28px] border border-[#1d2940] bg-[linear-gradient(180deg,rgba(12,18,31,0.98),rgba(8,12,22,0.99))] p-6 shadow-[0_30px_90px_rgba(0,0,0,0.48)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6e84a8]">
              {scope === "official" ? "Admin official scenario" : "Personal scenario"}
            </p>
            <h3 className="mt-1 text-xl font-semibold text-[#eef4ff]">
              {scenario ? "Edit scenario" : "Create scenario"}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-[#23324e] p-2 text-[#a6b8d6] transition hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-5 grid gap-3">
          <Input
            label="Title"
            value={editor.title}
            onChange={(event) => setEditor((current) => ({ ...current, title: event.target.value }))}
            placeholder={scope === "official" ? "Diagnose GMV delta" : "Weekly retention review"}
          />
          <Input
            label="Summary"
            value={editor.summary}
            onChange={(event) => setEditor((current) => ({ ...current, summary: event.target.value }))}
            placeholder="One-line analyst-facing description"
          />
          <Textarea
            label="Description"
            value={editor.description}
            onChange={(event) => setEditor((current) => ({ ...current, description: event.target.value }))}
            placeholder="What this workflow is for and how it should behave."
            className="min-h-[96px]"
          />
          <Textarea
            label="Prompt template"
            value={editor.promptTemplate}
            onChange={(event) => setEditor((current) => ({ ...current, promptTemplate: event.target.value }))}
            placeholder={"Use {{metric_name}} and {{time_window}} placeholders."}
            className="min-h-[168px] font-mono text-[13px]"
          />
          <Input
            label="Parameters"
            value={editor.parameterNames}
            onChange={(event) => setEditor((current) => ({ ...current, parameterNames: event.target.value }))}
            placeholder="metric_name, time_window, segment"
          />
          <div className="flex flex-wrap items-center gap-4 rounded-2xl border border-[#22324d] bg-[#09111f]/75 px-4 py-3 text-sm text-[#c7d8f4]">
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={editor.allowRunSql}
                onChange={(event) =>
                  setEditor((current) => ({ ...current, allowRunSql: event.target.checked }))
                }
              />
              Allow SQL execution
            </label>
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={editor.status === "published"}
                onChange={(event) =>
                  setEditor((current) => ({
                    ...current,
                    status: event.target.checked ? "published" : "draft",
                  }))
                }
              />
              Published
            </label>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            className="border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]"
          >
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            variant="primary"
            onClick={submitEditor}
            disabled={saving || !editor.title.trim() || !editor.summary.trim() || !editor.promptTemplate.trim()}
            className="gap-2"
          >
            {saving ? "Saving..." : scenario ? "Save changes" : "Create scenario"}
          </Button>
        </div>
      </div>
    </div>
  );
}
