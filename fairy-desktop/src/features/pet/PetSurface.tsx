import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  FairyAvatar,
  type FairyAvatarSignal,
  type FairyWorkState,
  resolveFairyAvatarSignal,
} from "../../components/fairy/FairyAvatar";
import { streamChat } from "../../lib/api/chat";
import { muteCompanion, petCompanion } from "../../lib/api/companion";
import { getSystemState, performSystemAction } from "../../lib/api/system";
import { useFairyVoiceRuntime } from "../../components/fairy/useFairyVoiceRuntime";
import { SpeechBubble } from "../companion/SpeechBubble";
import { sceneToSignal } from "../companion/sceneToAvatarMode";
import { useAttendingState } from "../companion/useAttendingState";
import { useCompanionStream } from "../companion/useCompanionStream";


const ATTENDING_SIGNAL: FairyAvatarSignal = { state: "focused", certainty: 0.9, urgency: 0.18 };
import type {
  CardUnion,
  ChatStreamEvent,
  DesktopSystemState,
  FairyMeta,
  RuntimeAssistantState,
} from "../../lib/types/api";

type PetPhase = "idle" | "thinking" | "replying" | "error";
type PetAnswerVariant =
  | "text"
  | "structured"
  | "weather"
  | "time"
  | "location"
  | "news"
  | "compare"
  | "error"
  | "thinking"
  | "uncertain";

interface PetPresence {
  signal: FairyAvatarSignal;
  label: string;
  detail: string;
}

interface PetCardPreview {
  variant: PetAnswerVariant;
  label: string;
  title: string;
  summary: string;
  metrics: string[];
}

const SESSION_ID = "fairy-pet";
const FAIRY_STATES = new Set<FairyWorkState>(["standby", "relaxed", "thinking", "focused", "uncertain", "alert"]);

const STATE_LABELS: Record<FairyWorkState, string> = {
  standby: "待机",
  relaxed: "放松",
  thinking: "思考",
  focused: "专注",
  uncertain: "待确认",
  alert: "警觉",
};

const TONE_LABELS: Record<string, string> = {
  answering: "正在整理",
  attention: "需要处理",
  clarifying: "需要确认",
  empty_result: "结果不足",
  focused: "处理中",
  initializing: "启动中",
  ready: "就绪",
  warming_up: "预热中",
  working: "工作中",
};

const RUNTIME_SIGNAL_BY_STATE: Record<RuntimeAssistantState, FairyAvatarSignal> = {
  booting: { state: "standby", certainty: 0.45, urgency: 0.36 },
  warming_up: { state: "relaxed", certainty: 0.58, urgency: 0.28 },
  idle: { state: "standby", certainty: 0.9, urgency: 0.08 },
  thinking: { state: "thinking", certainty: 0.58, urgency: 0.58 },
  analyzing: { state: "focused", certainty: 0.76, urgency: 0.52 },
  replying: { state: "focused", certainty: 0.9, urgency: 0.28 },
  error: { state: "alert", certainty: 0.62, urgency: 0.94 },
  sleeping: { state: "standby", certainty: 0.84, urgency: 0.03 },
};

function normalizeNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return undefined;
  }
  return Math.max(0, Math.min(1, value));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).trim();
}

function firstText(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = textValue(record[key]);
    if (value) {
      return value;
    }
  }
  return "";
}

function joinMetrics(values: Array<string | undefined>, limit = 3): string[] {
  return values.map((value) => textValue(value)).filter(Boolean).slice(0, limit);
}

function signalFromFairyMeta(meta: FairyMeta | undefined): FairyAvatarSignal | null {
  if (!meta?.state || !FAIRY_STATES.has(meta.state as FairyWorkState)) {
    return null;
  }
  return {
    state: meta.state as FairyWorkState,
    certainty: normalizeNumber(meta.certainty),
    urgency: normalizeNumber(meta.urgency),
  };
}

function messageFromError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error || "请求失败");
}

function presenceFromSignal(signal: FairyAvatarSignal, detail: string): PetPresence {
  return {
    signal,
    label: STATE_LABELS[signal.state],
    detail,
  };
}

