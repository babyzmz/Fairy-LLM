import type {
  ChatStreamEvent,
  CapabilitiesResponse,
  ChatInvokeRequest,
  ChatInvokeResponse,
  HealthResponse,
} from "../types/api";
import { API_BASE_URL } from "../config/env";
import { ApiRequestError, apiRequest } from "./client";
import { normalizeChatInvokeResponse, normalizeChatStreamEvent } from "../types/cardContract";

export function getHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>("/health");
}

export function getCapabilities(): Promise<CapabilitiesResponse> {
  return apiRequest<CapabilitiesResponse>("/capabilities");
}

export function invokeChat(payload: ChatInvokeRequest): Promise<ChatInvokeResponse> {
  return apiRequest<unknown>("/chat/invoke", {
    method: "POST",
    body: JSON.stringify(payload),
  }).then((response) => normalizeChatInvokeResponse(response));
}

interface StreamChatOptions {
  signal?: AbortSignal;
  onEvent: (event: ChatStreamEvent) => void;
}

function parseStreamEventPayload(eventName: string, rawData: string): ChatStreamEvent {
  const payload = (JSON.parse(rawData) as Record<string, unknown>) || {};
  payload.event = String(payload.event || eventName);
  return normalizeChatStreamEvent(eventName, payload);
}

function consumeSseBuffer(
  buffer: string,
  emit: (event: ChatStreamEvent) => void,
): string {
  let working = buffer.replace(/\r\n/g, "\n");
  while (true) {
    const separatorIndex = working.indexOf("\n\n");
    if (separatorIndex === -1) {
      return working;
    }
    const block = working.slice(0, separatorIndex);
    working = working.slice(separatorIndex + 2);
    if (!block.trim()) {
      continue;
    }
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }
    if (dataLines.length === 0) {
      continue;
    }
    emit(parseStreamEventPayload(eventName, dataLines.join("\n")));
  }
}

export async function streamChat(payload: ChatInvokeRequest, options: StreamChatOptions): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal: options.signal,
  });

  const errorText = await (async () => {
    if (response.ok || !response.body) {
      return "";
    }
    return response.text();
  })();

  if (!response.ok) {
    let message = `Request failed with status ${response.status}.`;
    let details: unknown = errorText;
    if (errorText) {
      try {
        details = JSON.parse(errorText) as unknown;
        if (typeof details === "object" && details !== null && "errors" in details) {
          message = String(((details as { errors?: Array<{ message?: string }> }).errors?.[0]?.message) || message);
        }
      } catch {
        message = errorText;
      }
    }
    throw new ApiRequestError(message, response.status, details);
  }

  if (!response.body) {
    throw new ApiRequestError("Streaming response body is unavailable.", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      buffer = consumeSseBuffer(buffer, options.onEvent);
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      consumeSseBuffer(`${buffer}\n\n`, options.onEvent);
    }
  } finally {
    reader.releaseLock();
  }
}
