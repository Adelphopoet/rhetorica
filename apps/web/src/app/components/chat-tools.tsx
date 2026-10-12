"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Tooltip, useToast } from "@rhetorica/ui";
import {
  BookOpen,
  Check,
  ChevronsLeftRight,
  Copy,
  HelpCircle,
  Menu,
  Moon,
  PanelRight,
  Sun,
} from "lucide-react";
import { DockBottomIcon, DockRightIcon, type ComposerDock } from "./chat-composer";
import { useGuidesPanelStore } from "@/features/guides/guidesPanelStore";
import { useTheme } from "@/features/theme/ThemeProvider";
import { startAppTour } from "./app-tour";

export type ChatWidth = 1100 | 1600 | "max";

const CHAT_WIDTH_OPTIONS: { value: ChatWidth; label: string }[] = [
  { value: 1100, label: "Обычный" },
  { value: 1600, label: "Расширенный" },
  { value: "max", label: "Во всю ширину" },
];

interface ChatToolsProps {
  canCopyChatHistory: boolean;
  chatWidth: ChatWidth;
  hasResultPayload: boolean;
  isResultPanelOpen: boolean;
  composerDock?: ComposerDock;
  onChangeChatWidth: (width: ChatWidth) => void;
  onCopyChatHistory: () => Promise<void>;
  onToggleSidebar: () => void;
  onToggleComposerDock?: () => void;
  onToggleResultPanel: () => void;
}

