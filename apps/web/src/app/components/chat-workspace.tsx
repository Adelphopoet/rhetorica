"use client";

import {
  lazy,
  startTransition,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Skeleton, useToast } from "@rhetorica/ui";

import {
  buildChatAskResponse,
  resolveDeliveryState,
  type ChatAskResponse,
  type ChatFeedbackValue,
  type ChatMessageViewModel,
  type ExecutionHistoryItem,
} from "@/features/chat/chatViewModel";
import { copyTextToClipboard, serializeChatHistoryForCopy } from "@/features/chat/chatCopy";
import {
  approveAnalysisRun,
  conversationListQueryKey,
  cancelAnalysisRun,
  createConversation,
  deriveConversationTitle,
  getAnalysisRunState,
  cacheMessageIntermediateSteps,
  lazyLoadExecution,
  listConversations,
  loadConversationThread,
  mergeHydratedMessages,
  removeConversation,
  resumeAnalysisRun,
  type AnalysisRunState,
  type ConversationSummary,
} from "@/features/chat/historyApi";
import {
  archiveScenario,
  createScenario,
  listScenarios,
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
import { ChatComposer } from "./chat-composer";
import { ScenarioEditorModal } from "./scenario-editor-modal";
import { ScenarioCatalogModal } from "./scenario-catalog-modal";
import { ChatThread } from "./chat-thread";
import { Header } from "./header";
import { Sidebar } from "./sidebar";

const LazyResultSurface = lazy(async () => ({
  default: (await import("./result-surface")).ResultSurface,
}));

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
};

function getInitialMessages(): ChatMessageViewModel[] {
  return [];
}

