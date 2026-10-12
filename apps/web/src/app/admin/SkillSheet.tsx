"use client";

import { adminLabel } from "./adminLabels";

import { Alert, Badge, Button, Input, Sheet, Switch, Textarea } from "@rhetorica/ui";

import type { WorkspaceSkillItem } from "./types";
import { formatDateTime } from "./adminUtils";
import { useSessionState } from "./useSheetSession";

type SkillSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingSkill: WorkspaceSkillItem | null;
  onSubmit: (formData: FormData) => Promise<void>;
  saving: boolean;
  error: string | null;
  sessionNonce?: number;
};

function NativeSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="flex h-10 w-full rounded-lg border border-control-border bg-surface px-3 py-2 text-sm text-primary outline-none transition focus:border-transparent focus:ring-2 focus:ring-accent"
    />
  );
}

function manualSkillUseWhen(skill: WorkspaceSkillItem | null) {
  const raw = skill?.content_bundle?.assets?.[0]?.frontmatter?.use_when;
  return typeof raw === "string" ? raw : skill?.summary ?? "";
}

function gitLabImportDefaults(skill: WorkspaceSkillItem | null) {
  const match = skill?.source_url?.match(/^gitlab:\/\/([^/]+)\/(.+)@([^/]+)\/(.*)$/);
  return {
    connector: match?.[1] ?? "",
    project: match?.[2] ?? "",
    ref: match?.[3] ?? "",
    path: match?.[4] ?? "",
  };
}

export function SkillSheet({
  open,
  onOpenChange,
  editingSkill,
  onSubmit,
  saving,
  error,
  sessionNonce = 0,
}: SkillSheetProps) {
  const isEdit = editingSkill != null;
  const sessionKey = `${editingSkill?.id ?? "new-skill"}:${sessionNonce}`;
  const gitLab = gitLabImportDefaults(editingSkill);
  const [sourceKind, setSourceKind] = useSessionState<WorkspaceSkillItem["source_kind"]>(
    sessionKey,
    () => editingSkill?.source_kind ?? "github_markdown_url"
  );

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={isEdit ? "Редактирование навыка рабочего пространства" : "Добавление навыка рабочего пространства"}
      description="Навыки задают версионируемые инструкции. Источник должен быть каноническим, а приоритет — явным"
      keepMounted
    >
      <div className="space-y-3">
        {error ? (
          <Alert variant="danger" title="Ошибка сохранения" description={error} />
        ) : null}

        <form
          key={sessionKey}
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(new FormData(event.currentTarget));
          }}
          className="space-y-3"
        >
          <input type="hidden" name="skill_id" value={editingSkill?.id ?? ""} />

          <div className="grid gap-3 md:grid-cols-2">
            <Input
              data-autofocus
              label="Название"
              name="name"
              defaultValue={editingSkill?.name ?? ""}
              placeholder="clickhouse-optimizer-playbook"
              required
            />
            <input type="hidden" name="capability_family" value="chat_instruction" />
            <input type="hidden" name="engine_scope" value="any" />
            <Input
              label="Приоритет"
              type="number"
              name="priority"
              min={0}
              max={1000}
              defaultValue={editingSkill?.priority ?? 100}
            />
            <label>
              <span className="mb-1 block text-sm font-medium text-primary">Статус</span>
              <NativeSelect name="status" defaultValue={editingSkill?.status ?? "active"}>
                <option value="active">активен</option>
                <option value="inactive">неактивен</option>
              </NativeSelect>
            </label>
            <div className="rounded-lg border border-border-strong bg-surface px-4 py-3">
              <Switch
                name="visible_to_default_assistant"
                defaultChecked={editingSkill?.visible_to_default_assistant ?? true}
                label="Ассистент по умолчанию"
              />
            </div>
            <label className="md:col-span-2">
              <span className="mb-1 block text-sm font-medium text-primary">Источник</span>
              <NativeSelect
                name="source_kind"
                value={sourceKind}
                onChange={(event) => {
                  setSourceKind(
                    event.currentTarget.value as WorkspaceSkillItem["source_kind"]
                  );
                }}
              >
                <option value="github_markdown_url">URL GitHub Markdown</option>
                <option value="gitlab_bundle">Пакет GitLab (зафиксированный коммит)</option>
                <option value="manual_markdown">Ручной текст</option>
              </NativeSelect>
            </label>
            <div className="md:col-span-2">
              {sourceKind === "manual_markdown" ? (
                <div className="grid gap-3">
                  <Input
                    label="Применять, когда"
                    name="use_when"
                    defaultValue={manualSkillUseWhen(editingSkill)}
                    placeholder="Применять при подготовке ответов поддержке"
                    maxLength={500}
                    required
                  />
                  <Textarea
                    label="Текст навыка вручную"
                    name="content_markdown"
                    defaultValue={editingSkill?.content_markdown ?? ""}
                    placeholder={"# Тон ответов поддержки\nОтвечать кратко и уточнять недостающий контекст"}
                    minLength={1}
                    maxLength={32768}
                    rows={12}
                    required
                  />
                </div>
              ) : sourceKind === "gitlab_bundle" ? (
                <div className="grid gap-3 md:grid-cols-2">
                  <Input label="Подключение GitLab" name="connector_name" defaultValue={gitLab.connector} required />
                  <Input label="Путь проекта" name="project_path" defaultValue={gitLab.project} placeholder="group/project" required />
                  <Input label="SHA коммита или ref" name="ref" defaultValue={gitLab.ref} required />
                  <Input label="Каталог пакета" name="path" defaultValue={gitLab.path} placeholder="skills/data-quality" />
                  <p className="md:col-span-2 text-xs text-secondary">
                    Импорт однократно разрешает ref в SHA коммита, читает только Markdown и текстовые файлы и не запускает файлы репозитория
                  </p>
                </div>
              ) : (
                <Input
                  label="URL GitHub Markdown"
                  name="source_url"
                  defaultValue={editingSkill?.source_url ?? ""}
                  placeholder="https://github.com/ClickHouse/agent-skills"
                  required
                />
              )}
            </div>
          </div>

          <div className="grid gap-3 rounded-lg border border-border bg-surface/90 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">
                  Контракт выполнения
                </p>
                <p className="mt-1 text-sm text-primary">Назначение и источник — явные входные данные выполнения.</p>
              </div>
              <Badge variant="primary">управление</Badge>
            </div>
            <dl className="grid gap-3 text-sm text-primary md:grid-cols-2">
              <div>
                <dt className="text-xs uppercase tracking-[0.16em] text-muted">Проверка</dt>
                <dd className="mt-1">{adminLabel(editingSkill?.validation_status ?? "pending")}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-[0.16em] text-muted">Последняя проверка</dt>
                <dd className="mt-1">{formatDateTime(editingSkill?.last_validated_at ?? null)}</dd>
              </div>
              <div className="md:col-span-2">
                <dt className="text-xs uppercase tracking-[0.16em] text-muted">Загруженное название</dt>
                <dd className="mt-1">{editingSkill?.title ?? "—"}</dd>
              </div>
            </dl>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-3">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              className="border border-border-strong bg-surface text-primary hover:bg-elevated"
            >
              Отмена
            </Button>
            <Button type="submit" isLoading={saving}>
              {isEdit ? "Сохранить навык" : "Создать навык"}
            </Button>
          </div>
        </form>
      </div>
    </Sheet>
  );
}