function derivePetPresence(
  systemState: DesktopSystemState | null,
  phase: PetPhase,
  chatError: string,
  systemError: string,
  streamSignal: FairyAvatarSignal | null,
): PetPresence {
  if (chatError || systemError || phase === "error") {
    return presenceFromSignal({ state: "alert", certainty: 0.62, urgency: 0.92 }, chatError || systemError);
  }
  if (streamSignal) {
    return presenceFromSignal(streamSignal, "fairy-meta");
  }
  if (phase === "thinking") {
    return presenceFromSignal({ state: "thinking", certainty: 0.54, urgency: 0.62 }, "streaming-request");
  }
  if (phase === "replying") {
    return presenceFromSignal({ state: "focused", certainty: 0.9, urgency: 0.28 }, "streaming-response");
  }

  const runtimeState = systemState?.runtime_state;
  const runtimeFairySignal = signalFromFairyMeta(runtimeState?.fairy);
  if (runtimeFairySignal) {
    return presenceFromSignal(runtimeFairySignal, "runtime-fairy");
  }
  if (runtimeState?.current_state) {
    return presenceFromSignal(RUNTIME_SIGNAL_BY_STATE[runtimeState.current_state], runtimeState.current_state);
  }
  if (systemState?.bridge_status === "starting" || !systemState) {
    return presenceFromSignal(resolveFairyAvatarSignal("booting"), "starting-shell");
  }
  if (systemState.bridge_status !== "ready" && systemState.bridge_status !== "reused") {
    return presenceFromSignal(resolveFairyAvatarSignal("warming_up"), systemState.bridge_status);
  }
  return presenceFromSignal(resolveFairyAvatarSignal("idle"), "ready");
}

function signalFromStreamEvent(event: ChatStreamEvent): FairyAvatarSignal | null {
  if (!("meta" in event)) {
    return null;
  }
  return signalFromFairyMeta(event.meta.fairy);
}

function fairyMetaFromStreamEvent(event: ChatStreamEvent): FairyMeta | null {
  if (!("meta" in event)) {
    return null;
  }
  return event.meta.fairy ?? null;
}

function toneLabel(meta: FairyMeta | null, fallback: string): string {
  const tone = textValue(meta?.tone);
  return TONE_LABELS[tone] || tone || fallback;
}

