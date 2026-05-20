"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Button } from "@rhetorica/ui";
import { cn } from "@rhetorica/ui";
import {
  Clock3,
  LoaderCircle,
  MessageSquare,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import type { ConversationSummary } from "@/features/chat/historyApi";
import { ProfileMenu } from "./profile-menu";

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
  workspaceName: string;
  canAccessAdmin: boolean;
  userLogin: string;
  userDisplayName: string | null;
  userRole: string;
  userAuthProvider: string;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  historyLoading: boolean;
  interactionDisabled: boolean;
  deletingConversationId: string | null;
  onNewChat: () => void;
  onSelectConversation: (conversationId: string) => void;
  onDeleteConversation: (conversationId: string) => void;
}

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatConversationTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }

  const day = String(date.getUTCDate()).padStart(2, "0");
  const month = MONTH_LABELS[date.getUTCMonth()] ?? "???";
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");

  return `${day} ${month}, ${hours}:${minutes}`;
}

function getConversationGroupLabel(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "Older";
  }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const candidate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffInDays = Math.round((today.getTime() - candidate.getTime()) / 86_400_000);

  if (diffInDays <= 0) {
    return "Today";
  }
  if (diffInDays === 1) {
    return "Yesterday";
  }
  if (diffInDays < 7) {
    return "This week";
  }

  return "Older";
}

function getConversationLabel(conversation: ConversationSummary): string {
  const title = conversation.title?.trim();
  return title && title.length > 0 ? title : "Untitled analysis";
}

