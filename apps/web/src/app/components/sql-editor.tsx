"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ThemedEditor as Editor } from "@/app/components/themed-editor";
import { Button, Badge, Tooltip } from "@rhetorica/ui";
import { Copy, Check, Maximize2, Minimize2, Code2 } from "lucide-react";
import { cn } from "@rhetorica/ui";

interface SQLEditorProps {
  sql: string;
  engine?: string;
  className?: string;
}

export function SQLEditor({ sql, engine = "SQL", className }: SQLEditorProps) {
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className={cn(
        "border border-border-strong rounded-xl overflow-hidden bg-code",
        className
      )}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-surface">
        <div className="flex items-center gap-2">
          <Code2 className="h-4 w-4 text-muted" />
          <span className="text-xs font-medium text-secondary uppercase tracking-wider">
            Сформированный SQL
          </span>
          <Badge variant="info" className="ml-2">
            {engine}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Tooltip content={copied ? "Скопировано!" : "Копировать SQL"} side="top">
            <Button
              variant="ghost"
              size="icon"
              onClick={handleCopy}
              className="h-8 w-8"
            >
              {copied ? (
                <Check className="h-4 w-4 text-success" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </Button>
          </Tooltip>
          <Tooltip content={isExpanded ? "Свернуть" : "Развернуть"} side="top">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsExpanded(!isExpanded)}
              className="h-8 w-8"
            >
              {isExpanded ? (
                <Minimize2 className="h-4 w-4" />
              ) : (
                <Maximize2 className="h-4 w-4" />
              )}
            </Button>
          </Tooltip>
        </div>
      </div>
      <div className={cn(isExpanded ? "h-96" : "h-48")}>
        <Editor
          language="sql"
          value={sql || "-- SQL-запрос ещё не сформирован"}

          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            automaticLayout: true,
            padding: { top: 12, bottom: 12 },
            renderWhitespace: "none",
            wordWrap: "on",
          }}
        />
      </div>
    </motion.div>
  );
}
