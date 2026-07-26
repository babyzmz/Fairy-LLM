import {
  m,
  type MotionValue,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "motion/react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type { Message } from "../core/client";

const EXCERPT_LENGTH = 48;
const IDLE_POINTER_INDEX = Number.POSITIVE_INFINITY;
const FOLLOW_RESUME_DELAY_MS = 400;
const ITEM_HEIGHT = 28;
const SCROLLER_PADDING = 6;

export interface MessageLineItem {
  key: string;
  role: "user" | "assistant";
  label: "You" | "Fairy";
  excerpt: string;
  streaming: boolean;
}

interface MessageLineSidebarProps {
  items: readonly MessageLineItem[];
  activeKey: string | null;
  onNavigate(key: string, smooth: boolean): void;
}

export function MessageLineSidebar({
  items,
  activeKey,
  onNavigate,
}: MessageLineSidebarProps) {
  const reducedMotion = useReducedMotion() ?? false;
  const pointerIndex = useMotionValue(IDLE_POINTER_INDEX);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef(new Map<string, HTMLButtonElement>());
  const browsingRef = useRef(false);
  const hoveredRef = useRef(false);
  const focusedRef = useRef(false);
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearResumeTimer = useCallback(() => {
    if (resumeTimerRef.current === null) return;
    clearTimeout(resumeTimerRef.current);
    resumeTimerRef.current = null;
  }, []);

  const revealActive = useCallback(() => {
    if (activeKey === null) return;
    revealItem(scrollerRef.current, itemRefs.current.get(activeKey) ?? null, reducedMotion);
  }, [activeKey, reducedMotion]);

  const pauseFollowing = useCallback(() => {
    clearResumeTimer();
    browsingRef.current = true;
  }, [clearResumeTimer]);

  const resumeFollowingSoon = useCallback(() => {
    if (hoveredRef.current || focusedRef.current) return;
    clearResumeTimer();
    resumeTimerRef.current = setTimeout(() => {
      browsingRef.current = false;
      resumeTimerRef.current = null;
      revealActive();
    }, FOLLOW_RESUME_DELAY_MS);
  }, [clearResumeTimer, revealActive]);

  useEffect(() => {
    if (!browsingRef.current) revealActive();
  }, [activeKey, items, revealActive]);

  useEffect(
    () => () => {
      clearResumeTimer();
    },
    [clearResumeTimer],
  );

  const registerItem = useCallback((key: string, node: HTMLButtonElement | null) => {
    if (node === null) itemRefs.current.delete(key);
    else itemRefs.current.set(key, node);
  }, []);

  if (items.length === 0) return null;

  return (
    <nav
      className="message-line-sidebar"
      aria-label="Conversation outline"
      onPointerEnter={() => {
        hoveredRef.current = true;
        pauseFollowing();
      }}
      onPointerMove={(event) => {
        const scroller = scrollerRef.current;
        if (scroller === null) return;
        const bounds = scroller.getBoundingClientRect();
        pointerIndex.set(
          (event.clientY - bounds.top + scroller.scrollTop - SCROLLER_PADDING) /
            ITEM_HEIGHT,
        );
      }}
      onPointerLeave={() => {
        hoveredRef.current = false;
        pointerIndex.set(IDLE_POINTER_INDEX);
        resumeFollowingSoon();
      }}
      onFocusCapture={() => {
        focusedRef.current = true;
        pauseFollowing();
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          focusedRef.current = false;
          resumeFollowingSoon();
        }
      }}
      onWheel={pauseFollowing}
    >
      <div
        ref={scrollerRef}
        className="message-line-sidebar-scroller"
        data-testid="message-line-sidebar-scroller"
      >
        {items.map((item, index) => (
          <MessageLineButton
            key={item.key}
            item={item}
            index={index}
            active={item.key === activeKey}
            pointerIndex={pointerIndex}
            reducedMotion={reducedMotion}
            register={registerItem}
            onNavigate={onNavigate}
          />
        ))}
      </div>
    </nav>
  );
}

