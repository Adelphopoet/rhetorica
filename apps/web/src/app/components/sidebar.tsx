"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Avatar, Button, cn } from "@rhetorica/ui";
import {
  Clock3,
  Diamond,
  LoaderCircle,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import type { ConversationSummary } from "@/features/chat/historyApi";
import { useTheme } from "@/features/theme/ThemeProvider";
import { formatMoscowShortDateTime, getMoscowCalendarDay } from "@/lib/dateTime";
import { pluralize } from "@/lib/plural";
import { getDisplayName, getInitials, ProfileMenu } from "./profile-menu";

export const DEFAULT_SIDEBAR_WIDTH = 280;
export const MIN_SIDEBAR_WIDTH = 200;

function clampSidebarWidth(value: number, max?: number): number {
  return Math.max(MIN_SIDEBAR_WIDTH, max === undefined ? value : Math.min(max, value));
}

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
  collapsed: boolean;
  onCollapseChange: (collapsed: boolean) => void;
  workspaceName: string;
  apiStatus: string;
  canAccessAdmin: boolean;
  userLogin: string;
  userDisplayName: string | null;
  userRole: string;
  userAuthProvider: string;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  historyLoading: boolean;
  interactionDisabled: boolean;
  deletionDisabled: boolean;
  deletingConversationId: string | null;
  width?: number;
  onResizeWidth?: (width: number) => void;
  maxWidth?: number;
  onNewChat: () => void;
  onSelectConversation: (conversationId: string) => void;
  onDeleteConversation: (conversationId: string) => void;
}

function formatConversationTimestamp(timestamp: string): string {
  return formatMoscowShortDateTime(timestamp) ?? timestamp;
}

function getConversationGroupLabel(timestamp: string): string {
  const today = getMoscowCalendarDay(new Date());
  const candidate = getMoscowCalendarDay(timestamp);
  if (today === null || candidate === null) {
    return "Ранее";
  }

  const diffInDays = today - candidate;

  if (diffInDays <= 0) {
    return "Сегодня";
  }
  if (diffInDays === 1) {
    return "Вчера";
  }
  if (diffInDays < 7) {
    return "На этой неделе";
  }

  return "Ранее";
}

function getConversationLabel(conversation: ConversationSummary): string {
  const title = conversation.title?.trim();
  return title && title.length > 0 ? title : "Анализ без названия";
}

function groupConversations(conversations: ConversationSummary[]) {
  const orderedSections = ["Сегодня", "Вчера", "На этой неделе", "Ранее"] as const;
  const grouped = new Map<string, ConversationSummary[]>();

  conversations.forEach((conversation) => {
    const label = getConversationGroupLabel(conversation.updated_at);
    const section = grouped.get(label);
    if (section) {
      section.push(conversation);
      return;
    }

    grouped.set(label, [conversation]);
  });

  return orderedSections
    .map((label) => ({
      label,
      conversations: grouped.get(label) ?? [],
    }))
    .filter((section) => section.conversations.length > 0);
}

