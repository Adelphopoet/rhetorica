import { MarkdownRenderer } from "./markdown-renderer";

interface MarkdownMessageProps {
  text: string;
  className?: string;
}

export function MarkdownMessage({ text, className }: MarkdownMessageProps) {
  return <MarkdownRenderer text={text} className={className} />;
}