function MessageLineButton({
  item,
  index,
  active,
  pointerIndex,
  reducedMotion,
  register,
  onNavigate,
}: {
  item: MessageLineItem;
  index: number;
  active: boolean;
  pointerIndex: MotionValue<number>;
  reducedMotion: boolean;
  register(key: string, node: HTMLButtonElement | null): void;
  onNavigate(key: string, smooth: boolean): void;
}) {
  const influence = useTransform(pointerIndex, (latest) => {
    if (reducedMotion || !Number.isFinite(latest)) return 0;
    return Math.max(0, 1 - Math.abs(latest - index) / 2.6);
  });
  const width = useTransform(influence, (value) =>
    Math.max(active ? 20 : 12, 12 + value * 14),
  );
  const x = useTransform(influence, [0, 1], [0, 4]);

  useEffect(() => {
    pointerIndex.set(pointerIndex.get());
  }, [active, pointerIndex, reducedMotion]);

  const setRef = useCallback(
    (node: HTMLButtonElement | null) => {
      register(item.key, node);
    },
    [item.key, register],
  );

  return (
    <button
      ref={setRef}
      className={`message-line-sidebar-item${active ? " is-active" : ""}`}
      type="button"
      aria-current={active ? "location" : undefined}
      aria-label={`${item.label}: ${item.excerpt}`}
      data-line-key={item.key}
      data-role={item.role}
      data-streaming={item.streaming ? "true" : undefined}
      title={`${item.label}: ${item.excerpt}`}
      onClick={() => onNavigate(item.key, !reducedMotion)}
    >
      <span className="message-line-sidebar-mark-slot" aria-hidden="true">
        <m.span className="message-line-sidebar-mark" style={{ width, x }} />
      </span>
      <span className="message-line-sidebar-summary">
        <strong>{item.label}</strong>
        <span>{item.excerpt}</span>
      </span>
    </button>
  );
}

export function projectMessageLineItems(
  messages: Message[],
  streamedText: string,
  turnId: string | null,
): MessageLineItem[] {
  const durable = messages.flatMap<MessageLineItem>((message) => {
    if (message.role !== "user" && message.role !== "assistant") return [];
    const role = message.role;
    return [{
      key: messageLineKey(message.id),
      role,
      label: role === "user" ? "You" : "Fairy",
      excerpt: messageLineExcerpt(message.content, role),
      streaming: false,
    }];
  });
  if (!streamedText) return durable;
  return [
    ...durable,
    {
      key: streamingMessageLineKey(turnId),
      role: "assistant",
      label: "Fairy",
      excerpt: messageLineExcerpt(streamedText, "assistant"),
      streaming: true,
    },
  ];
}

export function messageLineKey(messageId: string): string {
  return `message:${messageId}`;
}

export function streamingMessageLineKey(turnId: string | null): string {
  return `stream:${turnId ?? "unscoped"}`;
}

export function messageLineExcerpt(
  content: string,
  role: MessageLineItem["role"],
): string {
  const visible = content
    .replace(/!\[([^\]]*)\]\([^)]*\)/gu, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/gu, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s+/gmu, "")
    .replace(/```[\w-]*|```|`/gu, "")
    .replace(/[*_~]/gu, "")
    .replace(/\s+/gu, " ")
    .trim();
  if (!visible) return role === "user" ? "Attachment message" : "Response";
  return Array.from(visible).slice(0, EXCERPT_LENGTH).join("");
}

function revealItem(
  scroller: HTMLDivElement | null,
  item: HTMLButtonElement | null,
  reducedMotion: boolean,
) {
  if (scroller === null || item === null) return;
  const inset = 8;
  const itemTop = item.offsetTop;
  const itemBottom = itemTop + item.offsetHeight;
  let top: number | null = null;
  if (itemTop < scroller.scrollTop + inset) {
    top = Math.max(0, itemTop - inset);
  } else if (itemBottom > scroller.scrollTop + scroller.clientHeight - inset) {
    top = Math.max(0, itemBottom - scroller.clientHeight + inset);
  }
  if (top === null) return;
  if (typeof scroller.scrollTo === "function") {
    scroller.scrollTo({ top, behavior: reducedMotion ? "auto" : "smooth" });
  } else {
    scroller.scrollTop = top;
  }
}