export function Sidebar({
  isOpen,
  onClose,
  collapsed,
  onCollapseChange,
  workspaceName,
  apiStatus,
  canAccessAdmin,
  userLogin,
  userDisplayName,
  userRole,
  userAuthProvider,
  conversations,
  activeConversationId,
  historyLoading,
  interactionDisabled,
  deletionDisabled,
  deletingConversationId,
  width = DEFAULT_SIDEBAR_WIDTH,
  onResizeWidth = () => undefined,
  maxWidth,
  onNewChat,
  onSelectConversation,
  onDeleteConversation,
}: SidebarProps) {
  const { preference } = useTheme();
  const iconSrc = "/icon.png";
  const [profileMenuSignal, setProfileMenuSignal] = useState(0);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const closeMenu = useCallback(() => setOpenMenuId(null), []);

  function startWidthDrag(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const handleMove = (moveEvent: PointerEvent) => {
      onResizeWidth(clampSidebarWidth(startWidth + (moveEvent.clientX - startX), maxWidth));
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp, { once: true });
  }

  useLayoutEffect(() => {
    if (openMenuId === null || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const menuWidth = 192;
    const menuHeight = 140;
    const left = Math.min(Math.max(rect.right + 4, 8), Math.max(8, window.innerWidth - menuWidth - 8));
    const top = Math.min(Math.max(rect.top, 8), Math.max(8, window.innerHeight - menuHeight - 8));
    setMenuAnchor({ top, left });
  }, [openMenuId]);

  useEffect(() => {
    if (openMenuId === null) return;
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (target && target.nodeType === Node.ELEMENT_NODE) {
        if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) {
          return;
        }
      }
      closeMenu();
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [openMenuId, closeMenu]);

  const historySections = groupConversations(conversations);
  const openConversation = openMenuId
    ? conversations.find((conversation) => conversation.id === openMenuId) ?? null
    : null;
  const resolvedDisplayName = getDisplayName(userDisplayName, userLogin);
  const initials = getInitials(resolvedDisplayName);

  return (
    <>
      <div
        aria-hidden="true"
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-30 bg-overlay/[0.72] backdrop-blur-sm motion-safe:transition-opacity lg:hidden",
          isOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        )}
      />

      <aside
        data-tour="tour-sidebar"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex min-h-0 w-[280px] flex-col overflow-hidden border-r border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.95))] p-4 shadow-[24px_0_80px_rgb(var(--color-shadow)/calc(0.45*var(--shadow-opacity-scale)))] motion-safe:transition-transform max-[1599px]:p-3 lg:relative lg:z-auto lg:w-auto lg:rounded-l-[28px] lg:border-r lg:shadow-none",
          collapsed && "lg:hidden",
          isOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        )}
      >
        {profileMenuOpen ? (
          <div
            aria-hidden="true"
            className="pointer-events-auto absolute inset-0 z-30 rounded-none bg-overlay/[0.55] backdrop-blur-[6px] lg:rounded-l-[28px]"
          />
        ) : null}

        <div className="mb-4 flex items-center justify-between gap-3">
          {preference === "light" ? (
            <div>
              <span className="bg-gradient-to-r from-accent via-accent/80 to-accent/90 bg-clip-text text-[25px] font-semibold leading-none tracking-[0.02em] text-transparent">
                Rhetorica
              </span>
            </div>
          ) : (
            <div className="flex items-end">
              <span className="bg-gradient-to-r from-accent via-accent/80 to-accent/90 bg-clip-text text-[25px] font-semibold leading-none tracking-[0.02em] text-transparent">
                Rhetorica
              </span>
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label="Новый чат"
              onClick={() => {
                onNewChat();
                onClose();
              }}
              className="h-9 w-9 border border-border-strong/50 p-0 text-secondary transition-all focus:ring-0"
            >
              <Plus className="h-4 w-4" />
            </Button>

            <button
              type="button"
              onClick={() => onCollapseChange(true)}
              aria-label="Свернуть сайдбар"
              className="hidden rounded-full border border-border-strong p-2 text-secondary transition hover:text-primary lg:inline-flex"
            >
              <PanelLeftClose className="h-4 w-4" />
            </button>

            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть сайдбар"
              className="rounded-full border border-border-strong p-2 text-secondary transition hover:text-primary lg:hidden"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <section className="relative min-h-0 flex-1 overflow-y-auto pr-1">
          <div className="space-y-4">
            {historyLoading ? (
              <div className="space-y-2">
                {[0, 1, 2].map((item) => (
                  <div
                    key={item}
                    className="rounded-[22px] border border-border bg-surface/[0.82] px-3 py-3 text-sm text-secondary"
                  >
                    <span className="inline-flex items-center gap-2">
                      <LoaderCircle className="h-4 w-4 animate-spin text-accent" />
                      Загрузка чатов…
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            {!historyLoading && conversations.length === 0 ? (
              <div className="p-4 text-sm italic text-secondary">
                Нет сохранённых чатов...
              </div>
            ) : null}

            {!historyLoading
              ? historySections.map((section) => (
                  <div key={section.label}>
                    <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">
                      {section.label}
                    </p>
                    <div className="space-y-0.5">
                      {section.conversations.map((conversation) => {
                        const isActive = conversation.id === activeConversationId;
                        const label = getConversationLabel(conversation);

                        const isMenuOpen = openMenuId === conversation.id;

                        return (
                          <div
                            key={conversation.id}
                            className="group relative flex items-center rounded-[22px] transition"
                          >
                            <button
                              type="button"
                              disabled={interactionDisabled}
                              aria-current={isActive ? "page" : undefined}
                              onClick={() => {
                                onSelectConversation(conversation.id);
                                onClose();
                              }}
                              className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-[22px] py-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <Diamond
                                className={cn(
                                  "h-2.5 w-2.5 shrink-0 transition-colors",
                                  isActive
                                    ? "fill-current text-accent"
                                    : "text-border-strong group-hover:text-secondary"
                                )}
                              />

                              <span
                                className={cn(
                                  "min-w-0 flex-1 overflow-hidden whitespace-nowrap pr-2 text-sm font-medium transition-colors [mask-image:linear-gradient(to_right,black_calc(100%_-_15px),transparent)]",
                                  isActive
                                    ? "text-primary"
                                    : "text-secondary group-hover:text-primary"
                                )}
                                title={label}
                              >
                                {label}
                              </span>
                            </button>

                            <div
                              className={cn(
                                "pointer-events-none absolute bottom-0 right-[-2px] top-0 flex items-center rounded-r-xl pl-3 opacity-0 transition group-hover:pointer-events-auto group-hover:opacity-100 pointer-coarse:pointer-events-auto pointer-coarse:opacity-100",
                                "bg-gradient-to-r from-transparent to-surface",
                                isMenuOpen && "pointer-events-auto opacity-100"
                              )}
                              ref={isMenuOpen ? triggerRef : undefined}
                            >
                              <button
                                type="button"
                                aria-label={`Действия для ${label}`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setOpenMenuId(isMenuOpen ? null : conversation.id);
                                }}
                                className="rounded-xl bg-surface p-2 text-secondary shadow-[0_0_8px_var(--sidebar-dot-shadow)] transition hover:bg-[var(--sidebar-dot-hover-bg)] hover:text-primary"
                              >
                                <MoreHorizontal className="h-4 w-4" />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))
              : null}

          </div>
        </section>

        <div className="mt-auto pt-4">
          <ProfileMenu
            workspaceName={workspaceName}
            apiStatus={apiStatus}
            login={userLogin}
            displayName={userDisplayName}
            role={userRole}
            authProvider={userAuthProvider}
            canAccessAdmin={canAccessAdmin}
            rootClassName="w-full"
            triggerClassName="h-auto w-full justify-between rounded-[18px] border-border bg-[radial-gradient(circle_at_top_right,rgb(var(--color-accent)/0.12),transparent_26%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.95))] px-3 py-2 shadow-none"
            contentClassName="left-0 right-auto w-full max-w-full"
            forceDetailsVisible
            openUp
            openSignal={profileMenuSignal}
            onOpenChange={setProfileMenuOpen}
          />
        </div>

        {collapsed ? null : (
          <div
            role="separator"
            aria-label="Изменить ширину сайдбара"
            onPointerDown={startWidthDrag}
            className="group absolute inset-y-0 right-0 z-20 hidden w-1.5 cursor-col-resize touch-none lg:block"
          >
            <div className="absolute inset-y-0 right-1/2 w-[3px] translate-x-1/2 rounded bg-transparent transition group-hover:bg-border-strong group-active:bg-accent" />
          </div>
        )}

      </aside>

      {collapsed ? (
        <aside
          className="fixed inset-y-0 left-0 z-40 hidden min-h-0 w-[72px] flex-col items-center gap-3 overflow-hidden border-r border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.95))] p-3 shadow-[24px_0_80px_rgb(var(--color-shadow)/calc(0.45*var(--shadow-opacity-scale)))] lg:static lg:z-auto lg:flex lg:rounded-l-[28px] lg:border-r lg:shadow-none"
        >
          {preference === "light" ? (
            <span className="flex h-11 w-11 items-center justify-center rounded-full border border-accent-border bg-accent-subtle">
              <span className="bg-gradient-to-r from-accent to-accent-hover bg-clip-text text-[20px] font-bold leading-none text-transparent">
                R
              </span>
            </span>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- static favicon-style asset served from public/
            <img
              src={iconSrc}
              alt="Rhetorica"
              className="w-8 rounded-xl object-contain mb-2"
            />
          )}

          <button
            type="button"
            onClick={() => onCollapseChange(false)}
            aria-label="Развернуть сайдбар"
            className="flex h-11 w-11 items-center justify-center rounded-[20px] border border-border-strong p-2 text-secondary transition hover:border-border-strong hover:text-primary"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>

          <button
            type="button"
            onClick={onNewChat}
            aria-label="Новый чат"
            className="flex h-11 w-11 items-center justify-center rounded-[20px] border border-border-strong p-2 text-secondary transition hover:border-border-strong hover:text-primary"
          >
            <Plus className="h-4 w-4" />
          </button>

          <div className="mt-auto">
            <button
              type="button"
              onClick={() => {
                onCollapseChange(false);
                setProfileMenuSignal((value) => value + 1);
              }}
              aria-label="Открыть профиль"
              className="flex items-center justify-center transition"
            >
              <Avatar
                fallback={initials}
                className="h-11 w-11 border border-border-strong bg-[radial-gradient(circle_at_top,rgb(var(--color-accent)/0.32),transparent_65%),rgb(var(--color-elevated))] text-primary transition hover:border-accent"
              />
            </button>
          </div>
        </aside>
      ) : null}

      {openConversation && menuAnchor ? (
        createPortal(
          <div
            ref={menuRef}
            className="fixed z-[100] w-48 overflow-hidden rounded-xl border border-border-strong bg-surface py-1 shadow-xl"
            style={{ top: menuAnchor.top, left: menuAnchor.left }}
          >
            <div className="px-3 py-2">
              <div className="flex items-center gap-1.5 text-xs text-secondary">
                <Clock3 className="h-3 w-3" />
                {formatConversationTimestamp(openConversation.updated_at)}
              </div>
              <p className="mt-1 text-xs text-secondary">
                {openConversation.message_count}{" "}
                {pluralize(openConversation.message_count, ["сообщение", "сообщения", "сообщений"])}
              </p>
            </div>

            <div className="border-t border-border" />

            <button
              type="button"
              aria-label={`Удалить ${getConversationLabel(openConversation)}`}
              onClick={() => {
                onDeleteConversation(openConversation.id);
                closeMenu();
              }}
              disabled={deletionDisabled || deletingConversationId === openConversation.id}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger/70 transition hover:bg-danger/10 hover:text-danger disabled:cursor-not-allowed disabled:opacity-60"
            >
              {deletingConversationId === openConversation.id ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Удалить чат
            </button>
          </div>,
          document.body
        )
      ) : null}
    </>
  );
}
