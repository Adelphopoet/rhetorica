"use client";

import { FormEvent, useState } from "react";
import { Alert, Button, Dialog, Input, Switch } from "@rhetorica/ui";
import { RotateCcw } from "lucide-react";

import type { LocalUserItem } from "./types";

type UserRateLimitDialogProps = {
  user: LocalUserItem | null;
  open: boolean;
  busy: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onSave: (requestsPerHour: number | null) => Promise<void>;
  onReset: () => Promise<void>;
};

export function UserRateLimitDialog({
  user,
  open,
  busy,
  error,
  onOpenChange,
  onSave,
  onReset,
}: UserRateLimitDialogProps) {
  const [enabled, setEnabled] = useState(user?.api_rate_limit_per_hour != null);
  const [requestsPerHour, setRequestsPerHour] = useState(
    String(user?.api_rate_limit_per_hour ?? 20)
  );
  const [validationError, setValidationError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enabled) {
      setValidationError(null);
      await onSave(null);
      return;
    }

    const parsed = Number(requestsPerHour);
    if (!Number.isSafeInteger(parsed) || parsed <= 0) {
      setValidationError("Введите целое положительное число запросов в час.");
      return;
    }
    setValidationError(null);
    await onSave(parsed);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !busy && onOpenChange(next)}
      title={user ? `Лимит API для ${user.login}` : "Лимит запросов API"}
      description="Учитывает все авторизованные запросы /v1 из браузерных сессий, PAT и OAuth-учётных данных за один час."
      className="max-w-xl"
    >
      <form noValidate onSubmit={(event) => void submit(event)} className="space-y-5">
        <Switch
          data-autofocus
          label="Ограничить запросы API для этой учётной записи"
          checked={enabled}
          disabled={busy}
          onChange={(event) => setEnabled(event.currentTarget.checked)}
        />

        {enabled ? (
          <Input
            label="Запросов в час"
            type="number"
            min={1}
            step={1}
            required
            value={requestsPerHour}
            disabled={busy}
            onChange={(event) => setRequestsPerHour(event.currentTarget.value)}
          />
        ) : (
          <p className="rounded-xl border border-border-strong bg-surface p-3 text-sm text-secondary">
            Индивидуальный лимит не задан. Для этой учётной записи действует стандартное поведение без
            ограничений.
          </p>
        )}

        {validationError || error ? (
          <Alert
            variant="danger"
            title="Не удалось обновить лимит API"
            description={validationError ?? error ?? "Неизвестная ошибка"}
          />
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <Button
            type="button"
            variant="ghost"
            disabled={busy || user?.api_rate_limit_per_hour == null}
            onClick={() => void onReset()}
          >
            <RotateCcw className="h-4 w-4" />
            Сбросить использование
          </Button>
          <div className="flex gap-3">
            <Button type="button" variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button type="submit" isLoading={busy}>
              Сохранить лимит
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