function groupConversations(conversations: ConversationSummary[]) {
  const orderedSections = ["Today", "Yesterday", "This week", "Older"] as const;
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
  workspaceName,
  canAccessAdmin,
  userLogin,
  userDisplayName,
  userRole,
  userAuthProvider,
  conversations,
  activeConversationId,
  historyLoading,
  interactionDisabled,
  deletingConversationId,
  onNewChat,
  onSelectConversation,
  onDeleteConversation,
}: SidebarProps) {
  const historySections = groupConversations(conversations);
  const shouldReduceMotion = useReducedMotion();

  return (
    <>
      <button
        type="button"
        aria-label="Close navigation"
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-30 bg-[#02050c]/72 backdrop-blur-sm transition lg:hidden",
          isOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        )}
      />

      <motion.aside
        initial={false}
        animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, x: 0 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[280px] flex-col overflow-hidden border-r border-[#16233a] bg-[linear-gradient(180deg,rgba(8,12,21,0.98),rgba(8,12,22,0.95))] p-4 shadow-[24px_0_80px_rgba(0,0,0,0.45)] lg:static lg:z-auto lg:w-[264px] lg:rounded-l-[28px] lg:border-r lg:shadow-none xl:w-[280px]",
          shouldReduceMotion ? "transition-opacity" : "transition-transform",
          isOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        )}
      >
        <div className="mb-6 flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <ProfileMenu
              workspaceName={workspaceName}
              login={userLogin}
              displayName={userDisplayName}
              role={userRole}
              authProvider={userAuthProvider}
              canAccessAdmin={canAccessAdmin}
              rootClassName="w-full"
              triggerClassName="h-auto w-full justify-between rounded-[22px] border-[#203550] bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.12),transparent_26%),linear-gradient(180deg,rgba(11,18,31,0.98),rgba(9,14,25,0.95))] px-4 py-4 shadow-none"
              contentClassName="left-0 right-auto w-full max-w-full"
              forceDetailsVisible
            />
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-[#23324e] p-2 text-[#a6b8d6] transition hover:text-white lg:hidden"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <section className="flex-none">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#687d9e]">
                Chats
              </p>
              <p className="mt-1 text-sm font-medium text-[#eef4ff]">
                Saved threads
              </p>
            </div>
          </div>

          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => {
              onNewChat();
              onClose();
            }}
            className="mb-4 w-full justify-center gap-2 rounded-2xl py-2.5 shadow-[0_12px_30px_rgba(93,168,255,0.2)]"
          >
            <Plus className="h-4 w-4" />
            New chat
          </Button>
        </section>

        <section className="min-h-0 flex-1 overflow-y-auto pr-1">
          <div className="space-y-4">
            {historyLoading ? (
              <div className="space-y-2">
                {[0, 1, 2].map((item) => (
                  <div
                    key={item}
                    className="rounded-[22px] border border-[#1d2b43] bg-[#09111f]/82 px-3 py-3 text-sm text-[#aab9d2]"
                  >
                    <span className="inline-flex items-center gap-2">
                      <LoaderCircle className="h-4 w-4 animate-spin text-[#5da8ff]" />
                      Loading chats
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            {!historyLoading && conversations.length === 0 ? (
              <div className="rounded-[22px] border border-dashed border-[#22324d] bg-[#09111f]/75 p-4 text-sm text-[#8ea3c5]">
                No saved chats yet.
              </div>
            ) : null}

            {!historyLoading
              ? historySections.map((section) => (
                  <div key={section.label}>
                    <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#5f7496]">
                      {section.label}
                    </p>
                    <div className="space-y-2">
                      {section.conversations.map((conversation) => {
                        const isActive = conversation.id === activeConversationId;
                        const isDeleting = deletingConversationId === conversation.id;
                        const label = getConversationLabel(conversation);

                        return (
                          <div
                            key={conversation.id}
                            className={cn(
                              "group flex items-start gap-2 rounded-[22px] border px-3 py-3 transition",
                              isActive
                                ? "border-[#4f7ebb] bg-[#112039] shadow-[0_14px_32px_rgba(14,28,52,0.38)]"
                                : "border-[#1d2b43] bg-[#09111f]/82 hover:border-[#31496c] hover:bg-[#0f182a]"
                            )}
                          >
                            <button
                              type="button"
                              onClick={() => {
                                onSelectConversation(conversation.id);
                                onClose();
                              }}
                              disabled={interactionDisabled}
                              className="min-w-0 flex-1 text-left disabled:cursor-not-allowed disabled:opacity-70"
                            >
                              <div className="flex items-start gap-2">
                                <span
                                  className={cn(
                                    "mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border",
                                    isActive
                                      ? "border-[#4b82ca] bg-[#14305a] text-[#89b7ff]"
                                      : "border-[#24344f] bg-[#0b1322] text-[#7c93b8]"
                                  )}
                                >
                                  {interactionDisabled && isActive ? (
                                    <LoaderCircle className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <MessageSquare className="h-4 w-4" />
                                  )}
                                </span>
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-start justify-between gap-3">
                                    <p className="truncate text-sm font-medium text-[#eef4ff]" title={label}>
                                      {label}
                                    </p>
                                    <span className="shrink-0 rounded-full border border-[#22324d] px-2 py-0.5 text-[11px] font-medium text-[#8ea3c5]">
                                      {conversation.message_count} msg
                                    </span>
                                  </div>
                                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#7f93b4]">
                                    <span className="inline-flex items-center gap-1">
                                      <Clock3 className="h-3.5 w-3.5" />
                                      {formatConversationTimestamp(conversation.updated_at)}
                                    </span>
                                  </div>
                                </div>
                              </div>
                            </button>

                            <button
                              type="button"
                              aria-label={`Delete ${label}`}
                              onClick={() => onDeleteConversation(conversation.id)}
                              disabled={interactionDisabled || isDeleting}
                              className="mt-0.5 rounded-xl border border-transparent p-2 text-[#7084a8] transition hover:border-[#2c405f] hover:bg-[#101a2c] hover:text-[#ffafb6] disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {isDeleting ? (
                                <LoaderCircle className="h-4 w-4 animate-spin" />
                              ) : (
                                <Trash2 className="h-4 w-4" />
                              )}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))
              : null}

          </div>
        </section>

      </motion.aside>
    </>
  );
}
