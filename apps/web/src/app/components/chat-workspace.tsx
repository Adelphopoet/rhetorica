"use client";

import {
  lazy,
  startTransition,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
  type CSSProperties,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Skeleton, cn, useToast } from "@rhetorica/ui";
import { useRouter } from "next/navigation";
import { startWorkflow, WorkflowApiError, workflowRequest, type Capabilities } from "@/features/scenarios/workflowApi";
import { ConversationScenarioRuns } from "@/features/scenarios/WorkflowRunView";

import {
  agentListQueryKey,
  listAgents,
  setAgentFavorite,
  setAgentStar,
  type AgentProfile,
} from "@/features/agents/agentsApi";
import {
  buildChatAskResponse,
  resolveDeliveryState,
  type FeedbackReason,
  type ChatFeedbackValue,
  type ChatFeedbackDetails,
  type ChatMessageViewModel,
  type ExecutionHistoryItem,
} from "@/features/chat/chatViewModel";
import { copyTextToClipboard, serializeChatHistoryForCopy } from "@/features/chat/chatCopy";
import {
  deleteStagedFile,
  MAX_FILES_PER_TURN,
  uploadFile,
  validateSelectedFile,
  type AttachmentDraft,
  type FileArtifactRef,
} from "@/features/files/fileApi";
import {
  conversationListQueryKey,
  createConversation,
  deriveConversationTitle,
  cacheMessageIntermediateSteps,
  lazyLoadExecution,
  listConversations,
  loadConversationThread,
  mergeHydratedMessages,
  removeConversation,
  type ConversationSummary,
} from "@/features/chat/historyApi";
import {
  archiveScenario,
  createScenario,
  listScenarios,
  runScenario,
  scenarioListQueryKey,
  updateScenario,
  type SaveScenarioPayload,
  type ScenarioDefinition,
} from "@/features/scenarios/scenariosApi";
import {
  useChatStreaming,
  type DoneEvent,
  type ErrorEvent,
  type IntermediateSteps,
  type StreamingMessage,
} from "@/features/chat/useChatStreaming";
import { AppTour } from "./app-tour";
import {
  clearChatDraft,
  clearExpiredChatDrafts,
  readChatDraft,
  writeChatDraft,
} from "@/features/chat/chatDraft";
import {
  clearRecoveryState,
  emitStaleRecoverySettled,
  markRecoverySettled,
  onStaleAssetConfirmed,
  requestRecoveryReload,
  setPendingChatAction,
  takePendingChatAction,
  type PendingChatAction,
} from "@/lib/staleRecovery";
import { ChatComposer, type ComposerDock, DEFAULT_COMPOSER_DOCK_HEIGHT, DEFAULT_COMPOSER_DOCK_WIDTH } from "./chat-composer";
import { ScenarioEditorModal } from "./scenario-editor-modal";
import { ScenarioCatalogModal } from "./scenario-catalog-modal";
import { ChatThread } from "./chat-thread";
import { ChatTools, type ChatWidth } from "./chat-tools";
import { GuidesSurface } from "./guides-surface";
import { Sidebar, DEFAULT_SIDEBAR_WIDTH } from "./sidebar";
import { useGuidesPanelStore } from "@/features/guides/guidesPanelStore";
import { UI_COMPOSER_DOCK_COOKIE, UI_RESULT_PANEL_COOKIE, UI_SIDEBAR_COLLAPSED_COOKIE, UI_COMPOSER_DOCK_WIDTH_COOKIE, UI_COMPOSER_DOCK_HEIGHT_COOKIE, UI_SIDEBAR_WIDTH_COOKIE, writeUiCookie } from "@/lib/uiCookies";

const LazyResultSurface = lazy(async () => ({
  default: (await import("./result-surface")).ResultSurface,
}));

const SCENARIO_CAPABILITIES_STALE_TIME_MS = 60_000;

function scenarioCapabilitiesQueryKey(workspace: string) {
  return ["scenario-capabilities", workspace] as const;
}

function fetchScenarioCapabilities(workspace: string): Promise<Capabilities> {
  return workflowRequest<Capabilities>(
    `scenario-capabilities?workspace_id=${encodeURIComponent(workspace)}`
  );
}

type ChatWorkspaceProps = {
  workspaceName: string;
  canAccessAdmin: boolean;
  apiStatus: string;
  currentUser: {
    login: string;
    displayName: string | null;
    role: string;
    authProvider: string;
  };
  recentExecutions: ExecutionHistoryItem[];
  initialConversations: ConversationSummary[];
  initialScenarios: ScenarioDefinition[];
  initialAgents?: AgentProfile[];
  initialComposerDock?: ComposerDock;
  initialResultPanelOpen?: boolean;
  initialSidebarCollapsed?: boolean;
  initialComposerDockWidth?: number;
  initialComposerDockHeight?: number;
  initialSidebarWidth?: number;
};

function getInitialMessages(): ChatMessageViewModel[] {
  return [];
}

type InFlightChatAction =
  | { type: "switch"; targetConversationId: string }
  | { type: "send"; draftText: string; hadAttachments: boolean };

function hasDetachedPendingTurn(messages: ChatMessageViewModel[]): boolean {
  const lastMessage = messages.at(-1);
  return lastMessage?.role === "user";
}

function buildDetachedPendingMessage(conversationId: string): ChatMessageViewModel {
  return {
    id: `detached-run-${conversationId}`,
    role: "assistant",
    text: "Работа всё ещё выполняется. Ответ появится здесь автоматически, когда завершится сохранённый ход",
    timestamp: new Date(),
    deliveryState: "streaming",
  };
}

function upsertConversation(
  conversations: ConversationSummary[] | undefined,
  nextConversation: ConversationSummary
): ConversationSummary[] {
  const previous = conversations ?? [];
  return [nextConversation, ...previous.filter((conversation) => conversation.id !== nextConversation.id)];
}

function ResultSurfaceFallback() {
  return (
    <aside className="fixed inset-y-3 right-3 z-40 flex w-full max-w-[min(100vw-24px,560px)] flex-col rounded-[28px] border border-border bg-surface/[0.94] p-6 shadow-[-24px_0_80px_rgb(var(--color-shadow)/calc(0.45*var(--shadow-opacity-scale)))] xl:static xl:inset-auto xl:z-0 xl:h-full xl:w-[520px] xl:max-w-none xl:rounded-none xl:rounded-r-[32px] xl:border-y-0 xl:border-r-0 xl:shadow-none">
      <Skeleton className="h-5 w-44" />
      <Skeleton className="mt-6 h-32 w-full" />
      <Skeleton className="mt-5 h-80 w-full" />
    </aside>
  );
}

