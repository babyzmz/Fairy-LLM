import {
  m,
  type MotionValue,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "motion/react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type { Message } from "../core/client";

const TITLE_EXCERPT_LENGTH = 48;
const RESPONSE_EXCERPT_LENGTH = 120;
const IDLE_POINTER_INDEX = Number.POSITIVE_INFINITY;
const FOLLOW_RESUME_DELAY_MS = 400;
const ITEM_HEIGHT = 28;
const SCROLLER_PADDING = 6;

export interface MessageLineItem {
  key: string;
  anchorKey: string;
  messageIds: readonly string[];
  title: string;
  response: string;
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
      aria-label={`${item.title}: ${item.response}`}
      data-line-key={item.key}
      data-streaming={item.streaming ? "true" : undefined}
      title={`${item.title}: ${item.response}`}
      onClick={() => onNavigate(item.key, !reducedMotion)}
    >
      <span className="message-line-sidebar-mark-slot" aria-hidden="true">
        <m.span className="message-line-sidebar-mark" style={{ width, x }} />
      </span>
      <span className="message-line-sidebar-summary">
        <strong>{item.title}</strong>
        <span>{item.response}</span>
      </span>
    </button>
  );
}

export function projectMessageLineItems(
  messages: Message[],
  streamedText: string,
  turnId: string | null,
): MessageLineItem[] {
  const exchanges: MutableExchange[] = [];
  const exchangesByTurn = new Map<string, MutableExchange>();
  let currentLegacyExchange: MutableExchange | null = null;

  for (const message of messages) {
    if (message.role !== "user" && message.role !== "assistant") continue;
    if (message.turn_id !== null) {
      currentLegacyExchange = null;
      const key = turnMessageLineKey(message.turn_id);
      let exchange = exchangesByTurn.get(key);
      if (exchange === undefined) {
        exchange = createExchange(key, messageLineKey(message.id));
        exchangesByTurn.set(key, exchange);
        exchanges.push(exchange);
      }
      exchange.messageIds.push(message.id);
      if (message.role === "user") {
        if (!exchange.userText) exchange.userText = message.content;
        exchange.anchorKey = messageLineKey(message.id);
      } else {
        exchange.assistantTexts.push(message.content);
      }
      continue;
    }

    if (message.role === "user") {
      currentLegacyExchange = createExchange(
        legacyMessageLineKey(message.id),
        messageLineKey(message.id),
      );
      currentLegacyExchange.messageIds.push(message.id);
      currentLegacyExchange.userText = message.content;
      exchanges.push(currentLegacyExchange);
      continue;
    }

    if (currentLegacyExchange === null) {
      currentLegacyExchange = createExchange(
        legacyMessageLineKey(message.id),
        messageLineKey(message.id),
      );
      exchanges.push(currentLegacyExchange);
    }
    currentLegacyExchange.messageIds.push(message.id);
    currentLegacyExchange.assistantTexts.push(message.content);
  }

  const normalizedStream = streamedText.trim();
  if (normalizedStream) {
    let exchange: MutableExchange | undefined;
    if (turnId !== null) {
      const key = turnMessageLineKey(turnId);
      exchange = exchangesByTurn.get(key);
      if (exchange === undefined) {
        exchange = createExchange(key, streamingMessageLineKey(turnId));
        exchangesByTurn.set(key, exchange);
        exchanges.push(exchange);
      }
    } else {
      exchange = currentLegacyExchange ?? createExchange(
        streamingMessageLineKey(null),
        streamingMessageLineKey(null),
      );
      if (currentLegacyExchange === null) exchanges.push(exchange);
    }
    exchange.streamedText = streamedText;
  }

  return exchanges.map((exchange) => {
    const durableResponse = exchange.assistantTexts.join(" ").trim();
    const activeResponse = exchange.streamedText.trim();
    return {
      key: exchange.key,
      anchorKey: exchange.anchorKey,
      messageIds: exchange.messageIds,
      title: exchange.userText.trim()
        ? messageLineExcerpt(
          exchange.userText,
          "user",
          TITLE_EXCERPT_LENGTH,
        )
        : "Current request",
      response: durableResponse
        ? messageLineExcerpt(
          durableResponse,
          "assistant",
          RESPONSE_EXCERPT_LENGTH,
        )
        : activeResponse
          ? messageLineExcerpt(
            activeResponse,
            "assistant",
            RESPONSE_EXCERPT_LENGTH,
          )
          : "Fairy is responding…",
      streaming: durableResponse.length === 0 && activeResponse.length > 0,
    };
  });
}

export function messageLineKey(messageId: string): string {
  return `message:${messageId}`;
}

export function turnMessageLineKey(turnId: string): string {
  return `turn:${turnId}`;
}

export function legacyMessageLineKey(messageId: string): string {
  return `exchange:${messageId}`;
}

export function streamingMessageLineKey(turnId: string | null): string {
  return `stream:${turnId ?? "unscoped"}`;
}

export function messageLineExcerpt(
  content: string,
  role: MessageLineRole,
  limit = TITLE_EXCERPT_LENGTH,
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
  return Array.from(visible).slice(0, limit).join("");
}

type MessageLineRole = "user" | "assistant";

interface MutableExchange {
  key: string;
  anchorKey: string;
  messageIds: string[];
  userText: string;
  assistantTexts: string[];
  streamedText: string;
}

function createExchange(key: string, anchorKey: string): MutableExchange {
  return {
    key,
    anchorKey,
    messageIds: [],
    userText: "",
    assistantTexts: [],
    streamedText: "",
  };
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
