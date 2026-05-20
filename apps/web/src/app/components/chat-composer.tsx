"use client";

import type { FormEvent } from "react";
import { Button, Textarea } from "@rhetorica/ui";
import { LoaderCircle, Send, Square } from "lucide-react";

interface ChatComposerProps {
  value: string;
  loading: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

export function ChatComposer({
  value,
  loading,
  onChange,
  onSubmit,
  onCancel,
}: ChatComposerProps) {
  const submitDisabled = loading || !value.trim();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <div className="shrink-0 border-t border-[#19253a] bg-[linear-gradient(180deg,rgba(5,8,14,0.14),rgba(7,11,19,0.95)_24%,rgba(7,11,19,0.985))] px-4 pb-4 pt-3 backdrop-blur-xl sm:px-5 xl:px-6">
      <form onSubmit={handleSubmit} className="mx-auto max-w-4xl">
        <div className="rounded-[24px] border border-[#22324d] bg-[linear-gradient(180deg,rgba(11,18,31,0.98),rgba(8,13,24,0.99))] p-3 shadow-[0_18px_50px_rgba(0,0,0,0.28)] sm:p-3.5">
          <Textarea
            value={value}
            onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)}
            placeholder="Ask a metric, trend, anomaly, or root-cause question. Rhetorica will keep the SQL and execution trail visible."
            className="min-h-[84px] resize-none border-0 bg-transparent px-1 py-1 text-[15px] leading-7 text-[#edf3ff] placeholder:text-[#7284a4] focus:ring-0"
            disabled={loading}
            onKeyDown={(event: React.KeyboardEvent<HTMLTextAreaElement>) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSubmit();
              }
            }}
          />

          <div className="mt-2 flex items-center justify-end gap-2 border-t border-[#18243a] pt-2.5">
            <div className="mr-auto text-[11px] text-[#6f84a6]">
              Enter to send
            </div>
            <div className="flex items-center justify-end gap-2">
              {loading ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={onCancel}
                  className="gap-2"
                >
                  <Square className="h-4 w-4" />
                  Cancel
                </Button>
              ) : null}
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={submitDisabled}
                className="min-w-[132px] gap-2"
              >
                {loading ? (
                  <>
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                    Running
                  </>
                ) : (
                  <>
                    <Send className="h-4 w-4" />
                    Ask Rhetorica
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
}
