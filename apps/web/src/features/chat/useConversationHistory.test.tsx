import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useConversationHistory } from "./useConversationHistory";
import { listConversations } from "./historyApi";
import type { ConversationSummary } from "./historyApi";
vi.mock("./historyApi", async importOriginal => ({ ...await importOriginal<typeof import("./historyApi")>(), listConversations: vi.fn() }));
afterEach(() => vi.clearAllMocks());
const rows = (count: number) => Array.from({ length: count }, (_, index) => ({ id: String(index), title: `Chat ${index}` }) as ConversationSummary);
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return renderHook(() => useConversationHistory("default", rows(25)), { wrapper });
}
it("uses a lookahead row and does not reset a page after mount", async () => {
  vi.mocked(listConversations).mockImplementation(async (_workspace, _limit, options) => options?.offset ? rows(3) : rows(25));
  const hook = mount();
  await waitFor(() => expect(hook.result.current.isFetching).toBe(false));
  act(() => hook.result.current.next());
  await waitFor(() => expect(hook.result.current.conversations).toHaveLength(3));
  expect(hook.result.current.page).toBe(1);
  expect(hook.result.current.hasNext).toBe(false);
  expect(listConversations).toHaveBeenLastCalledWith("default", 25, expect.objectContaining({ offset: 24, search: "" }));
});
it("debounces server search and resets pagination; errors stay retryable", async () => {
  vi.mocked(listConversations).mockResolvedValue(rows(25));
  const hook = mount();
  await waitFor(() => expect(hook.result.current.isFetching).toBe(false));
  act(() => hook.result.current.next());
  await waitFor(() => expect(hook.result.current.isFetching).toBe(false));
  act(() => hook.result.current.setSearch("net_revenue"));
  await waitFor(() => expect(listConversations).toHaveBeenLastCalledWith("default", 25, expect.objectContaining({ offset: 0, search: "net_revenue" })));
  expect(hook.result.current.page).toBe(0);
  vi.mocked(listConversations).mockRejectedValueOnce(new Error("offline"));
  await act(async () => { await hook.result.current.refetch(); });
  await waitFor(() => expect(hook.result.current.error?.message).toBe("offline"));
  vi.mocked(listConversations).mockResolvedValue([]);
  await act(async () => { await hook.result.current.refetch(); });
  await waitFor(() => expect(hook.result.current.error).toBeNull());
  expect(hook.result.current.conversations).toEqual([]);
});
