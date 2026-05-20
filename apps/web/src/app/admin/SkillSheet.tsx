"use client";

import { Alert, Badge, Button, Input, Sheet } from "@rhetorica/ui";

import type { WorkspaceSkillItem } from "./types";
import { formatDateTime } from "./adminUtils";

type SkillSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingSkill: WorkspaceSkillItem | null;
  onSubmit: (formData: FormData) => Promise<void>;
  saving: boolean;
  error: string | null;
};

function NativeSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="flex h-10 w-full rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 text-sm text-[#edf3ff] outline-none transition focus:border-transparent focus:ring-2 focus:ring-[#5da8ff]"
    />
  );
}

export function SkillSheet({
  open,
  onOpenChange,
  editingSkill,
  onSubmit,
  saving,
  error,
}: SkillSheetProps) {
  const isEdit = editingSkill != null;

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={isEdit ? "Edit workspace skill" : "Add workspace skill"}
      description="Skills define reusable optimization heuristics. Keep the source canonical and the priority explicit."
    >
      <div className="space-y-5">
        {error ? (
          <Alert variant="danger" title="Save failed" description={error} />
        ) : null}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(new FormData(event.currentTarget));
          }}
          className="space-y-5"
        >
          <input type="hidden" name="skill_id" value={editingSkill?.id ?? ""} />

          <div className="grid gap-4 md:grid-cols-2">
            <Input
              data-autofocus
              label="Name"
              name="name"
              defaultValue={editingSkill?.name ?? ""}
              placeholder="clickhouse-optimizer-playbook"
              required
            />
            <label>
              <span className="mb-2 block text-sm font-medium text-[#c7d8f4]">Engine scope</span>
              <NativeSelect
                name="engine_scope"
                defaultValue={editingSkill?.engine_scope ?? "clickhouse"}
              >
                <option value="clickhouse">ClickHouse</option>
                <option value="trino">Trino</option>
                <option value="greenplum">Greenplum</option>
                <option value="impala">Impala</option>
                <option value="any">Any</option>
              </NativeSelect>
            </label>
            <Input
              label="Priority"
              type="number"
              name="priority"
              min={0}
              max={1000}
              defaultValue={editingSkill?.priority ?? 100}
            />
            <label>
              <span className="mb-2 block text-sm font-medium text-[#c7d8f4]">Status</span>
              <NativeSelect name="status" defaultValue={editingSkill?.status ?? "active"}>
                <option value="active">active</option>
                <option value="inactive">inactive</option>
              </NativeSelect>
            </label>
            <div className="md:col-span-2">
              <Input
                label="GitHub Markdown URL"
                name="source_url"
                defaultValue={editingSkill?.source_url ?? ""}
                placeholder="https://github.com/ClickHouse/agent-skills"
                required
              />
            </div>
          </div>

          <div className="grid gap-3 rounded-[24px] border border-[#1f2d45] bg-[#0a111f]/90 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#6e84a8]">
                  Runtime contract
                </p>
                <p className="mt-1 text-sm text-[#d7e6ff]">Capability is fixed to `sql_optimization_advisor`.</p>
              </div>
              <Badge variant="primary">governance</Badge>
            </div>
            <dl className="grid gap-3 text-sm text-[#b4c6e2] md:grid-cols-2">
              <div>
                <dt className="text-xs uppercase tracking-[0.16em] text-[#6e84a8]">Validation</dt>
                <dd className="mt-1">{editingSkill?.validation_status ?? "pending"}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-[0.16em] text-[#6e84a8]">Last validated</dt>
                <dd className="mt-1">{formatDateTime(editingSkill?.last_validated_at ?? null)}</dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-xs uppercase tracking-[0.16em] text-[#6e84a8]">Fetched title</dt>
                <dd className="mt-1">{editingSkill?.title ?? "—"}</dd>
              </div>
            </dl>
          </div>

          <div className="flex items-center justify-end gap-3">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              className="border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]"
            >
              Cancel
            </Button>
            <Button type="submit" isLoading={saving}>
              {isEdit ? "Save skill" : "Create skill"}
            </Button>
          </div>
        </form>
      </div>
    </Sheet>
  );
}