function previewForCard(card: CardUnion): PetCardPreview {
  const data = asRecord(card.data);
  switch (card.type) {
    case "weather": {
      const title = firstText(data, ["location", "city", "country"]) || "天气";
      const temp = firstText(data, ["temperature_c", "temp"]);
      const condition = firstText(data, ["condition", "condition_key"]);
      return {
        variant: "weather",
        label: "天气",
        title,
        summary: joinMetrics([temp ? `${temp}°C` : "", condition], 2).join(" · ") || firstText(data, ["summary"]),
        metrics: joinMetrics([firstText(data, ["high_c", "high"]) && `高 ${firstText(data, ["high_c", "high"])}°`, firstText(data, ["low_c", "low"]) && `低 ${firstText(data, ["low_c", "low"])}°`, firstText(data, ["wind_kmh", "wind"])]),
      };
    }
    case "time":
      return {
        variant: "time",
        label: "时间",
        title: firstText(data, ["time_text"]) || "当前时间",
        summary: joinMetrics([firstText(data, ["location"]), firstText(data, ["date_text"]), firstText(data, ["weekday"])]).join(" · "),
        metrics: joinMetrics([firstText(data, ["timezone"]), firstText(data, ["period"])]),
      };
    case "location":
    case "map_preview":
      return {
        variant: "location",
        label: "位置",
        title: firstText(data, ["title", "location", "address"]) || "地图位置",
        summary: firstText(data, ["address", "summary", "city"]),
        metrics: joinMetrics([firstText(data, ["distance_text"]), firstText(data, ["city"]), firstText(data, ["country"])]),
      };
    case "news_list": {
      const items = asList(data.items).map(asRecord);
      const firstItem = items[0] ?? {};
      return {
        variant: "news",
        label: "新闻",
        title: firstText(data, ["title"]) || "新闻摘要",
        summary: firstText(firstItem, ["headline", "title", "summary", "snippet"]),
        metrics: items.slice(0, 3).map((item) => firstText(item, ["source", "published_at"])).filter(Boolean),
      };
    }
    case "compare":
      return {
        variant: "compare",
        label: "对比",
        title: firstText(data, ["title"]) || "对比结论",
        summary: firstText(data, ["recommendation", "summary"]),
        metrics: asList(data.items).map((item) => firstText(asRecord(item), ["title"])).filter(Boolean).slice(0, 3),
      };
    case "specs":
      return {
        variant: "structured",
        label: "规格",
        title: firstText(data, ["title"]) || "规格信息",
        summary: firstText(data, ["summary"]),
        metrics: asList(data.fields)
          .map((field) => {
            const record = asRecord(field);
            const label = firstText(record, ["label", "name"]);
            const value = firstText(record, ["value"]);
            return label && value ? `${label}: ${value}` : "";
          })
          .filter(Boolean)
          .slice(0, 3),
      };
    case "release":
      return {
        variant: "structured",
        label: "发布",
        title: firstText(data, ["title"]) || "发布信息",
        summary: firstText(data, ["summary"]),
        metrics: joinMetrics([firstText(data, ["status"]), firstText(data, ["date"])]),
      };
    case "visual_read":
      return {
        variant: "structured",
        label: "视觉",
        title: firstText(data, ["visual_type", "region"]) || "屏幕读取",
        summary: firstText(data, ["summary"]),
        metrics: joinMetrics([typeof data.confidence === "number" ? `置信 ${Math.round(data.confidence * 100)}%` : "", firstText(data, ["region"])]),
      };
    case "web_brief":
      return {
        variant: "structured",
        label: "网页",
        title: firstText(data, ["title"]) || "网页摘要",
        summary: firstText(data, ["summary"]) || asList(data.bullets).map(textValue).filter(Boolean).slice(0, 2).join(" · "),
        metrics: asList(data.bullets).map(textValue).filter(Boolean).slice(0, 3),
      };
    default:
      return {
        variant: "structured",
        label: "信息",
        title: firstText(data, ["title"]) || "结构化回复",
        summary: firstText(data, ["summary"]) || textValue(card.type),
        metrics: asList(data.fields)
          .map((field) => {
            const record = asRecord(field);
            const label = firstText(record, ["label", "name"]);
            const value = firstText(record, ["value"]);
            return label && value ? `${label}: ${value}` : "";
          })
          .filter(Boolean)
          .slice(0, 2),
      };
  }
}

function answerVariant(error: string, phase: PetPhase, cards: CardUnion[], presence: PetPresence): PetAnswerVariant {
  if (error) {
    return "error";
  }
  if (phase === "thinking") {
    return "thinking";
  }
  if (cards.length > 0) {
    return previewForCard(cards[0]).variant;
  }
  if (presence.signal.state === "uncertain") {
    return "uncertain";
  }
  return "text";
}

