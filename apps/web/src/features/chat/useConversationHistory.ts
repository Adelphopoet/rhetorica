"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { conversationListQueryKey, listConversations, type ConversationSummary } from "./historyApi";

export const CONVERSATION_PAGE_SIZE = 24;

export function useConversationHistory(workspaceId: string, initialConversations: ConversationSummary[]) {
  const [search, setSearch] = useState("");
  const [selection, setSelection] = useState({ search: "", page: 0 });
  useEffect(() => {
    if (search.trim() === selection.search) return;
    const timer = window.setTimeout(() => setSelection({ search: search.trim(), page: 0 }), 300);
    return () => window.clearTimeout(timer);
  }, [search, selection.search]);
  const query = useQuery({
    queryKey: [...conversationListQueryKey(workspaceId), selection],
    queryFn: ({ signal }) => listConversations(workspaceId, CONVERSATION_PAGE_SIZE + 1, {
      search: selection.search, offset: selection.page * CONVERSATION_PAGE_SIZE, signal,
    }),
    initialData: selection.page === 0 && !selection.search ? initialConversations : undefined,
  });
  const data = Array.isArray(query.data) ? query.data : [];
  return {
    ...query,
    conversations: data.slice(0, CONVERSATION_PAGE_SIZE),
    search, setSearch,
    page: selection.page,
    hasNext: data.length > CONVERSATION_PAGE_SIZE,
    searching: search.trim() !== selection.search,
    previous: () => setSelection(current => ({ ...current, page: Math.max(0, current.page - 1) })),
    next: () => setSelection(current => ({ ...current, page: current.page + 1 })),
  };
}
