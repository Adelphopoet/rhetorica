"use client";

import { motion } from "framer-motion";
import { Bot } from "lucide-react";
import { Avatar } from "@rhetorica/ui";

export function TypingIndicator() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.2 }}
      className="flex gap-3 p-4 rounded-xl border bg-surface/80 border-border"
    >
      <Avatar fallback="AI" size="md" className="bg-success/20">
        <Bot className="h-5 w-5 text-success" />
      </Avatar>
      <div className="flex items-center gap-1">
        <motion.div
          className="w-2 h-2 rounded-full bg-accent"
          animate={{ scale: [1, 1.2, 1] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: 0 }}
        />
        <motion.div
          className="w-2 h-2 rounded-full bg-accent"
          animate={{ scale: [1, 1.2, 1] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: 0.2 }}
        />
        <motion.div
          className="w-2 h-2 rounded-full bg-accent"
          animate={{ scale: [1, 1.2, 1] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: 0.4 }}
        />
      </div>
    </motion.div>
  );
}
