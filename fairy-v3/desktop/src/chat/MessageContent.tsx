import { Copy } from "lucide-react";
import { isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MessageContentProps {
  content: string;
  taskId: string;
  onCopy(taskId: string, content: string): Promise<void>;
  onOpenLink(taskId: string, url: string): Promise<void>;
}

export function MessageContent({
  content,
  taskId,
  onCopy,
  onOpenLink,
}: MessageContentProps) {
  return (
    <div className="message-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) =>
            isSafeLink(href) ? (
              <button
                type="button"
                className="message-link"
                title={href}
                onClick={() => void onOpenLink(taskId, href)}
              >
                {children}
              </button>
            ) : (
              <span>{children}</span>
            ),
          pre: ({ children }) => {
            const code = nodeText(children).replace(/\n$/, "");
            return (
              <div className="message-code-block">
                <button
                  type="button"
                  aria-label="Copy code"
                  title="Copy code"
                  onClick={() => void onCopy(taskId, code)}
                >
                  <Copy size={13} />
                </button>
                <pre>{children}</pre>
              </div>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function isSafeLink(value: string | undefined): value is string {
  if (value === undefined) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.username === "" && url.password === "";
  } catch {
    return false;
  }
}

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) {
    return nodeText((node.props as { children?: ReactNode }).children);
  }
  return "";
}