export function ChatTools({
  canCopyChatHistory,
  chatWidth,
  hasResultPayload,
  isResultPanelOpen,
  composerDock = "right",
  onChangeChatWidth,
  onCopyChatHistory,
  onToggleSidebar,
  onToggleComposerDock = () => undefined,
  onToggleResultPanel,
}: ChatToolsProps) {
  const { preference, setPreference } = useTheme();
  const isLight = preference === "light";
  const { addToast } = useToast();
  const guidesPanelOpen = useGuidesPanelStore((state) => state.guidesPanelOpen);
  const openGuidesPanel = useGuidesPanelStore((state) => state.openGuidesPanel);
  const closeGuidesPanel = useGuidesPanelStore((state) => state.closeGuidesPanel);
  const [copiedHistory, setCopiedHistory] = useState(false);
  const [widthMenuOpen, setWidthMenuOpen] = useState(false);
  const widthMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!widthMenuOpen) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !widthMenuRef.current?.contains(target)) {
        setWidthMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setWidthMenuOpen(false);
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [widthMenuOpen]);

  const handleCopyHistory = async () => {
    try {
      await onCopyChatHistory();
      setCopiedHistory(true);
      window.setTimeout(() => setCopiedHistory(false), 2000);
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось скопировать",
        description: error instanceof Error ? error.message : "Не удалось скопировать историю чата",
      });
    }
  };

  const handleToggleGuidesPanel = () => {
    if (guidesPanelOpen) {
      closeGuidesPanel();
      return;
    }

    openGuidesPanel();
  };

  const itemButtonClassName =
    "text-primary hover:bg-elevated hover:text-primary";

  return (
    <div className="-order-1 flex w-full shrink-0 flex-row items-center gap-2 border-b border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.95),rgb(var(--color-surface)/0.82))] px-3 py-2 backdrop-blur-xl lg:order-none lg:w-16 lg:flex-col lg:items-stretch lg:border-b-0 lg:border-l lg:px-2.5 lg:py-4">
      {hasResultPayload && !isResultPanelOpen ? (
        <div className="order-3 lg:order-none">
          <Tooltip content="Открыть панель результатов" side="bottom" desktopSide="left">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Открыть панель результатов"
              onClick={onToggleResultPanel}
              className={`h-9 w-9 ${itemButtonClassName}`}
            >
              <PanelRight className="h-4 w-4" />
            </Button>
          </Tooltip>
        </div>
      ) : null}

      {canCopyChatHistory ? (
        <div className="order-2 lg:order-none">
          <Tooltip content={copiedHistory ? "Скопировано" : "Скопировать чат"} side="bottom" desktopSide="left">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={copiedHistory ? "Скопировано" : "Скопировать чат"}
              onClick={() => {
                void handleCopyHistory();
              }}
              className={`h-9 w-9 ${itemButtonClassName}`}
            >
              {copiedHistory ? (
                <Check className="h-4 w-4 text-success" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </Button>
          </Tooltip>
        </div>
      ) : null}

      <div className="order-1 lg:hidden">
        <Tooltip content="Открыть сайдбар" side="bottom" desktopSide="left">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Открыть сайдбар"
            onClick={onToggleSidebar}
            className={`h-9 w-9 ${itemButtonClassName}`}
          >
            <Menu className="h-4 w-4" />
          </Button>
        </Tooltip>
      </div>

      <div className="order-6 lg:hidden">
        <Tooltip
          content={composerDock === "bottom" ? "Разместить справа" : "Разместить внизу"}
          side="bottom"
          desktopSide="left"
        >
          <Button
            type="button"
            variant="ghost"
            size="icon"
            data-tour="tour-composer-dock-toggle"
            aria-label={composerDock === "bottom" ? "Разместить справа" : "Разместить внизу"}
            onClick={onToggleComposerDock}
            className={`h-9 w-9 ${itemButtonClassName}`}
          >
            {composerDock === "bottom" ? (
              <DockRightIcon className="h-4 w-4" />
            ) : (
              <DockBottomIcon className="h-4 w-4" />
            )}
          </Button>
        </Tooltip>
      </div>

      <div className="order-5 lg:order-none">
        <div className="relative inline-flex" ref={widthMenuRef}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Ширина чата"
            aria-haspopup="menu"
            aria-expanded={widthMenuOpen}
            onClick={() => setWidthMenuOpen((current) => !current)}
            className={`h-9 w-9 ${itemButtonClassName}`}
          >
            <ChevronsLeftRight className="h-4 w-4" />
          </Button>
          {widthMenuOpen ? (
            <div
              role="menu"
              aria-label="Ширина чата"
              className="absolute right-full top-0 z-20 mr-2 min-w-40 overflow-hidden rounded-2xl border border-border-strong bg-surface p-1 shadow-[0_24px_70px_rgb(var(--color-shadow)/calc(0.35*var(--shadow-opacity-scale)))]"
            >
              <div className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-secondary">
                Ширина чата
              </div>
              {CHAT_WIDTH_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="menuitem"
                  className={`flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm text-secondary transition hover:bg-hover hover:text-primary ${
                    chatWidth === option.value ? "bg-hover text-primary" : ""
                  }`}
                  onClick={() => {
                    onChangeChatWidth(option.value);
                    setWidthMenuOpen(false);
                  }}
                >
                  {option.label}
                  {chatWidth === option.value ? <Check className="h-3.5 w-3.5" /> : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="order-4 lg:order-none lg:mt-auto">
        <Tooltip content="Полезные инструкции" side="bottom" desktopSide="left">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Полезные инструкции"
            aria-pressed={guidesPanelOpen}
            onClick={handleToggleGuidesPanel}
            className={`h-9 w-9 ${itemButtonClassName}`}
          >
            <BookOpen className="h-4 w-4" />
          </Button>
        </Tooltip>
      </div>

      <div className="order-4 lg:order-none">
        <Tooltip content={isLight ? "Тёмная тема" : "Светлая тема"} side="bottom" desktopSide="left">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Тема интерфейса"
            aria-pressed={isLight}
            title={isLight ? "Включить тёмную тему" : "Включить светлую тему"}
            onClick={() => setPreference(isLight ? "dark" : "light")}
            className={`h-9 w-9 ${itemButtonClassName}`}
          >
            {isLight ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
          </Button>
        </Tooltip>
      </div>

      <div className="order-4 lg:order-none">
        <Tooltip content="Тур по интерфейсу" side="bottom" desktopSide="left">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Тур по интерфейсу"
            onClick={startAppTour}
            className={`h-9 w-9 ${itemButtonClassName}`}
          >
            <HelpCircle className="h-4 w-4" />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}