function PetCardPreviewView({ card }: { card: CardUnion }): JSX.Element {
  const preview = previewForCard(card);
  return (
    <article className={`pet-result pet-result--${preview.variant}`}>
      <div className="pet-result__head">
        <span>{preview.label}</span>
        <strong>{preview.title}</strong>
      </div>
      {preview.summary ? <p>{preview.summary}</p> : null}
      {preview.metrics.length > 0 ? (
        <div className="pet-result__metrics">
          {preview.metrics.map((metric) => (
            <span key={metric}>{metric}</span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function PetSurface(): JSX.Element {
  const [systemState, setSystemState] = useState<DesktopSystemState | null>(null);
  const [systemError, setSystemError] = useState("");
  const [phase, setPhase] = useState<PetPhase>("idle");
  const [input, setInput] = useState("");
  const [reply, setReply] = useState("");
  const [chatError, setChatError] = useState("");
  const [progress, setProgress] = useState("");
  const [cards, setCards] = useState<CardUnion[]>([]);
  const [fairyMeta, setFairyMeta] = useState<FairyMeta | null>(null);
  const [streamSignal, setStreamSignal] = useState<FairyAvatarSignal | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const settleTimerRef = useRef<number | null>(null);

  const { bubble, petBurst, scene, triggerPet } = useCompanionStream();
  const { isAttending } = useAttendingState();
  const voice = useFairyVoiceRuntime({
    backendStatus: systemState?.bridge_status === "ready" || systemState?.bridge_status === "reused" ? "online" : systemState ? "offline" : "unknown",
    systemState,
  });
  const presence = useMemo(() => {
    if (isAttending) {
      return { signal: ATTENDING_SIGNAL, label: "聆听", detail: "attending" };
    }
    const base = derivePetPresence(systemState, phase, chatError, systemError, streamSignal);
    if (phase !== "idle" || chatError || systemError || streamSignal) return base;
    const sceneSignal = sceneToSignal(scene);
    if (!sceneSignal) return base;
    return { ...base, signal: sceneSignal, label: STATE_LABELS[sceneSignal.state], detail: `scene:${scene}` };
  }, [chatError, isAttending, phase, streamSignal, systemError, systemState, scene]);
  const visibleMessage = chatError || systemError || reply || (isBusy ? progress : "");
  const visibleQuestion = textValue(fairyMeta?.next_question);
  const hasAnswer = Boolean(visibleMessage || cards.length > 0 || visibleQuestion || isBusy);
  const variant = answerVariant(chatError || systemError, phase, cards, presence);
  const answerTitle = chatError || systemError
    ? "需要处理"
    : cards.length > 0
      ? previewForCard(cards[0]).label
      : toneLabel(fairyMeta, phase === "thinking" ? "思考中" : "Fairy");

  useEffect(() => {
    document.body.classList.add("pet-window-body");
    return () => {
      document.body.classList.remove("pet-window-body");
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    const refresh = async () => {
      try {
        const nextState = await getSystemState();
        if (!disposed) {
          setSystemState(nextState);
          setSystemError("");
        }
      } catch (error) {
        if (!disposed) {
          setSystemError(messageFromError(error));
        }
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 1200);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (settleTimerRef.current !== null) {
        window.clearTimeout(settleTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if ((!reply && cards.length === 0) || isBusy || phase !== "idle") {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      setReply("");
      setCards([]);
      setFairyMeta(null);
      setStreamSignal(null);
    }, 14000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [cards.length, isBusy, phase, reply]);

  const queueReturnToIdle = () => {
    if (settleTimerRef.current !== null) {
      window.clearTimeout(settleTimerRef.current);
    }
    settleTimerRef.current = window.setTimeout(() => {
      setPhase((current) => (current === "replying" ? "idle" : current));
      settleTimerRef.current = null;
    }, 2600);
  };

  const applyStreamEvent = (event: ChatStreamEvent, appendText: (delta: string) => void) => {
    const nextSignal = signalFromStreamEvent(event);
    if (nextSignal) {
      setStreamSignal(nextSignal);
    }
    const nextMeta = fairyMetaFromStreamEvent(event);
    if (nextMeta) {
      setFairyMeta(nextMeta);
    }
    const requestId = "request_id" in event ? String(event.request_id || "") : "";
    switch (event.event) {
      case "message_start":
        setProgress("正在理解请求");
        setPhase("thinking");
        break;
      case "progress":
        setProgress(event.text || event.stage);
        setPhase("thinking");
        break;
      case "text_delta":
        appendText(event.text);
        setProgress("");
        setPhase("replying");
        voice.handleChatReplyChunk(requestId, event.text);
        break;
      case "card":
        setCards((current) => [...current, event.card]);
        setProgress("");
        setPhase("replying");
        break;
      case "message_end":
        if (event.errors.length > 0) {
          setChatError(event.errors[0]?.message || "请求失败");
          setPhase("error");
          voice.handleChatReplyError(requestId, event.errors[0]?.message);
          break;
        }
        if (event.text.trim()) {
          setReply(event.text);
        }
        if (event.cards.length > 0) {
          setCards(event.cards);
        }
        setProgress("");
        setPhase("replying");
        voice.handleChatReply({
          id: `pet-${requestId || Date.now()}`,
          kind: "assistant_final",
          requestId,
          text: event.text || "",
          cards: event.cards || [],
          errors: event.errors || [],
          meta: event.meta as Record<string, unknown> | undefined,
          createdAt: Date.now(),
        });
        break;
      case "error":
        setChatError(event.message);
        setProgress("");
        setPhase("error");
        voice.handleChatReplyError(requestId, event.message);
        break;
      default:
        break;
    }
  };

  const clearAnswer = () => {
    setReply("");
    setCards([]);
    setChatError("");
    setProgress("");
    setFairyMeta(null);
    setStreamSignal(null);
    setPhase("idle");
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = input.trim();
    if (!message || isBusy) {
      return;
    }
    if (settleTimerRef.current !== null) {
      window.clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    let assistantText = "";
    let failed = false;
    setInput("");
    setReply("");
    setCards([]);
    setChatError("");
    setProgress("正在理解请求");
    setFairyMeta(null);
    setStreamSignal(null);
    setIsBusy(true);
    setPhase("thinking");
    try {
      await streamChat(
        { message, session_id: SESSION_ID, attachments: null },
        {
          signal: controller.signal,
          onEvent: (streamEvent) => {
            applyStreamEvent(streamEvent, (delta) => {
              assistantText += delta;
              setReply(assistantText);
            });
            if (streamEvent.event === "error" || (streamEvent.event === "message_end" && streamEvent.errors.length > 0)) {
              failed = true;
            }
          },
        },
      );
      if (!failed) {
        if (assistantText.trim()) {
          setReply((current) => current || assistantText);
        }
        setProgress("");
        setPhase("replying");
        queueReturnToIdle();
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setChatError(messageFromError(error));
        setProgress("");
        setPhase("error");
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setIsBusy(false);
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsBusy(false);
    clearAnswer();
  };

  const handleOpenMainWindow = () => {
    void performSystemAction("focus_window").catch(() => undefined);
  };

  const handleFairyPet = () => {
    triggerPet();
    void petCompanion().catch(() => undefined);
    inputRef.current?.focus();
  };

  const handleContextMenu = (event: React.MouseEvent<HTMLElement>) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY });
  };

  const closeContextMenu = () => setContextMenu(null);

  const handleToggleMute = () => {
    const next = !muted;
    setMuted(next);
    void muteCompanion(next).catch(() => undefined);
    closeContextMenu();
  };

  const handleShowMain = () => {
    void performSystemAction("focus_window").catch(() => undefined);
    closeContextMenu();
  };

  const handleQuitApp = () => {
    closeContextMenu();
    void (async () => {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("quit_app");
      } catch (err) {
        console.error("[fairy] quit_app failed:", err);
      }
    })();
  };

  useEffect(() => {
    if (!contextMenu) return undefined;
    const onDocPointer = () => closeContextMenu();
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeContextMenu();
    };
    window.addEventListener("mousedown", onDocPointer);
    window.addEventListener("keydown", onEsc);
    return () => {
      window.removeEventListener("mousedown", onDocPointer);
      window.removeEventListener("keydown", onEsc);
    };
  }, [contextMenu]);

  const handleAvatarPointerDown = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    const startX = event.clientX;
    const startY = event.clientY;
    let dragStarted = false;

    const onMove = (move: MouseEvent) => {
      if (dragStarted) return;
      const dx = Math.abs(move.clientX - startX);
      const dy = Math.abs(move.clientY - startY);
      if (dx > 4 || dy > 4) {
        dragStarted = true;
        cleanup();
        void (async () => {
          try {
            const { invoke } = await import("@tauri-apps/api/core");
            await invoke("plugin:window|start_dragging");
          } catch (err) {
            console.error("[fairy] start_dragging failed:", err);
          }
        })();
      }
    };

    const onUp = () => {
      cleanup();
      if (!dragStarted) {
        handleFairyPet();
      }
    };

    function cleanup() {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <main className={`pet-surface pet-surface--${presence.signal.state} pet-surface--${phase}`} onContextMenu={handleContextMenu}>
      {contextMenu ? (
        <ul
          className="pet-context-menu"
          style={{
            position: "fixed",
            left: Math.min(contextMenu.x, window.innerWidth - 160),
            top: Math.min(contextMenu.y, window.innerHeight - 160),
            zIndex: 100,
            margin: 0,
            padding: "4px 0",
            listStyle: "none",
            minWidth: 140,
            backgroundColor: "rgba(10, 24, 56, 0.96)",
            border: "1px solid rgba(143,181,255,0.45)",
            borderRadius: 8,
            boxShadow: "0 4px 14px rgba(0,0,0,0.45)",
            color: "rgba(236, 240, 246, 0.96)",
            fontSize: 13,
            userSelect: "none",
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <li style={{ padding: "6px 14px", cursor: "pointer" }} onClick={handleShowMain}>显示主聊天窗</li>
          <li style={{ padding: "6px 14px", cursor: "pointer" }} onClick={handleToggleMute}>
            {muted ? "取消静音" : "静音桌宠"}
          </li>
          <li style={{ height: 1, margin: "4px 0", backgroundColor: "rgba(143,181,255,0.25)" }} />
          <li style={{ padding: "6px 14px", cursor: "pointer", color: "rgba(255,170,170,0.92)" }} onClick={handleQuitApp}>退出 Fairy</li>
        </ul>
      ) : null}
      <section className="pet-stage" title={presence.detail} data-tauri-drag-region style={{ position: "relative" }}>
        <div className="pet-stage__halo" />
        {isAttending ? null : <SpeechBubble bubble={bubble} />}
        <button
          className={[
            "pet-orb-button",
            `pet-orb-button--scene-${scene}`,
            isAttending ? "pet-orb-button--attending" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          type="button"
          aria-label="Fairy"
          onMouseDown={handleAvatarPointerDown}
          onDoubleClick={handleOpenMainWindow}
          style={
            isAttending
              ? {
                  filter: "drop-shadow(0 0 10px rgba(200,230,255,0.55))",
                  transform: "translateY(-2px)",
                  transition: "transform 0.4s ease-out, filter 0.3s ease-out",
                }
              : petBurst
                ? { filter: "drop-shadow(0 0 12px rgba(255,182,213,0.7))" }
                : undefined
          }
        >
          <FairyAvatar size={154} animated mode={presence.signal.state} signal={presence.signal} />
        </button>
        <div className="pet-status" data-tauri-drag-region>
          <span className="pet-status__dot" />
          <span>{presence.label}</span>
          <em>{toneLabel(fairyMeta, isBusy ? "处理中" : "在线")}</em>
        </div>
      </section>

      {hasAnswer ? (
        <section className={`pet-answer pet-answer--${variant}`}>
          <div className="pet-answer__header">
            <span>{answerTitle}</span>
            <div className="pet-answer__actions">
              <button className="pet-mini-button" type="button" aria-label="打开主窗口" onClick={handleOpenMainWindow}>
                ↗
              </button>
              <button className="pet-mini-button" type="button" aria-label="清除回复" onClick={clearAnswer}>
                ×
              </button>
            </div>
          </div>
          {isBusy && !reply ? (
            <div className="pet-thinking" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          ) : null}
          {visibleMessage ? <p className="pet-reply">{visibleMessage}</p> : null}
          {cards.length > 0 ? (
            <div className="pet-results">
              {cards.slice(0, 2).map((card, index) => (
                <PetCardPreviewView key={`${card.type}-${index}`} card={card} />
              ))}
              {cards.length > 2 ? <span className="pet-results__more">+{cards.length - 2}</span> : null}
            </div>
          ) : null}
          {visibleQuestion ? (
            <button className="pet-question" type="button" onClick={() => setInput(visibleQuestion)}>
              {visibleQuestion}
            </button>
          ) : null}
        </section>
      ) : null}

      <form className="pet-input-row" onSubmit={handleSubmit}>
        <input
          ref={inputRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="问 Fairy"
          disabled={isBusy}
          maxLength={500}
        />
        <button className="pet-icon-button" type="submit" aria-label="发送" disabled={isBusy || !input.trim()}>
          ↵
        </button>
        {isBusy ? (
          <button className="pet-icon-button pet-icon-button--stop" type="button" aria-label="停止" onClick={handleCancel}>
            ×
          </button>
        ) : null}
      </form>
    </main>
  );
}
