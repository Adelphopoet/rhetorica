export const CHAT_DRAFT_PREFIX = "rhetorica.chatDraft.v1";
export const CHAT_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export interface ChatDraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  key(index: number): string | null;
  readonly length: number;
}

interface ChatDraftRecord {
  text: string;
  updatedAt: number;
}

export function chatDraftKey(workspaceId: string, conversationId: string | null): string {
  return `${CHAT_DRAFT_PREFIX}.${workspaceId}.${conversationId ?? "new"}`;
}

export function readChatDraft(
  storage: ChatDraftStorage,
  workspaceId: string,
  conversationId: string | null,
  now: number = Date.now()
): string | null {
  const key = chatDraftKey(workspaceId, conversationId);
  const raw = storage.getItem(key);
  if (!raw) {
    return null;
  }
  try {
    const record = JSON.parse(raw) as Partial<ChatDraftRecord>;
    if (typeof record.text !== "string" || typeof record.updatedAt !== "number") {
      storage.removeItem(key);
      return null;
    }
    if (now - record.updatedAt > CHAT_DRAFT_TTL_MS) {
      storage.removeItem(key);
      return null;
    }
    return record.text.trim() ? record.text : null;
  } catch {
    storage.removeItem(key);
    return null;
  }
}

export function writeChatDraft(
  storage: ChatDraftStorage,
  workspaceId: string,
  conversationId: string | null,
  text: string,
  now: number = Date.now()
): void {
  const key = chatDraftKey(workspaceId, conversationId);
  const trimmed = text.trim();
  if (!trimmed) {
    storage.removeItem(key);
    return;
  }
  storage.setItem(key, JSON.stringify({ text, updatedAt: now } satisfies ChatDraftRecord));
}

export function clearChatDraft(
  storage: ChatDraftStorage,
  workspaceId: string,
  conversationId: string | null
): void {
  storage.removeItem(chatDraftKey(workspaceId, conversationId));
}

export function clearExpiredChatDrafts(storage: ChatDraftStorage, now: number = Date.now()): number {
  let removed = 0;
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key || !key.startsWith(CHAT_DRAFT_PREFIX)) {
      continue;
    }
    try {
      const record = JSON.parse(storage.getItem(key) ?? "null") as Partial<ChatDraftRecord> | null;
      if (
        record &&
        typeof record.updatedAt === "number" &&
        now - record.updatedAt > CHAT_DRAFT_TTL_MS
      ) {
        storage.removeItem(key);
        removed += 1;
      }
    } catch {
      // keep the key - do not destroy user data on malformed input
    }
  }
  return removed;
}