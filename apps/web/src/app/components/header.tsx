"use client";

import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Badge, Button, useToast } from "@rhetorica/ui";
import { Check, Copy, Menu, PanelRight, Wifi, WifiOff } from "lucide-react";

interface HeaderProps {
  workspaceName: string;
  conversationTitle?: string | null;
  trustLabel: string;
  apiStatus: string;
  canCopyChatHistory: boolean;
  hasResultPayload: boolean;
  isResultPanelOpen: boolean;
  onCopyChatHistory: () => Promise<void>;
  onToggleSidebar: () => void;
  onToggleResultPanel: () => void;
}

export function Header({
  workspaceName,
  conversationTitle,
  apiStatus,
  canCopyChatHistory,
  hasResultPayload,
  isResultPanelOpen,
  onCopyChatHistory,
  onToggleSidebar,
  onToggleResultPanel,
}: HeaderProps) {
  const { addToast } = useToast();
  const [copiedHistory, setCopiedHistory] = useState(false);
  const isOnline = apiStatus !== "offline";
  const shouldReduceMotion = useReducedMotion();

  const handleCopyHistory = async () => {
    try {
      await onCopyChatHistory();
      setCopiedHistory(true);
      window.setTimeout(() => setCopiedHistory(false), 2000);
    } catch (error) {
      addToast({
        type: "error",
        title: "Copy failed",
        description: error instanceof Error ? error.message : "Chat history could not be copied.",
      });
    }
  };

  return (
    <motion.header
      initial={shouldReduceMotion ? { opacity: 0 } : { y: -12, opacity: 0 }}
      animate={shouldReduceMotion ? { opacity: 1 } : { y: 0, opacity: 1 }}
      transition={{ duration: 0.25 }}
      className="border-b border-[#172338] bg-[linear-gradient(180deg,rgba(8,12,21,0.95),rgba(8,12,21,0.82))] px-4 py-2.5 backdrop-blur-xl sm:px-5 xl:px-6"
    >
      <div className="mx-auto flex max-w-[1600px] flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-2 lg:hidden">
            <Button type="button" variant="ghost" size="icon" onClick={onToggleSidebar}>
              <Menu className="h-4 w-4" />
            </Button>
            {hasResultPayload ? (
              <Button type="button" variant="ghost" size="icon" onClick={onToggleResultPanel}>
                <PanelRight className="h-4 w-4" />
              </Button>
            ) : null}
          </div>

          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="default" className="px-2.5 py-1 text-[#dce8fb]">
                {workspaceName}
              </Badge>
              <p className="truncate text-sm font-medium text-[#eef4ff]">
                {conversationTitle ?? "New analysis"}
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 xl:justify-end">
          <div className="flex max-w-full flex-wrap items-center justify-end gap-2">
            {canCopyChatHistory ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  void handleCopyHistory();
                }}
                className="gap-2 whitespace-nowrap px-3 text-[#d7e6ff] hover:bg-[#132033] hover:text-white"
              >
                {copiedHistory ? (
                  <Check className="h-4 w-4 text-[#38c67d]" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">{copiedHistory ? "Copied" : "Copy chat"}</span>
                <span className="sm:hidden">{copiedHistory ? "Copied" : "Copy"}</span>
              </Button>
            ) : null}
            <Badge variant={isOnline ? "success" : "danger"} className="gap-1.5 px-3 py-1.5">
              {isOnline ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
              API {apiStatus}
            </Badge>
            <AnimatePresence initial={false}>
              {hasResultPayload && !isResultPanelOpen ? (
                <motion.div
                  key="open-result-panel"
                  initial={{ opacity: 0, x: 10, scale: 0.96 }}
                  animate={{ opacity: 1, x: 0, scale: 1 }}
                  exit={{ opacity: 0, x: 10, scale: 0.96 }}
                  transition={{ duration: 0.16, ease: "easeOut" }}
                >
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="Open result panel"
                    onClick={onToggleResultPanel}
                    className="gap-2 whitespace-nowrap px-3 text-[#d7e6ff] hover:bg-[#132033] hover:text-white"
                  >
                    <PanelRight className="h-4 w-4" />
                    Open
                  </Button>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </div>
      </div>
    </motion.header>
  );
}