function mergeAnalysisRunState(
  messages: ChatMessageViewModel[],
  state: AnalysisRunState | null
): ChatMessageViewModel[] {
  if (!state?.analysis_plan && !state?.checkpoint_digest) {
    return messages;
  }
  const runtimePayload: ChatAskResponse = {
    answer: "",
    conversation_id: state.conversation_id,
    generated_sql: null,
    optimization_trace: null,
    capability_trace: null,
    selected_database_connector: null,
    assistant_message_id: null,
    metadata_sources: [],
    context_tables: [],
    claims: [],
    evidence: [],
    claim_evidence_links: [],
    uncertainty_flags: [],
    execution: null,
    analysis_document: null,
    visualization: null,
    analysis_plan: state.analysis_plan,
    worker_results: state.worker_results,
    checkpoint_digest: state.checkpoint_digest,
    warnings: [],
  };
  const targetIndex = [...messages]
    .map((message, index) => ({ message, index }))
    .reverse()
    .find(({ message }) => message.role === "assistant")?.index;
  if (typeof targetIndex !== "number") {
    return [
      ...messages,
      {
        id: `analysis-run-${state.conversation_id}`,
        role: "assistant",
        text: "",
        payload: runtimePayload,
        timestamp: new Date(),
        deliveryState: state.status === "running" ? "streaming" : "idle",
      },
    ];
  }
  return messages.map((message, index) => {
    if (index !== targetIndex) {
      return message;
    }
    const payload = message.payload ?? runtimePayload;
    return {
      ...message,
      payload: {
        ...payload,
        analysis_plan: state.analysis_plan ?? payload.analysis_plan ?? null,
        worker_results: state.worker_results.length > 0
          ? state.worker_results
          : payload.worker_results ?? [],
        checkpoint_digest: state.checkpoint_digest ?? payload.checkpoint_digest ?? null,
      },
    };
  });
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
    <aside className="fixed inset-y-3 right-3 z-40 flex w-full max-w-[min(100vw-24px,560px)] flex-col rounded-[28px] border border-[#172235] bg-[#07101d]/94 p-6 shadow-[-24px_0_80px_rgba(0,0,0,0.45)] xl:static xl:inset-auto xl:z-0 xl:h-full xl:w-[520px] xl:max-w-none xl:rounded-none xl:rounded-r-[32px] xl:border-y-0 xl:border-r-0 xl:shadow-none">
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
}: ChatWorkspaceProps) {
  const { addToast } = useToast();
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageViewModel[]>(getInitialMessages);
  const [feedbackPendingByMessageId, setFeedbackPendingByMessageId] = useState<
    Record<string, boolean>
  >({});
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [resultPanelOpen, setResultPanelOpen] = useState(false);
  const [selectedResultMessageId, setSelectedResultMessageId] = useState<string | null>(null);
  const [runningScenarioId, setRunningScenarioId] = useState<string | null>(null);
  const [scenarioEditorOpen, setScenarioEditorOpen] = useState(false);
  const [scenarioCatalogOpen, setScenarioCatalogOpen] = useState(false);
  const [editingScenario, setEditingScenario] = useState<ScenarioDefinition | null>(null);
  // Cache for lazy-loaded executions: executionId -> ExecuteSqlResponse
  const [lazyExecutionsCache, setLazyExecutionsCache] = useState<Record<string, import("@/features/chat/chatViewModel").ExecuteSqlResponse | null>>({});
  // Currently loading execution IDs
  const [loadingExecutionIds, setLoadingExecutionIds] = useState<Set<string>>(new Set());
  // Mapping of messageId -> executionId for assistant messages (for lazy loading)
  const [messageExecutionIds, setMessageExecutionIds] = useState<Record<string, string>>({});
  const feedbackRequestVersionRef = useRef<Record<string, number>>({});
  const clearStreamingRef = useRef<() => void>(() => undefined);
  const conversationsQueryKey = conversationListQueryKey(workspaceName);
  const scenariosQueryKey = scenarioListQueryKey(workspaceName);

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
  const userLabel = currentUser.displayName?.trim() || currentUser.login;

  const createConversationMutation = useMutation({
    mutationFn: (title: string) => createConversation(workspaceName, title),
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
        title: "Delete failed",
        description: error instanceof Error ? error.message : "Conversation could not be deleted.",
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
        setConversationId(doneEvent.conversation_id);
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
      setTimeout(() => clearStreamingRef.current(), 0);
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
    },
    [conversationsQueryKey, queryClient]
  );

  const handleStreamingError = useCallback(
    (
      streamingMessage: StreamingMessage | null,
      errorEvent: ErrorEvent,
      intermediateSteps: IntermediateSteps
    ) => {
      if (streamingMessage) {
        const errorMessage: ChatMessageViewModel = {
          id: streamingMessage.id,
          role: "assistant",
          text: streamingMessage.text || `Error: ${errorEvent.message}`,
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
      addToast({
        type: "error",
        title: "Streaming failed",
        description: errorEvent.message,
      });
      setTimeout(() => clearStreamingRef.current(), 0);
    },
    [addToast]
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

  const resetThreadState = useCallback(
    (nextConversationId: string | null) => {
      clearStreaming();
      setConversationId(nextConversationId);
      setQuestion("");
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
    [clearStreaming]
  );

  const handleNewChat = useCallback(() => {
    resetThreadState(null);
  }, [resetThreadState]);

  const handleSelectConversation = useCallback(
    async (targetConversationId: string) => {
      if (
        loading ||
        createConversationMutation.isPending ||
        deleteConversationMutation.isPending
      ) {
        return;
      }

      const previousConversationId = conversationId;
      const previousMessages = messages;
      const previousResultPanelOpen = resultPanelOpen;

      resetThreadState(targetConversationId);

      try {
        const thread = await openConversationMutation.mutateAsync(targetConversationId);
        const runState = await getAnalysisRunState(targetConversationId);
        const hydratedMessages = mergeAnalysisRunState(
          mergeHydratedMessages(previousMessages, thread.messages),
          runState
        );

        // Build messageId -> executionId mapping from thread data
        const newMessageExecutionIds: Record<string, string> = {};
        for (const { messageId, executionId } of thread.assistantExecutionIds) {
          newMessageExecutionIds[messageId] = executionId;
        }

        startTransition(() => {
          setMessages(hydratedMessages);
          setFeedbackPendingByMessageId({});
        });
        setMessageExecutionIds(newMessageExecutionIds);
        setResultPanelOpen(false);
        setSelectedResultMessageId(
          [...hydratedMessages].reverse().find((message) => message.role === "assistant" && message.payload)?.id ?? null
        );
      } catch (error) {
        setConversationId(previousConversationId);
        setResultPanelOpen(previousResultPanelOpen);
        startTransition(() => {
          setMessages(previousMessages);
        });
        addToast({
          type: "error",
          title: "History load failed",
          description: error instanceof Error ? error.message : "Conversation could not be loaded.",
        });
      }
    },
    [
      addToast,
      conversationId,
      createConversationMutation.isPending,
      deleteConversationMutation.isPending,
      loading,
      messages,
      openConversationMutation,
      resetThreadState,
      resultPanelOpen,
    ]
  );

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
        if (targetConversationId === conversationId) {
          resetThreadState(null);
        }
      } catch {
        return;
      }
    },
    [
      conversationId,
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
      messageConversationId: string | null | undefined
    ) => {
      const targetConversationId = messageConversationId ?? conversationId;
      if (!targetConversationId) {
        return;
      }

      const previousFeedback =
        messages.find((message) => message.backendMessageId === messageId)?.feedback ?? null;

      startTransition(() => {
        setMessages((prev) =>
          prev.map((message) =>
            message.backendMessageId === messageId ? { ...message, feedback } : message
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

        setFeedbackPendingByMessageId((prev) => ({ ...prev, [messageId]: false }));
        if (shouldRollback) {
          startTransition(() => {
            setMessages((prev) =>
              prev.map((message) =>
                message.backendMessageId === messageId
                  ? { ...message, feedback: previousFeedback }
                  : message
              )
            );
          });
          addToast({
            type: "warning",
            title: "Feedback was not saved",
            description: "The local selection was rolled back.",
          });
        }
      }
    },
    [addToast, conversationId, messages, workspaceName]
  );

  const doSubmit = useCallback(
    async (
      prefilledQuestion?: string,
      clarificationId?: string,
      clarificationValue?: string
    ) => {
      const cleanQuestion = (prefilledQuestion ?? question).trim();
      if (!cleanQuestion || loading) {
        return;
      }

      const userMessage: ChatMessageViewModel = {
        id: `user-${Date.now()}`,
        role: "user",
        text: cleanQuestion,
        timestamp: new Date(),
        deliveryState: "complete",
      };

      startTransition(() => {
        setMessages((prev) => [...prev, userMessage]);
      });
      setQuestion("");
      setLoading(true);

      let nextConversationId = conversationId ?? undefined;
      if (!nextConversationId) {
        try {
          const createdConversation = await createConversationMutation.mutateAsync(
            deriveConversationTitle(cleanQuestion)
          );
          nextConversationId = createdConversation.id;
          setConversationId(createdConversation.id);
        } catch {
          nextConversationId = undefined;
        }
      }

      await startStreaming(cleanQuestion, nextConversationId, clarificationId, clarificationValue);
    },
    [conversationId, createConversationMutation, loading, question, startStreaming]
  );

  const handleResumeAnalysis = useCallback(async () => {
    if (!conversationId || loading) {
      return;
    }
    try {
      const resumed = await resumeAnalysisRun(conversationId);
      const latestState = await getAnalysisRunState(conversationId);
      startTransition(() => {
        setMessages((current) => mergeAnalysisRunState(current, latestState ?? resumed));
      });
      await doSubmit("Продолжи анализ с текущего чекпоинта");
    } catch (error) {
      addToast({
        type: "error",
        title: "Resume failed",
        description: error instanceof Error ? error.message : "Analysis run could not resume.",
      });
    }
  }, [addToast, conversationId, doSubmit, loading]);

  const handleApproveAnalysis = useCallback(async () => {
    if (!conversationId || loading) {
      return;
    }
    try {
      const approved = await approveAnalysisRun(conversationId);
      const latestState = await getAnalysisRunState(conversationId);
      startTransition(() => {
        setMessages((current) => mergeAnalysisRunState(current, latestState ?? approved));
      });
      await doSubmit("Продолжи анализ: SQL execution approved");
    } catch (error) {
      addToast({
        type: "error",
        title: "Approval failed",
        description: error instanceof Error ? error.message : "Analysis run could not be approved.",
      });
    }
  }, [addToast, conversationId, doSubmit, loading]);

  const handleCancelAnalysis = useCallback(async () => {
    if (!conversationId) {
      return;
    }
    try {
      const cancelled = await cancelAnalysisRun(conversationId);
      const latestState = await getAnalysisRunState(conversationId);
      startTransition(() => {
        setMessages((current) => mergeAnalysisRunState(current, latestState ?? cancelled));
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Cancel failed",
        description: error instanceof Error ? error.message : "Analysis run could not be cancelled.",
      });
    }
  }, [addToast, conversationId]);

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

  const handleRunScenario = useCallback(
    async (scenarioId: string, inputs: Record<string, unknown>, renderedPrompt: string) => {
      if (loading || runningScenarioId) {
        return;
      }
      setRunningScenarioId(scenarioId);
      try {
        await doSubmit(renderedPrompt);
      } catch (error) {
        addToast({
          type: "error",
          title: "Scenario run failed",
          description: error instanceof Error ? error.message : "Scenario could not be started.",
        });
      } finally {
        setLoading(false);
        setRunningScenarioId(null);
      }
    },
    [
      addToast,
      doSubmit,
      loading,
      runningScenarioId,
    ]
  );

  const handleSaveScenario = useCallback(
    async (payload: SaveScenarioPayload, scenarioId?: string) => {
      try {
        await saveScenarioMutation.mutateAsync({ payload, scenarioId });
      } catch (error) {
        addToast({
          type: "error",
          title: "Scenario save failed",
          description: error instanceof Error ? error.message : "Scenario could not be saved.",
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
          title: "Archive failed",
          description: error instanceof Error ? error.message : "Scenario could not be archived.",
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
            title: "Execution unavailable",
            description: "The execution result could not be loaded. Showing stored data instead.",
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

  const displayMessages = [...messages];
  const hasPersistedStreamingMessage = streamingMessage
    ? messages.some((message) => message.id === streamingMessage.id)
    : false;

  if (streamingMessage && !hasPersistedStreamingMessage) {
    displayMessages.push({
      id: streamingMessage.id,
      role: "assistant",
      text: streamingMessage.text || (streamingMessage.isStreaming ? "" : "Generation cancelled"),
      timestamp: streamingMessage.timestamp,
      thinkingBlocks: streamingMessage.thinkingBlocks,
      toolCalls: streamingMessage.toolCalls,
      executionStatuses: streamingMessage.executionStatuses,
      analysisPlan: streamingMessage.analysisPlan ?? null,
      checkpointDigest: streamingMessage.checkpointDigest ?? null,
      deliveryState: streamingMessage.isError
        ? "error"
        : streamingMessage.isComplete
          ? "complete"
          : streamingMessage.isStreaming
            ? "streaming"
            : "cancelled",
    });
  }

  const selectedResultMessage =
    displayMessages.find((message) => message.id === selectedResultMessageId && message.payload) ??
    [...displayMessages].reverse().find((message) => message.role === "assistant" && message.payload);
  const deferredResultPayload = useDeferredValue(selectedResultMessage?.payload);
  const hasResultPayload = Boolean(deferredResultPayload);
  const sidebarInteractionDisabled =
    loading ||
    openConversationMutation.isPending ||
    deleteConversationMutation.isPending ||
    createConversationMutation.isPending;
  const conversations = Array.isArray(conversationsQuery.data) ? conversationsQuery.data : [];
  const scenarios = Array.isArray(scenariosQuery.data) ? scenariosQuery.data : [];
  const activeConversationTitle =
    conversations.find((conversation) => conversation.id === conversationId)?.title ?? null;
  const canCopyChatHistory = displayMessages.length > 0;
  const workspaceMode = hasResultPayload
    ? resultPanelOpen
      ? "result-open"
      : "active"
    : displayMessages.length > 0
      ? "active"
      : "empty";
  const trustLabel =
    workspaceMode === "empty"
      ? "Governed analytics with visible SQL, guarded execution, and explainable evidence."
      : workspaceMode === "result-open"
        ? "Inspection rail is live: result, SQL, evidence, and execution stay together."
        : "Keep the answer moving while the trust trail stays attached.";
  const handleCopyChatHistory = async () => {
    await copyTextToClipboard(serializeChatHistoryForCopy(displayMessages, { userLabel }));
  };

  return (
    <main className="h-screen overflow-hidden bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.18),transparent_22%),radial-gradient(circle_at_top_left,rgba(18,26,42,0.9),transparent_30%),linear-gradient(180deg,#04070d,#09111d_48%,#060910)] px-3 py-3 sm:px-4 sm:py-4">
      <div className="mx-auto grid h-[calc(100vh-24px)] max-w-[1800px] overflow-hidden rounded-[32px] border border-[#142033] bg-[linear-gradient(180deg,rgba(8,12,21,0.94),rgba(7,11,19,0.98))] shadow-[0_24px_80px_rgba(0,0,0,0.4)] lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)]">
        <Sidebar
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          workspaceName={workspaceName}
          canAccessAdmin={canAccessAdmin}
          userLogin={currentUser.login}
          userDisplayName={currentUser.displayName}
          userRole={currentUser.role}
          userAuthProvider={currentUser.authProvider}
          conversations={conversations}
          activeConversationId={conversationId}
          historyLoading={conversationsQuery.isLoading}
          interactionDisabled={sidebarInteractionDisabled}
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

        <div className="flex min-w-0 overflow-hidden border-l border-transparent lg:border-l lg:border-[#142033] xl:border-l-0">
          <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <Header
              workspaceName={workspaceName}
              conversationTitle={activeConversationTitle}
              trustLabel={trustLabel}
              apiStatus={apiStatus}
              canCopyChatHistory={canCopyChatHistory}
              hasResultPayload={hasResultPayload}
              isResultPanelOpen={resultPanelOpen}
              onCopyChatHistory={handleCopyChatHistory}
              onToggleSidebar={() => setSidebarOpen((value) => !value)}
              onToggleResultPanel={() => {
                if (!selectedResultMessageId && selectedResultMessage?.id) {
                  setSelectedResultMessageId(selectedResultMessage.id);
                }
                setResultPanelOpen((value) => !value);
              }}
            />

            <div className="flex min-h-0 flex-1 flex-col">
              <ChatThread
                messages={displayMessages}
                userLabel={userLabel}
                loading={loading && !streamingMessage}
                scenarioCount={scenarios.length}
                feedbackPendingByMessageId={feedbackPendingByMessageId}
                onFeedback={submitFeedback}
                onApproveAnalysis={handleApproveAnalysis}
                onResumeAnalysis={handleResumeAnalysis}
                onCancelAnalysis={handleCancelAnalysis}
                onSelectClarification={handleSelectClarification}
                onOpenScenarioCatalog={() => setScenarioCatalogOpen(true)}
                onInspectResult={handleInspectResult}
              />

              <ChatComposer
                value={question}
                loading={loading}
                onChange={setQuestion}
                onSubmit={() => {
                  void doSubmit();
                }}
                onCancel={() => {
                  cancelStreaming();
                  if (conversationId) {
                    void handleCancelAnalysis();
                  }
                  setLoading(false);
                }}
              />
            </div>
          </section>

          {hasResultPayload ? (
            <Suspense fallback={<ResultSurfaceFallback />}>
              <LazyResultSurface
                payload={deferredResultPayload}
                recentExecutions={recentExecutions}
                isOpen={hasResultPayload && resultPanelOpen}
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
        saving={saveScenarioMutation.isPending}
        archivingScenarioId={
          archiveScenarioMutation.isPending ? archiveScenarioMutation.variables : null
        }
        onClose={() => setScenarioCatalogOpen(false)}
        onRunScenario={(scenarioId, inputs, renderedPrompt) => {
          setScenarioCatalogOpen(false);
          void handleRunScenario(scenarioId, inputs, renderedPrompt);
        }}
        onCreateScenario={() => {
          setScenarioCatalogOpen(false);
          setEditingScenario(null);
          setScenarioEditorOpen(true);
        }}
        onEditScenario={(scenario) => {
          setScenarioCatalogOpen(false);
          setEditingScenario(scenario);
          setScenarioEditorOpen(true);
        }}
        onArchiveScenario={(scenarioId) => {
          void handleArchiveScenario(scenarioId);
        }}
      />
    </main>
  );
}