export function ChatWorkspace({
  workspaceName,
  canAccessAdmin,
  apiStatus,
  currentUser,
  recentExecutions,
  initialConversations,
  initialScenarios,
  initialAgents = [],
  initialComposerDock = "bottom",
  initialResultPanelOpen = false,
  initialSidebarCollapsed = false,
  initialComposerDockWidth = DEFAULT_COMPOSER_DOCK_WIDTH,
  initialComposerDockHeight = DEFAULT_COMPOSER_DOCK_HEIGHT,
  initialSidebarWidth = DEFAULT_SIDEBAR_WIDTH,
}: ChatWorkspaceProps) {
  const router = useRouter();
  const { addToast } = useToast();
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<AttachmentDraft[]>([]);
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [threadLoadingConversationId, setThreadLoadingConversationId] = useState<string | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageViewModel[]>(getInitialMessages);
  const [feedbackPendingByMessageId, setFeedbackPendingByMessageId] = useState<
    Record<string, boolean>
  >({});
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(initialSidebarCollapsed);
  const [resultPanelOpen, setResultPanelOpen] = useState(initialResultPanelOpen);
  const [panelEverOpened, setPanelEverOpened] = useState(initialResultPanelOpen);
  const [chatWidth, setChatWidth] = useState<ChatWidth>(1100);
  const [composerDock, setComposerDock] = useState<ComposerDock>(initialComposerDock);
  const [composerDockWidth, setComposerDockWidth] = useState(initialComposerDockWidth);
  const [composerDockHeight, setComposerDockHeight] = useState(initialComposerDockHeight);
  const [sidebarWidth, setSidebarWidth] = useState(initialSidebarWidth);
  const guidesPanelOpen = useGuidesPanelStore((state) => state.guidesPanelOpen);
  const closeGuidesPanel = useGuidesPanelStore((state) => state.closeGuidesPanel);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const rowRef = useRef<HTMLDivElement | null>(null);
  const sectionRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (guidesPanelOpen) {
      setResultPanelOpen(false);
    }
  }, [guidesPanelOpen]);
  const [gridWidth, setGridWidth] = useState(0);
  const [rowWidth, setRowWidth] = useState(0);
  const [sectionHeight, setSectionHeight] = useState(0);
  const [isXl, setIsXl] = useState<boolean>(false);
  const [selectedResultMessageId, setSelectedResultMessageId] = useState<string | null>(null);
  const [runningScenarioId, setRunningScenarioId] = useState<string | null>(null);
  const [scenarioEditorOpen, setScenarioEditorOpen] = useState(false);
  const [scenarioCatalogOpen, setScenarioCatalogOpen] = useState(false);
  const [editingScenario, setEditingScenario] = useState<ScenarioDefinition | null>(null);
  const [scenarioAuthoringTarget, setScenarioAuthoringTarget] = useState<"create" | "edit" | null>(null);
  const [isScenarioAuthoringPending, startScenarioAuthoringTransition] = useTransition();
  const [launchingScenarioId, setLaunchingScenarioId] = useState<string | null>(null);
  const [, startScenarioLaunchTransition] = useTransition();
  // Cache for lazy-loaded executions: executionId -> ExecuteSqlResponse
  const [lazyExecutionsCache, setLazyExecutionsCache] = useState<Record<string, import("@/features/chat/chatViewModel").ExecuteSqlResponse | null>>({});
  // Currently loading execution IDs
  const [loadingExecutionIds, setLoadingExecutionIds] = useState<Set<string>>(new Set());
  // Mapping of messageId -> executionId for assistant messages (for lazy loading)
  const [messageExecutionIds, setMessageExecutionIds] = useState<Record<string, string>>({});
  const feedbackRequestVersionRef = useRef<Record<string, number>>({});
  const feedbackPendingRef = useRef<Record<string, boolean>>({});
  const clearStreamingRef = useRef<() => void>(() => undefined);
  const conversationIdRef = useRef<string | null>(null);
  const threadEpochRef = useRef(0);
  const activeStreamEpochRef = useRef<number | null>(null);
  const questionRef = useRef("");
  const inFlightChatActionRef = useRef<InFlightChatAction | null>(null);
  const lastSubmittedTextRef = useRef<string | null>(null);
  const persistTickRef = useRef(0);
  const recoveryRanRef = useRef(false);
  const conversationsQueryKey = conversationListQueryKey(workspaceName);
  const scenariosQueryKey = scenarioListQueryKey(workspaceName);
  const agentsQueryKey = agentListQueryKey(workspaceName);

  const conversationsQuery = useQuery({
    queryKey: conversationsQueryKey,
    queryFn: () => listConversations(workspaceName),
    initialData: initialConversations,
  });
  const scenariosQuery = useQuery({
    queryKey: scenariosQueryKey,
    queryFn: () => listScenarios(workspaceName),
    initialData: initialScenarios,
  });
  const agentsQuery = useQuery({
    queryKey: agentsQueryKey,
    queryFn: () => listAgents(workspaceName),
    initialData: initialAgents,
  });
  const userLabel = currentUser.displayName?.trim() || currentUser.login;

  const setActiveConversationId = useCallback((nextConversationId: string | null) => {
    conversationIdRef.current = nextConversationId;
    setConversationId(nextConversationId);
  }, []);

  const createConversationMutation = useMutation({
    mutationFn: ({ title, agentId }: { title: string; agentId: string | null }) =>
      createConversation(workspaceName, title, agentId),
    onSuccess: (createdConversation) => {
      queryClient.setQueryData<ConversationSummary[]>(
        conversationsQueryKey,
        (current) => upsertConversation(current, createdConversation)
      );
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
    },
  });
  const setAgentStarMutation = useMutation({
    mutationFn: ({ agentId, enabled }: { agentId: string; enabled: boolean }) =>
      setAgentStar(workspaceName, agentId, enabled),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: agentsQueryKey });
    },
  });
  const setAgentFavoriteMutation = useMutation({
    mutationFn: ({ agentId, enabled }: { agentId: string; enabled: boolean }) =>
      setAgentFavorite(workspaceName, agentId, enabled),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: agentsQueryKey });
    },
  });

  const deleteConversationMutation = useMutation({
    mutationFn: removeConversation,
    onMutate: async (targetConversationId) => {
      await queryClient.cancelQueries({ queryKey: conversationsQueryKey });
      const previousConversations =
        queryClient.getQueryData<ConversationSummary[]>(conversationsQueryKey) ?? [];

      queryClient.setQueryData<ConversationSummary[]>(
        conversationsQueryKey,
        previousConversations.filter((conversation) => conversation.id !== targetConversationId)
      );

      return { previousConversations };
    },
    onError: (error, _targetConversationId, context) => {
      if (context?.previousConversations) {
        queryClient.setQueryData(conversationsQueryKey, context.previousConversations);
      }
      addToast({
        type: "error",
        title: "Не удалось удалить",
        description: error instanceof Error ? error.message : "Не удалось удалить чат",
      });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
    },
  });

  const openConversationMutation = useMutation({
    mutationFn: loadConversationThread,
  });
  const saveScenarioMutation = useMutation({
    mutationFn: ({ payload, scenarioId }: { payload: SaveScenarioPayload; scenarioId?: string }) =>
      scenarioId
        ? updateScenario(scenarioId, {
            workspace_id: payload.workspace_id,
            title: payload.title,
            summary: payload.summary,
            description: payload.description,
            prompt_template: payload.prompt_template,
            input_schema: payload.input_schema,
            tool_policy: payload.tool_policy,
            default_connector_bindings: payload.default_connector_bindings,
            status: payload.status,
          })
        : createScenario(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scenariosQueryKey });
    },
  });
  const archiveScenarioMutation = useMutation({
    mutationFn: (scenarioId: string) => archiveScenario(scenarioId, workspaceName),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scenariosQueryKey });
    },
  });

  const handleStreamingComplete = useCallback(
    (
      streamingMessage: StreamingMessage,
      doneEvent: DoneEvent,
      intermediateSteps: IntermediateSteps
    ) => {
      if (activeStreamEpochRef.current !== threadEpochRef.current) {
        return;
      }

      const finalAnswer = streamingMessage.text || doneEvent.answer;
      const payload = buildChatAskResponse(doneEvent, finalAnswer);
      cacheMessageIntermediateSteps(doneEvent.assistant_message_id, intermediateSteps);
      const finalMessage: ChatMessageViewModel = {
        id: streamingMessage.id,
        backendMessageId: doneEvent.assistant_message_id ?? undefined,
        role: "assistant",
        text: finalAnswer,
        timestamp: streamingMessage.timestamp,
        payload,
        thinkingBlocks: intermediateSteps.thinkingBlocks,
        toolCalls: intermediateSteps.toolCalls,
        executionStatuses: intermediateSteps.executionStatuses,
        deliveryState: resolveDeliveryState({
          isComplete: true,
          payload,
        }),
      };

      if (doneEvent.conversation_id) {
        setActiveConversationId(doneEvent.conversation_id);
      }

      startTransition(() => {
        setSelectedResultMessageId(finalMessage.id);
        setResultPanelOpen(false);
        setMessages((prev) => {
          const filtered = prev.filter((message) => message.id !== streamingMessage.id);
          return [...filtered, finalMessage];
        });
      });

      setLoading(false);
      activeStreamEpochRef.current = null;
      inFlightChatActionRef.current = null;
      lastSubmittedTextRef.current = null;
      clearChatDraft(window.localStorage, workspaceName, conversationIdRef.current);
      clearChatDraft(window.localStorage, workspaceName, null);
      setTimeout(() => clearStreamingRef.current(), 0);
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
    },
    [conversationsQueryKey, queryClient, setActiveConversationId, workspaceName]
  );

  const handleStreamingError = useCallback(
    (
      streamingMessage: StreamingMessage | null,
      errorEvent: ErrorEvent,
      intermediateSteps: IntermediateSteps
    ) => {
      if (activeStreamEpochRef.current !== threadEpochRef.current) {
        return;
      }

      const shouldRefreshConversation =
        errorEvent.code === "stream_ended_without_terminal_event" ||
        /fetch|network|terminated|connection/i.test(errorEvent.message);

      const targetConversationId = conversationIdRef.current;
      if (shouldRefreshConversation && targetConversationId) {
        void (async () => {
          try {
            const thread = await loadConversationThread(targetConversationId);
            if (conversationIdRef.current !== targetConversationId) {
              return;
            }
            const newMessageExecutionIds: Record<string, string> = {};
            for (const { messageId, executionId } of thread.assistantExecutionIds) {
              newMessageExecutionIds[messageId] = executionId;
            }

            startTransition(() => {
              setMessages((current) => mergeHydratedMessages(current, thread.messages));
              setFeedbackPendingByMessageId({});
            });
            setMessageExecutionIds(newMessageExecutionIds);
            void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
          } catch {
            // Keep the local transport error if history refresh also fails.
          }
        })();
      }

      if (streamingMessage) {
        const errorMessage: ChatMessageViewModel = {
          id: streamingMessage.id,
          role: "assistant",
          text:
            streamingMessage.text ||
            (shouldRefreshConversation
              ? "Соединение прервано во время выполнения анализа. Обновляю сохранённый чат"
              : `Ошибка: ${errorEvent.message}`),
          timestamp: streamingMessage.timestamp,
          thinkingBlocks: intermediateSteps.thinkingBlocks,
          toolCalls: intermediateSteps.toolCalls,
          executionStatuses: intermediateSteps.executionStatuses,
          deliveryState: "error",
        };

        startTransition(() => {
          setMessages((prev) => {
            const filtered = prev.filter((message) => message.id !== streamingMessage.id);
            return [...filtered, errorMessage];
          });
        });
      }

      setLoading(false);
      activeStreamEpochRef.current = null;
      inFlightChatActionRef.current = null;
      lastSubmittedTextRef.current = null;
      addToast({
        type: "error",
        title: shouldRefreshConversation ? "Соединение прервано" : "Ошибка трансляции",
        description: shouldRefreshConversation
          ? "Восстанавливаю чат из сохранённого состояния"
          : errorEvent.message,
      });
      setTimeout(() => clearStreamingRef.current(), 0);
    },
    [addToast, conversationsQueryKey, queryClient]
  );

  const { streamingMessage, startStreaming, cancelStreaming, clearStreaming } = useChatStreaming({
    workspaceId: workspaceName,
    onComplete: (message, doneEvent, intermediateSteps) => {
      handleStreamingComplete(message, doneEvent, intermediateSteps);
    },
    onError: (errorEvent, message, intermediateSteps) => {
      handleStreamingError(message, errorEvent, intermediateSteps);
    },
  });

  useEffect(() => {
    clearStreamingRef.current = clearStreaming;
  }, [clearStreaming]);

  useEffect(() => {
    questionRef.current = question;
  }, [question]);

  useEffect(() => {
    clearExpiredChatDrafts(window.localStorage);
    if (conversationIdRef.current === null) {
      const draft = readChatDraft(window.localStorage, workspaceName, null);
      if (draft) {
        setQuestion(draft);
      }
    }
  }, [workspaceName]);

  useEffect(() => {
    const tick = persistTickRef.current + 1;
    persistTickRef.current = tick;
    if (tick === 1) {
      return;
    }
    const timer = window.setTimeout(() => {
      writeChatDraft(window.localStorage, workspaceName, conversationIdRef.current, question);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [question, workspaceName]);

  const resetThreadState = useCallback(
    (nextConversationId: string | null) => {
      threadEpochRef.current += 1;
      activeStreamEpochRef.current = null;
      clearStreaming();
      setActiveConversationId(nextConversationId);
setThreadLoadingConversationId(nextConversationId);
      setQuestion(readChatDraft(window.localStorage, workspaceName, nextConversationId) ?? "");
      setPendingAttachments([]);
      setLoading(false);
      setResultPanelOpen(false);
      setSelectedResultMessageId(null);
      setMessageExecutionIds({});
      setLazyExecutionsCache({});
      setLoadingExecutionIds(new Set());
      startTransition(() => {
        setMessages(getInitialMessages());
        setFeedbackPendingByMessageId({});
      });
    },
    [clearStreaming, setActiveConversationId, workspaceName]
  );

  const handleNewChat = useCallback(() => {
    writeChatDraft(window.localStorage, workspaceName, conversationIdRef.current, question);
    resetThreadState(null);
  }, [question, resetThreadState, workspaceName]);

  const clearThreadLoading = useCallback((loadedConversationId: string) => {
    setThreadLoadingConversationId((current) =>
      current === loadedConversationId ? null : current
    );
  }, []);

  const refreshConversationThread = useCallback(
    async (targetConversationId: string, currentMessages: ChatMessageViewModel[]) => {
      const thread = await loadConversationThread(targetConversationId);
      if (conversationIdRef.current !== targetConversationId) {
        clearThreadLoading(targetConversationId);
        return currentMessages;
      }
      const newMessageExecutionIds: Record<string, string> = {};
      for (const { messageId, executionId } of thread.assistantExecutionIds) {
        newMessageExecutionIds[messageId] = executionId;
      }
      const hydratedMessages = mergeHydratedMessages(currentMessages, thread.messages);

      startTransition(() => {
        clearThreadLoading(targetConversationId);
        setMessages(hydratedMessages);
        setFeedbackPendingByMessageId({});
      });
      setSelectedAgentId(thread.detail.agent_id ?? null);
      setMessageExecutionIds(newMessageExecutionIds);
      setSelectedResultMessageId(
        [...hydratedMessages].reverse().find((m) => m.role === "assistant" && m.payload)?.id ?? null
      );
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
      return hydratedMessages;
    },
    [clearThreadLoading, conversationsQueryKey, queryClient]
  );

  const handleSelectConversation = useCallback(
    async (targetConversationId: string): Promise<boolean> => {
      if (targetConversationId === conversationIdRef.current) {
        return true;
      }
      if (createConversationMutation.isPending || deleteConversationMutation.isPending) {
        return false;
      }

      const previousConversationId = conversationIdRef.current;
      const previousMessages = messages;
      const previousResultPanelOpen = resultPanelOpen;
      const previousQuestion = question;

      inFlightChatActionRef.current = { type: "switch", targetConversationId };
      writeChatDraft(window.localStorage, workspaceName, previousConversationId, previousQuestion);
      resetThreadState(targetConversationId);

      try {
        await refreshConversationThread(
          targetConversationId,
          previousMessages
        );
        if (conversationIdRef.current !== targetConversationId) {
          return false;
        }
        setResultPanelOpen(false);
        inFlightChatActionRef.current = null;
        return true;
      } catch (error) {
        inFlightChatActionRef.current = null;
        if (conversationIdRef.current !== targetConversationId) {
          return false;
        }
        setActiveConversationId(previousConversationId);
        setResultPanelOpen(previousResultPanelOpen);
        setQuestion(previousQuestion);
        startTransition(() => {
          clearThreadLoading(targetConversationId);
          setMessages(previousMessages);
        });
        addToast({
          type: "error",
          title: "Не удалось загрузить историю",
          description: error instanceof Error ? error.message : "Не удалось загрузить чат",
        });
        return false;
      }
    },
    [
      addToast,
      clearThreadLoading,
      createConversationMutation.isPending,
      deleteConversationMutation.isPending,
      messages,
      question,
      refreshConversationThread,
      resetThreadState,
      resultPanelOpen,
      setActiveConversationId,
      workspaceName,
    ]
  );

  const handleConfirmedStaleAsset = useCallback(() => {
    if (typeof window === "undefined") {
      return;
    }
    const storage = window.sessionStorage;
    const currentText = questionRef.current;
    writeChatDraft(window.localStorage, workspaceName, conversationIdRef.current, currentText);

    const inFlight = inFlightChatActionRef.current;
    if (!inFlight) {
      return;
    }

    if (!requestRecoveryReload(storage)) {
      return;
    }

    const pendingAction: PendingChatAction =
      inFlight.type === "switch"
        ? {
            type: "switch",
            workspaceId: workspaceName,
            targetConversationId: inFlight.targetConversationId,
            draftText: currentText,
            hadAttachments: false,
          }
        : {
            type: "send",
            workspaceId: workspaceName,
            conversationId: conversationIdRef.current,
            draftText: inFlight.draftText,
            hadAttachments: inFlight.hadAttachments,
          };
    setPendingChatAction(storage, pendingAction);
    window.location.reload();
  }, [workspaceName]);

  useEffect(() => {
    return onStaleAssetConfirmed(handleConfirmedStaleAsset);
  }, [handleConfirmedStaleAsset]);

  useEffect(() => {
    if (typeof window === "undefined" || recoveryRanRef.current) {
      return;
    }
    const storage = window.sessionStorage;
    const pending = takePendingChatAction(storage);
    if (!pending) {
      return;
    }
    recoveryRanRef.current = true;

    const restoreComposer = (text: string) => setQuestion(text || "");
    const finishRecovery = () => {
      clearRecoveryState(storage);
      emitStaleRecoverySettled();
    };
    const failRecovery = () => {
      markRecoverySettled(storage);
      emitStaleRecoverySettled();
    };
    const restoreConversationAndReturn = async (conversationId: string) => {
      const switched = await handleSelectConversation(conversationId);
      restoreComposer(pending.draftText);
      if (switched) {
        finishRecovery();
        return;
      }
      failRecovery();
    };

    if (pending.type === "send") {
      if (pending.hadAttachments) {
        addToast({
          type: "warning",
          title: "Файлы не были прикреплены",
          description: "Текст сохранён в поле ввода. Прикрепите файлы заново и отправьте сообщение.",
        });
      } else {
        addToast({
          type: "warning",
          title: "Сообщение не было отправлено",
          description:
            "Текст сохранён в поле ввода. Проверьте, что сообщение не дошло, и отправьте его ещё раз.",
        });
      }
      if (pending.conversationId) {
        restoreConversationAndReturn(pending.conversationId);
        return;
      }
      restoreComposer(pending.draftText);
      finishRecovery();
      return;
    }

    restoreConversationAndReturn(pending.targetConversationId);
  }, [addToast, handleSelectConversation]);

  const handleDeleteConversation = useCallback(
    async (targetConversationId: string) => {
      if (
        loading ||
        openConversationMutation.isPending ||
        createConversationMutation.isPending
      ) {
        return;
      }

      try {
        await deleteConversationMutation.mutateAsync(targetConversationId);
        if (targetConversationId === conversationIdRef.current) {
          resetThreadState(null);
        }
      } catch {
        return;
      }
    },
    [
      createConversationMutation.isPending,
      deleteConversationMutation,
      loading,
      openConversationMutation.isPending,
      resetThreadState,
    ]
  );

  const submitFeedback = useCallback(
    async (
      messageId: string,
      feedback: ChatFeedbackValue,
      messageConversationId: string | null | undefined,
      details?: ChatFeedbackDetails
    ) => {
      const targetConversationId = messageConversationId ?? conversationIdRef.current;
      if (!targetConversationId || feedbackPendingRef.current[messageId]) {
        return;
      }
      feedbackPendingRef.current[messageId] = true;

      const previousFeedback = messages.find((message) => message.backendMessageId === messageId);
      const feedbackReason: FeedbackReason | null = feedback === "wrong" ? "other" : null;
      const feedbackUpdate = {
        feedback,
        feedbackReason,
        feedbackComment: null,
        feedbackWhatWentWrong: details?.whatWentWrong ?? null,
        feedbackExpectedAnswer: details?.expectedAnswer ?? null,
      };

      startTransition(() => {
        setMessages((prev) =>
          prev.map((message) =>
            message.backendMessageId === messageId ? { ...message, ...feedbackUpdate } : message
          )
        );
      });

      const requestVersion = (feedbackRequestVersionRef.current[messageId] ?? 0) + 1;
      feedbackRequestVersionRef.current[messageId] = requestVersion;
      setFeedbackPendingByMessageId((prev) => ({ ...prev, [messageId]: true }));

      let shouldRollback = false;
      try {
        const response = await fetch(`/api/chat/messages/${messageId}/feedback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: targetConversationId,
            workspace_id: workspaceName,
            feedback,
            reason: feedbackReason,
            comment: null,
            what_went_wrong: details?.whatWentWrong ?? null,
            expected_answer: details?.expectedAnswer ?? null,
          }),
        });

        if (!response.ok) {
          shouldRollback = true;
        }
      } catch {
        shouldRollback = true;
      } finally {
        const isLatestRequest = feedbackRequestVersionRef.current[messageId] === requestVersion;
        if (!isLatestRequest) {
          return;
        }

        feedbackPendingRef.current[messageId] = false;
        setFeedbackPendingByMessageId((prev) => ({ ...prev, [messageId]: false }));
        if (shouldRollback) {
          startTransition(() => {
            setMessages((prev) =>
              prev.map((message) =>
                message.backendMessageId === messageId
                  ? {
                      ...message,
                      feedback: previousFeedback?.feedback ?? null,
                      feedbackReason: previousFeedback?.feedbackReason ?? null,
                      feedbackComment: previousFeedback?.feedbackComment ?? null,
                      feedbackWhatWentWrong: previousFeedback?.feedbackWhatWentWrong ?? null,
                      feedbackExpectedAnswer: previousFeedback?.feedbackExpectedAnswer ?? null,
                    }
                  : message
              )
            );
          });
          addToast({
            type: "warning",
            title: "Отзыв не сохранён",
            description: "Выбранная оценка была отменена",
          });
        }
      }
    },
    [addToast, messages, workspaceName]
  );

  const doSubmit = useCallback(
    async (
      prefilledQuestion?: string,
      clarificationId?: string,
      clarificationValue?: string
    ) => {
      const cleanQuestion = (prefilledQuestion ?? question).trim();
      const drafts = prefilledQuestion === undefined ? pendingAttachments : [];
      if ((!cleanQuestion && drafts.length === 0) || loading) {
        return;
      }
      inFlightChatActionRef.current = {
        type: "send",
        draftText: cleanQuestion,
        hadAttachments: drafts.length > 0,
      };
      lastSubmittedTextRef.current = cleanQuestion;
      setLoading(true);

      const optimisticMessageId = `user-${Date.now()}`;
      const optimisticMessage: ChatMessageViewModel = {
        id: optimisticMessageId,
        role: "user",
        text: cleanQuestion,
        files: [],
        timestamp: new Date(),
        deliveryState: "complete",
      };
      startTransition(() => setMessages((prev) => [...prev, optimisticMessage]));
      if (prefilledQuestion === undefined) {
        setQuestion("");
      }

      const submitEpoch = threadEpochRef.current;
      let nextConversationId = conversationIdRef.current ?? undefined;
      if (!nextConversationId) {
        try {
          const createdConversation = await createConversationMutation.mutateAsync(
            {
              title: deriveConversationTitle(cleanQuestion || drafts[0]?.file.name || "Анализ файла"),
              agentId: selectedAgentId,
            }
          );
          if (threadEpochRef.current !== submitEpoch) {
            return;
          }
          nextConversationId = createdConversation.id;
          setActiveConversationId(createdConversation.id);
          clearChatDraft(window.localStorage, workspaceName, null);
        } catch (error) {
          if (threadEpochRef.current !== submitEpoch) {
            return;
          }
          inFlightChatActionRef.current = null;
          lastSubmittedTextRef.current = null;
          setLoading(false);
          setMessages((prev) => prev.filter((item) => item.id !== optimisticMessageId));
          if (prefilledQuestion === undefined) setQuestion(cleanQuestion);
          addToast({
            type: "error",
            title: "Не удалось создать чат",
            description: error instanceof Error ? error.message : "Не удалось создать чат",
          });
          return;
        }
      }

      if (threadEpochRef.current !== submitEpoch) {
        return;
      }

      let uploadedFiles: FileArtifactRef[] = [];
      if (drafts.length > 0) {
        if (!nextConversationId) {
          setLoading(false);
          return;
        }
        const results: Array<FileArtifactRef | null> = Array(drafts.length).fill(null);
        const failures: Error[] = [];
        let cursor = 0;
        const uploadWorker = async () => {
          while (cursor < drafts.length) {
            const index = cursor++;
            const draft = drafts[index];
            setPendingAttachments((current) => current.map((item) =>
              item.id === draft.id ? { ...item, status: "uploading", error: undefined } : item
            ));
            try {
              results[index] = await uploadFile({
                file: draft.file,
                workspaceId: workspaceName,
                conversationId: nextConversationId,
              });
            } catch (error) {
              const failure = error instanceof Error ? error : new Error("Ошибка загрузки");
              failures.push(failure);
              setPendingAttachments((current) => current.map((item) =>
                item.id === draft.id
                  ? { ...item, status: "error", error: failure.message.slice(0, 120) }
                  : item
              ));
            }
          }
        };
        await Promise.all([uploadWorker(), uploadWorker()]);
        uploadedFiles = results.filter((item): item is FileArtifactRef => item !== null);
        if (threadEpochRef.current !== submitEpoch) {
          await Promise.allSettled(uploadedFiles.map((item) => deleteStagedFile(item.id)));
          return;
        }
        if (failures.length > 0) {
          await Promise.allSettled(uploadedFiles.map((item) => deleteStagedFile(item.id)));
          inFlightChatActionRef.current = null;
          lastSubmittedTextRef.current = null;
          setLoading(false);
          setMessages((prev) => prev.filter((item) => item.id !== optimisticMessageId));
          if (prefilledQuestion === undefined) setQuestion(cleanQuestion);
          addToast({ type: "error", title: "Не удалось загрузить файл", description: failures[0].message });
          return;
        }
      }

      if (uploadedFiles.length > 0) {
        setMessages((prev) => prev.map((item) =>
          item.id === optimisticMessageId ? { ...item, files: uploadedFiles } : item
        ));
      }
      setPendingAttachments([]);
      activeStreamEpochRef.current = submitEpoch;

      if (clarificationValue !== undefined) {
        await startStreaming(
          cleanQuestion,
          nextConversationId,
          selectedAgentId ?? undefined,
          clarificationId,
          clarificationValue,
          uploadedFiles.map((item) => item.id)
        );
        return;
      }

      if (uploadedFiles.length > 0) {
        await startStreaming(
          cleanQuestion,
          nextConversationId,
          selectedAgentId ?? undefined,
          clarificationId,
          undefined,
          uploadedFiles.map((item) => item.id)
        );
      } else {
        await startStreaming(
          cleanQuestion,
          nextConversationId,
          selectedAgentId ?? undefined,
          clarificationId
        );
      }
    },
    [addToast, createConversationMutation, loading, pendingAttachments, question, selectedAgentId, setActiveConversationId, startStreaming, workspaceName]
  );

  const handleRepairTurn = useCallback(
    async (messageId: string) => {
      if (feedbackPendingRef.current[messageId] || feedbackPendingByMessageId[messageId]) return;
      const assistantMessage = messages.find((message) => message.backendMessageId === messageId);
      const targetConversationId = assistantMessage?.payload?.conversation_id ?? conversationIdRef.current;
      if (!assistantMessage || !targetConversationId) return;
      const assistantIndex = messages.findIndex((message) => message.backendMessageId === messageId);
      const originalQuestion = messages
        .slice(0, assistantIndex)
        .reverse()
        .find((message) => message.role === "user")?.text;
      if (!originalQuestion) return;

      try {
        const response = await fetch(`/api/chat/messages/${messageId}/repair-turn`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message_id: messageId,
            conversation_id: targetConversationId,
            workspace_id: workspaceName,
            original_question: originalQuestion,
            original_answer: assistantMessage.text,
            generated_sql: assistantMessage.payload?.generated_sql ?? null,
            executed_sql: assistantMessage.payload?.execution?.normalized_sql ?? null,
            context_tables: assistantMessage.payload?.context_tables ?? [],
            metadata_sources: assistantMessage.payload?.metadata_sources ?? [],
            evidence: assistantMessage.payload?.evidence ?? [],
            claims: assistantMessage.payload?.claims ?? [],
            claim_evidence_links: assistantMessage.payload?.claim_evidence_links ?? [],
            tool_trace: [],
          }),
        });
        if (!response.ok) throw new Error("Запрос на исправление отклонён");
        const payload = (await response.json()) as { repair_question?: string };
        if (!payload.repair_question) throw new Error("Не получен вопрос для исправления");
        await doSubmit(payload.repair_question);
      } catch (error) {
        addToast({
          type: "error",
          title: "Не удалось исправить ответ",
          description: error instanceof Error ? error.message : "Попробуйте ещё раз.",
        });
      }
    },
    [addToast, doSubmit, feedbackPendingByMessageId, messages, workspaceName]
  );

  const handleFilesSelected = useCallback((files: File[]) => {
    setPendingAttachments((current) => {
      const next = [...current];
      for (const file of files) {
        if (next.length >= MAX_FILES_PER_TURN) break;
        const error = validateSelectedFile(file);
        if (error) {
          addToast({ type: "error", title: file.name, description: error });
          continue;
        }
        if (next.some((item) => item.file.name === file.name)) {
          addToast({ type: "warning", title: file.name, description: "Файл с таким именем уже добавлен" });
          continue;
        }
        next.push({ id: `${Date.now()}-${crypto.randomUUID()}`, file, status: "selected" });
      }
      if (next.reduce((total, item) => total + item.file.size, 0) > 50 * 1024 * 1024) {
        addToast({ type: "error", title: "Файлы слишком большие", description: "За один ход можно отправить не более 50 МиБ" });
        return current;
      }
      return next;
    });
  }, [addToast]);

  const handleSelectClarification = useCallback(
    async (clarificationId: string, choiceId: string, resolvedValue: string) => {
      // Clear the clarification request from the message
      startTransition(() => {
        setMessages((current) =>
          current.map((message) => {
            if (message.clarificationRequest?.clarification_id === clarificationId) {
              return {
                ...message,
                clarificationRequest: null,
              };
            }
            return message;
          })
        );
      });

      // Submit the clarification selection as the next turn with the clarification params
      await doSubmit(resolvedValue, clarificationId, resolvedValue);
    },
    [doSubmit]
  );

  const scenarios = useMemo(() => Array.isArray(scenariosQuery.data) ? scenariosQuery.data : [], [scenariosQuery.data]);
  const scenarioAuthoringPending = isScenarioAuthoringPending && scenarioAuthoringTarget !== null;

  const handleRunScenario = useCallback(
    async (scenarioId: string, inputs: Record<string, unknown>) => {
      if (loading || runningScenarioId || launchingScenarioId) return;
      if (scenarios.find(item => item.id === scenarioId)?.execution_kind === "workflow") {
        setLaunchingScenarioId(scenarioId);
        startScenarioLaunchTransition(() => {
          router.push(`/scenarios/${scenarioId}/run`);
        });
        return;
      }
      setScenarioCatalogOpen(false);
      setRunningScenarioId(scenarioId);
      try {
        try {
          const response = await startWorkflow(scenarioId, workspaceName, inputs, { conversation_id: conversationId });
          setActiveConversationId(response.run.conversation_id);
        } catch (error) {
          if (!(error instanceof WorkflowApiError) || error.code !== "scenario_workflows_disabled") throw error;
          const response = await runScenario(scenarioId, workspaceName, inputs, conversationId);
          if (response.conversation_id && conversationIdRef.current === conversationId) {
            setActiveConversationId(response.conversation_id);
            await refreshConversationThread(response.conversation_id, []);
          }
        }
        await queryClient.invalidateQueries({ queryKey: ["conversation-scenario-runs"] });
        await queryClient.invalidateQueries({ queryKey: conversationListQueryKey(workspaceName) });
      } catch (error) {
        addToast({ type: "error", title: "Не удалось запустить сценарий", description: error instanceof Error ? error.message : "Не удалось запустить сценарий" });
      } finally { setRunningScenarioId(null); }
    },
    [addToast, conversationId, launchingScenarioId, loading, queryClient, refreshConversationThread, router, runningScenarioId, scenarios, setActiveConversationId, workspaceName]
  );

  const handleOpenScenarioAuthoring = useCallback(
    (target: "create" | "edit", scenario: ScenarioDefinition | null) => {
      if (scenarioAuthoringPending) return;
      setScenarioAuthoringTarget(target);
      startScenarioAuthoringTransition(async () => {
        try {
          const capabilities = await queryClient.fetchQuery({
            queryKey: scenarioCapabilitiesQueryKey(workspaceName),
            queryFn: () => fetchScenarioCapabilities(workspaceName),
            staleTime: SCENARIO_CAPABILITIES_STALE_TIME_MS,
          });
          setScenarioCatalogOpen(false);
          if (capabilities.enabled || scenario?.execution_kind === "workflow") {
            router.push(scenario ? `/scenarios/${scenario.id}` : "/scenarios/new");
            return;
          }
          setEditingScenario(scenario);
          setScenarioEditorOpen(true);
        } catch {
          addToast({
            type: "error",
            title: "Редактор сценариев недоступен",
            description:
              "Не удалось загрузить список возможностей сценариев. Проверьте подключение и повторите попытку",
          });
        } finally {
          setScenarioAuthoringTarget(null);
        }
      });
    },
    [addToast, queryClient, router, scenarioAuthoringPending, workspaceName]
  );

  const handleSaveScenario = useCallback(
    async (payload: SaveScenarioPayload, scenarioId?: string) => {
      try {
        await saveScenarioMutation.mutateAsync({ payload, scenarioId });
      } catch (error) {
        addToast({
          type: "error",
          title: "Не удалось сохранить сценарий",
          description: error instanceof Error ? error.message : "Не удалось сохранить сценарий",
        });
      }
    },
    [addToast, saveScenarioMutation]
  );

  const handleArchiveScenario = useCallback(
    async (scenarioId: string) => {
      try {
        await archiveScenarioMutation.mutateAsync(scenarioId);
      } catch (error) {
        addToast({
          type: "error",
          title: "Не удалось заархивировать",
          description: error instanceof Error ? error.message : "Не удалось заархивировать сценарий",
        });
      }
    },
    [addToast, archiveScenarioMutation]
  );

  /**
   * Handles inspection of a message's result.
   * Lazy-loads execution detail if not already available.
   */
  const handleInspectResult = useCallback(
    async (messageId: string, executionId?: string | null) => {
      // First, select the message
      setSelectedResultMessageId(messageId);
      setResultPanelOpen(true);
      setPanelEverOpened(true);

      // Resolve executionId: use provided, or look up from stored mapping
      const resolvedExecutionId = executionId ?? messageExecutionIds[messageId] ?? null;

      // If we have an executionId and it's not already cached/loading, lazy-load it
      if (resolvedExecutionId && !lazyExecutionsCache[resolvedExecutionId] && !loadingExecutionIds.has(resolvedExecutionId)) {
        setLoadingExecutionIds((prev) => new Set(prev).add(resolvedExecutionId));

        try {
          const execution = await lazyLoadExecution(resolvedExecutionId);

          // Update cache
          setLazyExecutionsCache((prev) => ({
            ...prev,
            [resolvedExecutionId]: execution,
          }));

          // Update the message with the loaded execution
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== messageId) {
                return msg;
              }

              if (!msg.payload) {
                return msg;
              }

              return {
                ...msg,
                payload: {
                  ...msg.payload,
                  execution,
                  // Use stored SQL if execution didn't provide normalized_sql
                  generated_sql: msg.payload.generated_sql ?? execution?.normalized_sql ?? null,
                },
              };
            })
          );
        } catch {
          // Cache as null (unavailable) to prevent retry
          setLazyExecutionsCache((prev) => ({
            ...prev,
            [resolvedExecutionId]: null,
          }));
          addToast({
            type: "warning",
            title: "Результат выполнения недоступен",
            description: "Не удалось загрузить результат выполнения. Показываю сохранённые данные",
          });
        } finally {
          setLoadingExecutionIds((prev) => {
            const next = new Set(prev);
            next.delete(resolvedExecutionId);
            return next;
          });
        }
      }
    },
    [addToast, lazyExecutionsCache, loadingExecutionIds, messageExecutionIds]
  );

  useEffect(() => {
    if (!conversationId || loading || streamingMessage || !hasDetachedPendingTurn(messages)) {
      return;
    }

    let stopped = false;
    const poll = async () => {
      try {
        const hydratedMessages = await refreshConversationThread(conversationId, messages);
        if (!stopped && hasDetachedPendingTurn(hydratedMessages)) {
          window.setTimeout(() => {
            if (!stopped) {
              void poll();
            }
          }, 2500);
        }
      } catch {
        if (!stopped) {
          window.setTimeout(() => {
            if (!stopped) {
              void poll();
            }
          }, 5000);
        }
      }
    };

    const timeoutId = window.setTimeout(() => {
      void poll();
    }, 2500);

    return () => {
      stopped = true;
      window.clearTimeout(timeoutId);
    };
  }, [conversationId, loading, messages, refreshConversationThread, streamingMessage]);

  const displayMessages = useMemo(() => {
    const nextMessages = [...messages];
    const hasPersistedStreamingMessage = streamingMessage
      ? messages.some((message) => message.id === streamingMessage.id)
      : false;

    if (streamingMessage && !hasPersistedStreamingMessage) {
      nextMessages.push({
        id: streamingMessage.id,
        role: "assistant",
        text:
          streamingMessage.text || (streamingMessage.isStreaming ? "" : "Создание ответа отменено"),
        timestamp: streamingMessage.timestamp,
        thinkingBlocks: streamingMessage.thinkingBlocks,
        toolCalls: streamingMessage.toolCalls,
        executionStatuses: streamingMessage.executionStatuses,
        deliveryState: streamingMessage.isError
          ? "error"
          : streamingMessage.isComplete
            ? "complete"
            : streamingMessage.isStreaming
              ? "streaming"
              : "cancelled",
      });
    }

    if (
      conversationId &&
      !loading &&
      !streamingMessage &&
      hasDetachedPendingTurn(nextMessages)
    ) {
      nextMessages.push(buildDetachedPendingMessage(conversationId));
    }

    return nextMessages;
  }, [conversationId, loading, messages, streamingMessage]);

  const selectedResultMessage =
    displayMessages.find((message) => message.id === selectedResultMessageId && message.payload) ??
    [...displayMessages].reverse().find((message) => message.role === "assistant" && message.payload);
  const deferredResultPayload = useDeferredValue(selectedResultMessage?.payload);
  const hasResultPayload = Boolean(deferredResultPayload);
  const sidebarInteractionDisabled =
    openConversationMutation.isPending ||
    deleteConversationMutation.isPending ||
    createConversationMutation.isPending;
  const sidebarDeletionDisabled = sidebarInteractionDisabled || loading;
  const conversations = Array.isArray(conversationsQuery.data) ? conversationsQuery.data : [];
  const agents = Array.isArray(agentsQuery.data) ? agentsQuery.data : [];
  const activeConversationTitle =
    conversations.find((conversation) => conversation.id === conversationId)?.title ?? null;
  const agentStarPendingAgentId = setAgentStarMutation.isPending
    ? (setAgentStarMutation.variables?.agentId ?? null)
    : null;
  const agentFavoritePendingAgentId = setAgentFavoriteMutation.isPending
    ? (setAgentFavoriteMutation.variables?.agentId ?? null)
    : null;

  useEffect(() => {
    const threadLabel = conversationId
      ? activeConversationTitle?.trim() || "Чат"
      : "Новый чат";
    document.title = `${threadLabel} | Rhetorica`;
  }, [activeConversationTitle, conversationId]);
  const canCopyChatHistory = displayMessages.length > 0;
  const handleCopyChatHistory = async () => {
    await copyTextToClipboard(serializeChatHistoryForCopy(displayMessages, { userLabel }));
  };
  const handleChangeChatWidth = (width: ChatWidth) => {
    setChatWidth(width);
  };
  const toggleComposerDock = () => {
    setComposerDock((current) => (current === "bottom" ? "right" : "bottom"));
  };

  useEffect(() => {
    writeUiCookie(UI_COMPOSER_DOCK_COOKIE, composerDock);
  }, [composerDock]);

  useEffect(() => {
    writeUiCookie(UI_RESULT_PANEL_COOKIE, resultPanelOpen ? "1" : "0");
  }, [resultPanelOpen]);

  useEffect(() => {
    if (resultPanelOpen) {
      closeGuidesPanel();
    }
  }, [resultPanelOpen, closeGuidesPanel]);

  useEffect(() => {
    writeUiCookie(UI_SIDEBAR_COLLAPSED_COOKIE, sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  useEffect(() => {
    writeUiCookie(UI_SIDEBAR_WIDTH_COOKIE, String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mediaQuery = window.matchMedia("(min-width: 1280px)");
    const handleChange = () => setIsXl(mediaQuery.matches);
    handleChange();
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const grid = gridRef.current;
    const row = rowRef.current;
    const section = sectionRef.current;
    if (!grid && !row && !section) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = Math.round(entry.contentRect.width);
        const height = Math.round(entry.contentRect.height);
        if (entry.target === grid) {
          setGridWidth(width);
        } else if (entry.target === row) {
          setRowWidth(width);
        } else if (entry.target === section) {
          setSectionHeight(height);
        }
      }
    });
    if (grid) observer.observe(grid);
    if (row) observer.observe(row);
    if (section) observer.observe(section);
    return () => observer.disconnect();
  }, []);

  const resultSurfaceInFlow = panelEverOpened && hasResultPayload && isXl;
  const maxComposerWidth =
    rowWidth > 0
      ? Math.max(0, rowWidth - 100 - 64 - (resultSurfaceInFlow ? 520 : 0))
      : undefined;
  const maxComposerHeight = sectionHeight > 0 ? Math.max(0, sectionHeight - 100) : undefined;
  const maxSidebarWidth = gridWidth > 0 ? Math.max(0, gridWidth - 100) : undefined;

  useEffect(() => {
    writeUiCookie(UI_COMPOSER_DOCK_WIDTH_COOKIE, String(composerDockWidth));
  }, [composerDockWidth]);

  useEffect(() => {
    writeUiCookie(UI_COMPOSER_DOCK_HEIGHT_COOKIE, String(composerDockHeight));
  }, [composerDockHeight]);

  return (
    <main
      data-testid="chat-workspace"
      className="h-dvh min-h-0 overflow-hidden bg-[radial-gradient(circle_at_top_right,rgb(var(--color-accent)/0.18),transparent_22%),radial-gradient(circle_at_top_left,rgb(var(--color-elevated)/0.9),transparent_30%),linear-gradient(180deg,rgb(var(--color-page)),rgb(var(--color-surface))_48%,rgb(var(--color-page)))]"
    >
      <div
        ref={gridRef}
        data-testid="chat-workspace-grid"
        style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}
        className={cn(
        "mx-auto grid h-full min-h-0 grid-rows-[minmax(0,1fr)] overflow-hidden border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.94),rgb(var(--color-page)/0.98))] shadow-[0_24px_80px_rgb(var(--color-shadow)/calc(0.4*var(--shadow-opacity-scale)))]",
        sidebarCollapsed
          ? "lg:grid-cols-[72px_minmax(0,1fr)]"
          : "lg:grid-cols-[var(--sidebar-width)_minmax(0,1fr)]"
      )}>
        <Sidebar
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          collapsed={sidebarCollapsed}
          onCollapseChange={setSidebarCollapsed}
          width={sidebarWidth}
          onResizeWidth={setSidebarWidth}
          maxWidth={maxSidebarWidth}
          workspaceName={workspaceName}
          apiStatus={apiStatus}
          canAccessAdmin={canAccessAdmin}
          userLogin={currentUser.login}
          userDisplayName={currentUser.displayName}
          userRole={currentUser.role}
          userAuthProvider={currentUser.authProvider}
          conversations={conversations}
          activeConversationId={conversationId}
          historyLoading={conversationsQuery.isLoading}
          interactionDisabled={sidebarInteractionDisabled}
          deletionDisabled={sidebarDeletionDisabled}
          deletingConversationId={
            deleteConversationMutation.isPending ? deleteConversationMutation.variables : null
          }
          onNewChat={handleNewChat}
          onSelectConversation={(targetConversationId) => {
            void handleSelectConversation(targetConversationId);
          }}
          onDeleteConversation={(targetConversationId) => {
            void handleDeleteConversation(targetConversationId);
          }}
        />

        <div
          ref={rowRef}
          data-testid="chat-workspace-content"
          className="flex min-h-0 min-w-0 flex-col overflow-hidden border-l border-transparent lg:flex-row lg:border-l lg:border-border xl:border-l-0"
        >
          <section
            ref={sectionRef}
            className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
          >
            <div className="flex min-h-0 flex-1 flex-col">
              <ConversationScenarioRuns conversationId={conversationId} workspace={workspaceName} />
              <ChatThread
                chatWidth={chatWidth}
                conversationId={conversationId}
                messages={displayMessages}
                userLabel={userLabel}
                loading={loading && !streamingMessage}
                threadLoading={threadLoadingConversationId !== null}
                feedbackPendingByMessageId={feedbackPendingByMessageId}
                onFeedback={submitFeedback}
                onRepairTurn={handleRepairTurn}
                onSelectClarification={handleSelectClarification}
                onOpenScenarioCatalog={() => setScenarioCatalogOpen(true)}
                onInspectResult={handleInspectResult}
              />
            </div>

            {composerDock === "bottom" ? (
              <ChatComposer
                value={question}
                loading={loading}
                chatWidth={chatWidth}
                dock="bottom"
                onToggleDock={toggleComposerDock}
                dockWidth={composerDockWidth}
                dockHeight={composerDockHeight}
                onResizeDockWidth={setComposerDockWidth}
                onResizeDockHeight={setComposerDockHeight}
                maxDockWidth={maxComposerWidth}
                maxDockHeight={maxComposerHeight}
                agents={agents}
                selectedAgentId={selectedAgentId}
                agentLocked={Boolean(conversationId && displayMessages.length > 0)}
                onChange={setQuestion}
                onSelectAgent={setSelectedAgentId}
                onToggleAgentStar={(agent) => {
                  setAgentStarMutation.mutate({
                    agentId: agent.id,
                    enabled: !agent.is_starred,
                  });
                }}
                onToggleAgentFavorite={(agent) => {
                  setAgentFavoriteMutation.mutate({
                    agentId: agent.id,
                    enabled: !agent.is_favorite,
                  });
                }}
                agentStarPendingAgentId={agentStarPendingAgentId}
                agentFavoritePendingAgentId={agentFavoritePendingAgentId}
                onSubmit={() => {
                  void doSubmit();
                }}
                onCancel={() => {
                  cancelStreaming();
                  setLoading(false);
                }}
                attachments={pendingAttachments}
                onFilesSelected={handleFilesSelected}
                onRemoveAttachment={(attachmentId) => {
                  setPendingAttachments((current) =>
                    current.filter((item) => item.id !== attachmentId)
                  );
                }}
              />
            ) : null}
          </section>

          {composerDock === "right" ? (
            <div
              style={{ width: composerDockWidth, maxWidth: "100%" }}
              className="hidden w-full shrink-0 flex-col overflow-hidden lg:flex lg:w-auto"
            >
              <ChatComposer
                value={question}
                loading={loading}
                chatWidth={chatWidth}
                dock="right"
                onToggleDock={toggleComposerDock}
                dockWidth={composerDockWidth}
                dockHeight={composerDockHeight}
                onResizeDockWidth={setComposerDockWidth}
                onResizeDockHeight={setComposerDockHeight}
                maxDockWidth={maxComposerWidth}
                maxDockHeight={maxComposerHeight}
                agents={agents}
                selectedAgentId={selectedAgentId}
                agentLocked={Boolean(conversationId && displayMessages.length > 0)}
                onChange={setQuestion}
                onSelectAgent={setSelectedAgentId}
                onToggleAgentStar={(agent) => {
                  setAgentStarMutation.mutate({
                    agentId: agent.id,
                    enabled: !agent.is_starred,
                  });
                }}
                onToggleAgentFavorite={(agent) => {
                  setAgentFavoriteMutation.mutate({
                    agentId: agent.id,
                    enabled: !agent.is_favorite,
                  });
                }}
                agentStarPendingAgentId={agentStarPendingAgentId}
                agentFavoritePendingAgentId={agentFavoritePendingAgentId}
                onSubmit={() => {
                  void doSubmit();
                }}
                onCancel={() => {
                  cancelStreaming();
                  setLoading(false);
                }}
                attachments={pendingAttachments}
                onFilesSelected={handleFilesSelected}
                onRemoveAttachment={(attachmentId) => {
                  setPendingAttachments((current) =>
                    current.filter((item) => item.id !== attachmentId)
                  );
                }}
              />
            </div>
          ) : null}

          <ChatTools
            canCopyChatHistory={canCopyChatHistory}
            chatWidth={chatWidth}
            hasResultPayload={hasResultPayload}
            isResultPanelOpen={resultPanelOpen}
            composerDock={composerDock}
            onChangeChatWidth={handleChangeChatWidth}
            onCopyChatHistory={handleCopyChatHistory}
            onToggleSidebar={() => setSidebarOpen((value) => !value)}
            onToggleComposerDock={toggleComposerDock}
            onToggleResultPanel={() => {
              if (!selectedResultMessageId && selectedResultMessage?.id) {
                setSelectedResultMessageId(selectedResultMessage.id);
              }
              setPanelEverOpened(true);
              setResultPanelOpen((value) => !value);
            }}
          />

          {guidesPanelOpen && !resultPanelOpen ? (
            <GuidesSurface onClose={closeGuidesPanel} />
          ) : null}

          {panelEverOpened && hasResultPayload ? (
            <Suspense fallback={<ResultSurfaceFallback />}>
              <LazyResultSurface
                payload={deferredResultPayload}
                recentExecutions={recentExecutions}
                isOpen={resultPanelOpen}
                executionLoading={loadingExecutionIds.size > 0}
                onClose={() => setResultPanelOpen(false)}
              />
            </Suspense>
          ) : null}
        </div>
      </div>

      <ScenarioEditorModal
        isOpen={scenarioEditorOpen}
        workspaceName={workspaceName}
        scope="personal"
        saving={saveScenarioMutation.isPending}
        scenario={editingScenario}
        onClose={() => {
          setScenarioEditorOpen(false);
          setEditingScenario(null);
        }}
        onSubmit={(payload, scenarioId) => {
          void handleSaveScenario(payload, scenarioId);
          setScenarioEditorOpen(false);
          setEditingScenario(null);
        }}
      />

      <ScenarioCatalogModal
        isOpen={scenarioCatalogOpen}
        scenarios={scenarios}
        loading={scenariosQuery.isLoading}
        runningScenarioId={runningScenarioId}
        launchingScenarioId={launchingScenarioId}
        authoringTarget={scenarioAuthoringTarget}
        authoringPending={scenarioAuthoringPending}
        runDisabled={loading || Boolean(streamingMessage)}
        saving={saveScenarioMutation.isPending}
        archivingScenarioId={
          archiveScenarioMutation.isPending ? archiveScenarioMutation.variables : null
        }
        onClose={() => setScenarioCatalogOpen(false)}
        onRunScenario={(scenarioId, inputs) => {
          void handleRunScenario(scenarioId, inputs);
        }}
        onCreateScenario={() => {
          handleOpenScenarioAuthoring("create", null);
        }}
        onEditScenario={(scenario) => {
          handleOpenScenarioAuthoring("edit", scenario);
        }}
        onArchiveScenario={(scenarioId) => {
          void handleArchiveScenario(scenarioId);
        }}
      />

      <AppTour />
    </main>
  );
}
