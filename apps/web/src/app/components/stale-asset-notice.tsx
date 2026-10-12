"use client";

import { useEffect, useState } from "react";
import { Button } from "@rhetorica/ui";
import { RotateCw } from "lucide-react";
import {
  acknowledgeStaleAsset,
  hasPendingChatAction,
  isRecoveryInProgress,
  isStaleDetectionFresh,
  onStaleAssetConfirmed,
  onStaleRecoverySettled,
} from "@/lib/staleRecovery";

export function StaleAssetNotice() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const update = () => {
      if (typeof window === "undefined") {
        return;
      }
      const storage = window.sessionStorage;
      if (isRecoveryInProgress(storage) || hasPendingChatAction(storage)) {
        setVisible(false);
        return;
      }
      setVisible(isStaleDetectionFresh(storage));
    };
    update();
    const unsubscribeConfirmed = onStaleAssetConfirmed(update);
    const unsubscribeSettled = onStaleRecoverySettled(update);
    return () => {
      unsubscribeConfirmed();
      unsubscribeSettled();
    };
  }, []);

  if (!visible) {
    return null;
  }

  return (
    <div
      role="alert"
      data-testid="stale-asset-notice"
      className="fixed inset-x-0 top-4 z-50 mx-auto w-fit max-w-full rounded-2xl border-2 border-success bg-success-subtle p-4 pt-3 shadow-[0_24px_70px_rgb(var(--color-shadow)/calc(0.4*var(--shadow-opacity-scale)))] max-[600px]:mx-[25px] max-[600px]:w-auto"
    >
      <div className="flex items-center gap-x-[35px]">
        <div className="flex min-w-0 items-start gap-3 max-[600px]:flex-1">
          <RotateCw className="mt-0.5 h-5 w-5 shrink-0 text-success" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-primary">Приложение обновилось</p>
            <p className="mt-1 text-xs text-secondary">
              Для корректной работы обновите страницу
              <br />
              Введённые данные будут сохранены
            </p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          className="shrink-0 gap-2"
          onClick={() => {
            if (typeof window !== "undefined") {
              acknowledgeStaleAsset(window.sessionStorage);
              window.location.reload();
            }
          }}
        >
          Обновить
        </Button>
      </div>
    </div>
  );
}
