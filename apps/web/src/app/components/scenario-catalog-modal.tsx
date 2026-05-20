"use client";

import { X } from "lucide-react";

import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { ScenarioCatalog } from "./scenario-catalog";

type ScenarioCatalogModalProps = {
  isOpen: boolean;
  scenarios: ScenarioDefinition[];
  loading: boolean;
  runningScenarioId: string | null;
  saving: boolean;
  archivingScenarioId: string | null;
  onClose: () => void;
  onRunScenario: (
    scenarioId: string,
    inputs: Record<string, unknown>,
    renderedPrompt: string
  ) => void;
  onCreateScenario: () => void;
  onEditScenario: (scenario: ScenarioDefinition) => void;
  onArchiveScenario: (scenarioId: string) => void;
};

export function ScenarioCatalogModal({
  isOpen,
  scenarios,
  loading,
  runningScenarioId,
  saving,
  archivingScenarioId,
  onClose,
  onRunScenario,
  onCreateScenario,
  onEditScenario,
  onArchiveScenario,
}: ScenarioCatalogModalProps) {
  if (!isOpen) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#02050c]/72 p-3 backdrop-blur-sm sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[min(92vh,960px)] w-full max-w-6xl flex-col overflow-hidden rounded-[30px] border border-[#1d2940] bg-[linear-gradient(180deg,rgba(12,18,31,0.98),rgba(8,12,22,0.99))] shadow-[0_30px_90px_rgba(0,0,0,0.48)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[#172338] px-5 py-4 sm:px-6">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6e84a8]">
              Quick access
            </p>
            <h3 className="mt-1 text-xl font-semibold text-[#eef4ff]">Scenarios</h3>
          </div>
          <button
            type="button"
            aria-label="Close scenarios"
            onClick={onClose}
            className="rounded-full border border-[#23324e] p-2 text-[#a6b8d6] transition hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden p-4 sm:p-5">
          <ScenarioCatalog
            scenarios={scenarios}
            loading={loading}
            runningScenarioId={runningScenarioId}
            saving={saving}
            archivingScenarioId={archivingScenarioId}
            onRunScenario={onRunScenario}
            onCreateScenario={onCreateScenario}
            onEditScenario={onEditScenario}
            onArchiveScenario={onArchiveScenario}
          />
        </div>
      </div>
    </div>
  );
}
