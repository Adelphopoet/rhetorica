"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Skeleton, cn } from "@rhetorica/ui";
import type { ChatWidth } from "./chat-tools";

type ChatThreadSkeletonProps = {
  chatWidth: ChatWidth;
};

function MessageRowSkeleton({ align, widths }: { align: "start" | "end"; widths: string[] }) {
  return (
    <div className={cn("flex w-full", align === "end" ? "justify-end" : "justify-start")}>
      <div className={cn("flex w-full max-w-[86%] flex-col gap-3", align === "end" && "items-end")}>
        {widths.map((width) => (
          <Skeleton key={width} className={cn("h-4", width)} />
        ))}
      </div>
    </div>
  );
}

export function ChatThreadSkeleton({ chatWidth }: ChatThreadSkeletonProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <div
      data-testid="chat-thread-skeleton"
      aria-busy="true"
      aria-label="Загрузка диалога"
      role="status"
      className="chat-thread flex-1 overflow-auto px-4 pb-5 pt-4 sm:px-5 xl:px-6"
    >
      <motion.div
        initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
        className={cn(
          "mx-auto flex w-full flex-col gap-6",
          chatWidth === 1100 && "max-w-[1100px]",
          chatWidth === 1600 && "max-w-[1600px]"
        )}
      >
        <MessageRowSkeleton align="end" widths={["w-2/5", "w-1/4"]} />
        <MessageRowSkeleton align="start" widths={["w-11/12", "w-4/5", "w-2/3"]} />
        <MessageRowSkeleton align="end" widths={["w-1/3"]} />
        <MessageRowSkeleton align="start" widths={["w-5/6", "w-3/5"]} />
      </motion.div>
    </div>
  );
}
