import { describe, expect, it } from "vitest";

import {
  CHAT_DRAFT_PREFIX,
  CHAT_DRAFT_TTL_MS,
  chatDraftKey,
  clearChatDraft,
  clearExpiredChatDrafts,
  readChatDraft,
  writeChatDraft,
  type ChatDraftStorage,
} from "./chatDraft";

function createStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    key: (index: number) => [...map.keys()][index] ?? null,
    get length() {
      return map.size;
    },
  } satisfies ChatDraftStorage;
}

describe("chat draft", () => {
  it("keys drafts per conversation and per workspace", () => {
    expect(chatDraftKey("default", "conv-1")).toBe(`${CHAT_DRAFT_PREFIX}.default.conv-1`);
    expect(chatDraftKey("default", null)).toBe(`${CHAT_DRAFT_PREFIX}.default.new`);
    expect(chatDraftKey("analytics", "conv-1")).toBe(`${CHAT_DRAFT_PREFIX}.analytics.conv-1`);
  });

  it("stores and reads back a draft", () => {
    const storage = createStorage();
    writeChatDraft(storage, "default", "conv-1", "  Почему упал NPS?  ");
    expect(readChatDraft(storage, "default", "conv-1")).toBe("  Почему упал NPS?  ");
  });

  it("keeps drafts of different chats isolated", () => {
    const storage = createStorage();
    writeChatDraft(storage, "default", "conv-1", "вопрос один");
    writeChatDraft(storage, "default", "conv-2", "вопрос два");
    expect(readChatDraft(storage, "default", "conv-1")).toBe("вопрос один");
    expect(readChatDraft(storage, "default", "conv-2")).toBe("вопрос два");
    expect(readChatDraft(storage, "default", null)).toBeNull();
  });

  it("clears a draft when the text becomes empty", () => {
    const storage = createStorage();
    writeChatDraft(storage, "default", "conv-1", "вопрос");
    writeChatDraft(storage, "default", "conv-1", "   ");
    expect(readChatDraft(storage, "default", "conv-1")).toBeNull();
  });

  it("removes expired drafts on read", () => {
    const storage = createStorage();
    writeChatDraft(storage, "default", "conv-1", "старый вопрос", 1000);
    expect(readChatDraft(storage, "default", "conv-1", 1000 + CHAT_DRAFT_TTL_MS + 1)).toBeNull();
  });

  it("clears a draft explicitly", () => {
    const storage = createStorage();
    writeChatDraft(storage, "default", "conv-1", "вопрос");
    clearChatDraft(storage, "default", "conv-1");
    expect(readChatDraft(storage, "default", "conv-1")).toBeNull();
  });

  it("cleans up only expired drafts and leaves other keys alone", () => {
    const storage = createStorage();
    writeChatDraft(storage, "default", "conv-old", "старый", 1000);
    writeChatDraft(storage, "default", "conv-new", "свежий", 1000 + CHAT_DRAFT_TTL_MS);
    const unrelatedKey = `${CHAT_DRAFT_PREFIX}-something-else`;
    storage.setItem(unrelatedKey, "not a draft");
    writeChatDraft(storage, "default", null, "новый draft", 1000 + CHAT_DRAFT_TTL_MS);

    const removed = clearExpiredChatDrafts(storage, 1000 + CHAT_DRAFT_TTL_MS + 1);

    expect(removed).toBe(1);
    expect(readChatDraft(storage, "default", "conv-old")).toBeNull();
    expect(
      readChatDraft(storage, "default", "conv-new", 1000 + CHAT_DRAFT_TTL_MS)
    ).toBe("свежий");
    expect(readChatDraft(storage, "default", null)).toBeNull();
    expect(storage.getItem(unrelatedKey)).toBe("not a draft");
  });

  it("drops malformed draft records on read", () => {
    const storage = createStorage();
    storage.setItem(chatDraftKey("default", "conv-bad"), "not json");
    expect(readChatDraft(storage, "default", "conv-bad")).toBeNull();
    expect(storage.getItem(chatDraftKey("default", "conv-bad"))).toBeNull();
  });
});