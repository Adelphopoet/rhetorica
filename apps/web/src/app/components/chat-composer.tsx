"use client";

import { useEffect, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";
import { Button, cn, Textarea, Tooltip } from "@rhetorica/ui";
import { Bookmark, Check, ChevronDown, File, LoaderCircle, Paperclip, Send, Sparkles, Square, Star, X } from "lucide-react";
import type { CodingTeamOption } from "@/features/coding/codingApi";
import type { AgentProfile } from "@/features/agents/agentsApi";
import { useAgentMenuStore } from "@/features/agents/agentMenuStore";
import { formatFileSize, type AttachmentDraft } from "@/features/files/fileApi";
import type { ChatWidth } from "./chat-tools";

export type ComposerDock = "bottom" | "right";

export const DEFAULT_COMPOSER_DOCK_WIDTH = 380;
export const DEFAULT_COMPOSER_DOCK_HEIGHT = 170;
export const MIN_COMPOSER_DOCK_WIDTH = 280;
export const MIN_COMPOSER_DOCK_HEIGHT = 120;

function clampDockWidth(value: number, max?: number): number {
  return Math.max(MIN_COMPOSER_DOCK_WIDTH, max === undefined ? value : Math.min(max, value));
}

function clampDockHeight(value: number, max?: number): number {
  return Math.max(MIN_COMPOSER_DOCK_HEIGHT, max === undefined ? value : Math.min(max, value));
}

interface ChatComposerProps {
  codingControls?: ReactNode;
  value: string;
  loading: boolean;
  chatWidth: ChatWidth;
  dock: ComposerDock;
  onToggleDock: () => void;
  dockWidth: number;
  dockHeight: number;
  onResizeDockWidth: (width: number) => void;
  onResizeDockHeight: (height: number) => void;
  maxDockWidth?: number;
  maxDockHeight?: number;
  agents?: AgentProfile[];
  selectedAgentId?: string | null;
  teams?: CodingTeamOption[];
  selectedTeamId?: string | null;
  onSelectTeam?: (team: CodingTeamOption) => void;
  agentLocked?: boolean;
  onChange: (value: string) => void;
  onSelectAgent?: (agentId: string | null) => void;
  onToggleAgentStar?: (agent: AgentProfile) => void;
  onToggleAgentFavorite?: (agent: AgentProfile) => void;
  agentStarPendingAgentId?: string | null;
  agentFavoritePendingAgentId?: string | null;
  onSubmit: () => void;
  onCancel: () => void;
  attachments?: AttachmentDraft[];
  onFilesSelected?: (files: File[]) => void;
  onRemoveAttachment?: (attachmentId: string) => void;
}

export function ChatComposer({
  codingControls,
  value,
  loading,
  chatWidth,
  dock,
  onToggleDock,
  dockWidth,
  dockHeight,
  onResizeDockWidth,
  onResizeDockHeight,
  maxDockWidth,
  maxDockHeight,
  agents = [],
  selectedAgentId = null,
  teams = [],
  selectedTeamId = null,
  onSelectTeam = () => undefined,
  agentLocked = false,
  onChange,
  onSelectAgent = () => undefined,
  onToggleAgentStar = () => undefined,
  onToggleAgentFavorite = () => undefined,
  agentStarPendingAgentId = null,
  agentFavoritePendingAgentId = null,
  onSubmit,
  onCancel,
  attachments = [],
  onFilesSelected = () => undefined,
  onRemoveAttachment = () => undefined,
}: ChatComposerProps) {
  const agentMenuOpen = useAgentMenuStore((state) => state.agentMenuOpen);
  const agentMenuPulse = useAgentMenuStore((state) => state.agentMenuPulse);
  const toggleAgentMenu = useAgentMenuStore((state) => state.toggleAgentMenu);
  const closeAgentMenu = useAgentMenuStore((state) => state.closeAgentMenu);
  const agentMenuRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const submitDisabled = loading || (!value.trim() && attachments.length === 0);
  const [agentSearch, setAgentSearch] = useState("");
  const [searchMenuOpen, setSearchMenuOpen] = useState(agentMenuOpen);
  if (searchMenuOpen !== agentMenuOpen) {
    // Reset the search when the menu closes, during render rather than in an effect.
    setSearchMenuOpen(agentMenuOpen);
    if (!agentMenuOpen) setAgentSearch("");
  }
  const visibleAgents = agents.filter(agent => `${agent.title} ${agent.description ?? ""} ${agent.slug}`.toLocaleLowerCase().includes(agentSearch.trim().toLocaleLowerCase()));
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) ?? null;
  const selectedTeam = teams.find((team) => team.id === selectedTeamId) ?? null;
  const agentTitle = (id?: string | null) => id === "__default_assistant__" ? "Rhetorica" : agents.find(agent => agent.id === id)?.title ?? "координатор";
  const visibleTeams = teams.filter(team => `${team.name} ${agentTitle(team.coordinator_agent_profile_id)} ${(team.members ?? []).map(member => member.label).join(" ")}`.toLocaleLowerCase().includes(agentSearch.trim().toLocaleLowerCase()));
  const favoriteAgents = visibleAgents.filter((agent) => agent.is_favorite);
  const otherAgents = visibleAgents.filter((agent) => !agent.is_favorite);
  const agentControlDisabled = loading || agentLocked;
  const agentStarPending = Boolean(
    selectedAgent && agentStarPendingAgentId === selectedAgent.id
  );
  const agentFavoritePending = Boolean(
    selectedAgent && agentFavoritePendingAgentId === selectedAgent.id
  );

  useEffect(() => {
    if (!agentMenuOpen) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !agentMenuRef.current?.contains(target)) {
        closeAgentMenu();
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeAgentMenu();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [agentMenuOpen, closeAgentMenu]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }

  function selectAgent(agentId: string | null) {
    onSelectAgent(agentId);
    closeAgentMenu();
  }

  function acceptFiles(files: FileList | File[]) {
    onFilesSelected(Array.from(files));
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    if (!loading) acceptFiles(event.dataTransfer.files);
  }

  function startWidthDrag(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = dockWidth;
    const handleMove = (moveEvent: PointerEvent) => {
      onResizeDockWidth(clampDockWidth(startWidth - (moveEvent.clientX - startX), maxDockWidth));
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp, { once: true });
  }

  function startHeightDrag(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = dockHeight;
    const handleMove = (moveEvent: PointerEvent) => {
      onResizeDockHeight(clampDockHeight(startHeight - (moveEvent.clientY - startY), maxDockHeight));
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp, { once: true });
  }

  return (
    <>
      {dock === "bottom" ? (
        <div
          role="separator"
          aria-label="Изменить высоту поля ввода"
          onPointerDown={startHeightDrag}
          className="group z-10 h-1.5 w-full shrink-0 cursor-row-resize touch-none"
        >
          <div className="h-[3px] w-full rounded bg-transparent transition group-hover:bg-border-strong group-active:bg-accent" />
        </div>
      ) : null}
      <div
        data-testid="chat-composer"
        className={cn(
          "shrink-0 border-border bg-[linear-gradient(180deg,rgb(var(--color-page)/0.14),rgb(var(--color-page)/0.95)_24%,rgb(var(--color-page)/0.985))] relative backdrop-blur-xl",
          dock === "bottom" ? "border-t pb-3 px-4 pt-3 sm:px-5 xl:px-6" : "pb-2 flex h-full w-full flex-col border-l"
        )}
        style={dock === "bottom" ? { height: dockHeight, maxHeight: maxDockHeight } : undefined}
      >
        {dock === "right" ? (
          <div
            role="separator"
            aria-label="Изменить ширину поля ввода"
            onPointerDown={startWidthDrag}
            className="group absolute inset-y-0 left-0 z-10 w-1.5 cursor-col-resize touch-none"
          >
            <div className="absolute inset-y-0 left-1/2 w-[3px] -translate-x-1/2 rounded bg-transparent transition group-hover:bg-border-strong group-active:bg-accent" />
          </div>
        ) : null}
      <form
        onSubmit={handleSubmit}
        className={cn(
          "mx-auto w-full flex h-full min-h-0 flex-col",
          chatWidth === 1100 && "max-w-[1100px]",
          chatWidth === 1600 && "max-w-[1600px]"
        )}
      >
        <div
          onDragOver={(event) => { event.preventDefault(); setDragActive(true); }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          className={cn(
            "flex min-h-0 flex-1 flex-col",
            dock === "bottom"
              ? "rounded-[24px] border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.99))] p-3 shadow-[0_18px_50px_rgb(var(--color-shadow)/calc(0.28*var(--shadow-opacity-scale)))] sm:p-2.5 sm:pb-1.5"
              : "",
            dock === "bottom" &&
              (dragActive ? "border-accent ring-2 ring-accent/25" : "border-border-strong")
          )}
        >
          <Textarea
            value={value}
            data-tour="tour-chat-input"
            onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)}
            placeholder="Задайте вопрос о метрике, тренде, аномалии или причине проблемы. Rhetorica сохранит SQL и весь процесс выполнения запроса на виду"
            containerClassName="flex min-h-0 flex-1"
            innerClassName="h-full w-full"
            className={cn(
              "resize-none h-[inherit] min-h-0 flex-1 border-0 bg-transparent text-[15px] leading-7 text-primary placeholder:text-muted focus:ring-0",
              dock === "right" ? "px-5 pb-1 pt-3" : "px-1 py-1"
            )}
            disabled={loading}
            onKeyDown={(event: React.KeyboardEvent<HTMLTextAreaElement>) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSubmit();
              }
            }}
          />

          {attachments.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2" aria-label="Выбранные файлы">
              {attachments.map((attachment) => (
                <div key={attachment.id} className="inline-flex max-w-full items-center gap-2 rounded-xl border border-border-strong bg-surface px-2.5 py-1.5 text-xs text-primary">
                  {attachment.status === "uploading" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <File className="h-3.5 w-3.5 text-accent" />}
                  <span className="max-w-48 truncate">{attachment.file.name}</span>
                  <span className="text-secondary">{formatFileSize(attachment.file.size)}</span>
                  {attachment.status === "error" ? <span className="text-danger">{attachment.error ?? "Не удалось"}</span> : null}
                  <button type="button" aria-label={`Убрать ${attachment.file.name}`} disabled={loading} onClick={() => onRemoveAttachment(attachment.id)} className="text-secondary hover:text-primary disabled:opacity-40">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <div className={cn(
            "mt-2 flex flex-wrap items-center justify-end gap-2 border-t border-border pt-2.5",
            dock === "right" && "px-2"
          )}>
            <div className="mr-auto flex min-w-0 items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="sr-only"
                accept=".txt,.md,.csv,.tsv,.json,.parquet,.xlsx,.docx,.pptx,.pdf,.png,.jpg,.jpeg"
                onChange={(event) => event.target.files && acceptFiles(event.target.files)}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Прикрепить файлы"
                disabled={loading || attachments.length >= 5}
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip className="h-4 w-4" />
              </Button>
              <div ref={agentMenuRef} className="relative">
                <button
                  type="button"
                  aria-label="Агент"
                  data-tour="agent-menu"
                  aria-haspopup="menu"
                  aria-expanded={agentMenuOpen}
                  disabled={agentControlDisabled}
                  onClick={toggleAgentMenu}
                  className="inline-flex h-9 max-w-[260px] items-center gap-2 rounded-full border border-border-strong bg-surface px-3 text-sm text-primary shadow-[0_10px_30px_rgb(var(--color-shadow)/calc(0.18*var(--shadow-opacity-scale)))] transition hover:border-accent-border hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Sparkles className="h-4 w-4 shrink-0 text-accent" />
                  <span className="truncate">{selectedTeam ? `${selectedTeam.name} · команда` : selectedAgent?.title ?? "Rhetorica"}</span>
                  <ChevronDown className="h-4 w-4 shrink-0 text-secondary" />
                </button>

                {agentMenuOpen ? (
<div
                    role="menu"
                    aria-label="Выбор агента"
                    onKeyDown={event => {
                      if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
                      event.preventDefault();
                      const rows = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'));
                      if (!rows.length) return;
                      const current = rows.indexOf(document.activeElement as HTMLButtonElement);
                      const next = current === -1 ? (event.key === "ArrowDown" ? 0 : rows.length - 1) : (current + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
                      rows[next].focus();
                    }}
                    className={cn("absolute bottom-full left-0 z-30 mb-2 max-h-[min(60vh,32rem)] w-[min(340px,calc(100vw-32px))] overflow-x-hidden overflow-y-auto overscroll-contain rounded-[18px] border border-border-strong bg-surface p-2 pr-0 pl-[0.65rem] shadow-[0_24px_70px_rgb(var(--color-shadow)/calc(0.46*var(--shadow-opacity-scale)))] [scrollbar-gutter:stable]", agentMenuPulse && "glow-pulse")}
                  >
                    <div className="sticky top-0 z-10 bg-surface pb-2 pr-2">
                      <input autoFocus aria-label="Поиск агента" type="search" value={agentSearch} onChange={event => setAgentSearch(event.target.value)} placeholder="Найти агента…" className="h-10 w-full rounded-xl border border-control-border bg-elevated px-3 text-sm outline-none transition focus:border-accent-border focus:ring-2 focus:ring-accent/20" />
                    </div>
                    {(!agentSearch.trim() || "rhetorica".includes(agentSearch.trim().toLowerCase())) && <AgentMenuRow
                      title="Rhetorica"
                      description="По умолчанию"
                      selected={selectedAgentId == null && !selectedTeam}
                      onClick={() => selectAgent(null)}
                    />}
                    {favoriteAgents.length ? (
                      <AgentMenuGroup title="Избранное">
                        {favoriteAgents.map((agent) => (
                          <AgentMenuRow
                            key={agent.id}
                            title={agent.title}
                            description={agent.description ?? agent.slug}
                            starCount={agent.star_count}
                            selected={agent.id === selectedAgentId && !selectedTeam}
                            onClick={() => selectAgent(agent.id)}
                          />
                        ))}
                      </AgentMenuGroup>
                    ) : null}
                    {visibleTeams.length ? (
                      <AgentMenuGroup title="Команды">
                        {visibleTeams.map((team) => (
                          <AgentMenuRow
                            key={team.id}
                            title={team.name}
                            description={`Координатор: ${agentTitle(team.coordinator_agent_profile_id)} · ${(team.members ?? []).map(member => member.label).join(", ")}`}
                            selected={team.id === selectedTeamId}
                            onClick={() => { onSelectTeam(team); closeAgentMenu(); }}
                          />
                        ))}
                      </AgentMenuGroup>
                    ) : null}
                    <AgentMenuGroup title="Агенты">
                      {otherAgents.length > 0 ? (
                        otherAgents.map((agent) => (
                          <AgentMenuRow
                            key={agent.id}
                            title={agent.title}
                            description={agent.description ?? agent.slug}
                            starCount={agent.star_count}
                            selected={agent.id === selectedAgentId && !selectedTeam}
                            onClick={() => selectAgent(agent.id)}
                          />
                        ))
                      ) : (
                        <p className="px-3 py-3 text-sm text-secondary">{agentSearch ? "Агенты не найдены" : "Нет агентов"}</p>
                      )}
                    </AgentMenuGroup>
                  </div>
                ) : null}
              </div>
              {codingControls}
              <Tooltip
                content={dock === "bottom" ? "Разместить справа" : "Разместить внизу"}
                side={dock === "bottom" ? "top" : "bottom"}
                desktopSide="top"
              >
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  data-tour="tour-composer-dock"
                  aria-label={dock === "bottom" ? "Разместить справа" : "Разместить внизу"}
                  onClick={onToggleDock}
                  className="text-secondary hover:text-primary"
                >
                  {dock === "bottom" ? (
                    <DockRightIcon className="h-4 w-4" />
                  ) : (
                    <DockBottomIcon className="h-4 w-4" />
                  )}
                </Button>
              </Tooltip>
              {selectedAgent ? (
                <>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={selectedAgent.is_starred ? "Снять отметку с агента" : "Отметить агента"}
                    onClick={() => onToggleAgentStar(selectedAgent)}
                    isLoading={agentStarPending}
                    disabled={loading || agentStarPending}
                  >
                    <Star className={`h-4 w-4 ${selectedAgent.is_starred ? "fill-warning text-warning" : ""}`} />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={
                      selectedAgent.is_favorite ? "Убрать агента из избранного" : "Добавить агента в избранное"
                    }
                    onClick={() => onToggleAgentFavorite(selectedAgent)}
                    isLoading={agentFavoritePending}
                    disabled={loading || agentFavoritePending}
                  >
                    <Bookmark className={`h-4 w-4 ${selectedAgent.is_favorite ? "fill-accent text-accent" : ""}`} />
                  </Button>
                </>
              ) : null}
            </div>
            <div className="flex items-center justify-end gap-2">
              {loading ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={onCancel}
                  className="gap-2"
                >
                  <Square className="h-4 w-4" />
                  Отмена
                </Button>
              ) : null}
              <Button
                type="submit"
                variant="secondary"
                size="sm"
                aria-label="Отправить вопрос"
                disabled={submitDisabled}
                className="gap-2 border-border-strong bg-elevated shadow-[0_4px_6px_rgb(var(--color-accent)/0.05)] hover:bg-border-strong"
              >
                {loading ? (
                  <>
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                    Выполняется…
                  </>
                ) : (
                  <>
                    <Send className="h-4 w-4" />
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </form>
    </div>
    </>
  );
}

export function DockRightIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className={className}
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M15 4v16" />
      <rect x="15" y="5" width="6" height="14" fill="currentColor" fillOpacity="0.3" stroke="none" />
    </svg>
  );
}

export function DockBottomIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className={className}
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 15h18" />
      <rect x="4" y="15" width="16" height="4" fill="currentColor" fillOpacity="0.3" stroke="none" />
    </svg>
  );
}

function AgentMenuGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-2 border-t border-border pt-2">
      <p className="px-3 pb-1 text-[11px] font-semibold uppercase text-muted">{title}</p>
      <div className="grid gap-1">{children}</div>
    </div>
  );
}

function AgentMenuRow({
  title,
  description,
  starCount,
  selected,
  onClick,
}: {
  title: string;
  description: string;
  starCount?: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={selected}
      onClick={onClick}
      className={`grid w-full grid-cols-[1fr_auto] items-center gap-3 rounded-xl px-3 py-2 text-left transition ${
        selected
          ? "bg-hover text-primary"
          : "text-primary hover:bg-elevated hover:text-primary"
      }`}
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{title}</span>
        <span className="block truncate text-xs text-secondary">{description}</span>
      </span>
      <span className="flex items-center gap-2 text-xs text-secondary">
        {typeof starCount === "number" ? (
          <span className="inline-flex items-center gap-1">
            <Star className="h-3.5 w-3.5" />
            {starCount}
          </span>
        ) : null}
        {selected ? <Check className="h-4 w-4 text-accent" /> : null}
      </span>
    </button>
  );
}
