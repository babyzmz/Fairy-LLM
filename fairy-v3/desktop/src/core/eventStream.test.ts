import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "./contracts";
import {
  reconcileEventCheckpoint,
  runEventDelivery,
  type EventCheckpoint,
  type EventDeliveryClient,
} from "./eventStream";
import { readEventCheckpoint, writeEventCheckpoint } from "../app/workspacePreferences";

const event = (cursor: number): EventEnvelope => ({
  id: `019f4b33-c7fb-7652-a127-${cursor.toString().padStart(12, "0")}`,
  cursor,
  run_id: "019f4b33-c7fb-7652-a127-759143030001",
  project_id: null,
  conversation_id: "019f4b33-c7fb-7652-a127-759143030002",
  task_id: "019f4b33-c7fb-7652-a127-759143030003",
  version_id: null,
  task_sequence: cursor,
  event_type: "task.updated",
  visibility: "user",
  message: `Event ${cursor}`,
  payload: {},
  schema_version: 1,
  created_at: "2026-07-16T00:00:00Z",
});

describe("event checkpoint reconciliation", () => {
  const state = {
    ledger_id: "019f4b33-c7fb-7652-a127-759143030010",
    oldest_cursor: 5,
    latest_cursor: 9,
  };

  it("resets source, ledger, ahead, and retained-history mismatches", () => {
    const checkpoint = (overrides: Partial<EventCheckpoint>): EventCheckpoint => ({
      source_id: "local:stdio",
      ledger_id: state.ledger_id,
      cursor: 7,
      ...overrides,
    });

    expect(reconcileEventCheckpoint("cloud:https://fairy.test", state, checkpoint({}))).toBe(4);
    expect(reconcileEventCheckpoint("local:stdio", state, checkpoint({ ledger_id: "other" }))).toBe(4);
    expect(reconcileEventCheckpoint("local:stdio", state, checkpoint({ cursor: 99 }))).toBe(4);
    expect(reconcileEventCheckpoint("local:stdio", state, checkpoint({ cursor: 2 }))).toBe(4);
    expect(reconcileEventCheckpoint("local:stdio", state, checkpoint({ cursor: 7 }))).toBe(7);
  });
});

it("backfills before live delivery and persists only projected events", async () => {
  const delivered: number[] = [];
  const checkpoints: EventCheckpoint[] = [];
  const calls: string[] = [];
  const client: EventDeliveryClient = {
    sourceId: "local:stdio",
    async state() {
      calls.push("state");
      return {
        ledger_id: "019f4b33-c7fb-7652-a127-759143030010",
        oldest_cursor: 1,
        latest_cursor: 3,
      };
    },
    async list(cursor) {
      calls.push(`list:${cursor}`);
      const items = cursor === 0 ? [event(1), event(2)] : [event(3)];
      return { items, next_cursor: items.at(-1)?.cursor ?? cursor };
    },
    async *subscribe(cursor) {
      calls.push(`subscribe:${cursor}`);
      yield event(3);
      yield event(4);
    },
  };

  await runEventDelivery(client, {
    checkpoint: {
      source_id: "local:stdio",
      ledger_id: "old-ledger",
      cursor: 500,
    },
    onEvent(next) {
      delivered.push(next.cursor);
    },
    onCheckpoint(next) {
      checkpoints.push(next);
    },
  });

  expect(calls).toEqual(["state", "list:0", "list:2", "subscribe:3"]);
  expect(delivered).toEqual([1, 2, 3, 4]);
  expect(checkpoints.at(-1)?.cursor).toBe(4);
});

it("persists a source-fenced checkpoint and never trusts the legacy bare cursor", () => {
  window.localStorage.clear();
  window.localStorage.setItem("fairy.events.cursor", "9000");

  expect(readEventCheckpoint()).toBeNull();

  const checkpoint: EventCheckpoint = {
    source_id: "cloud:https://fairy.test",
    ledger_id: "019f4b33-c7fb-7652-a127-759143030010",
    cursor: 7,
  };
  writeEventCheckpoint(checkpoint);

  expect(readEventCheckpoint()).toEqual(checkpoint);
  expect(window.localStorage.getItem("fairy.events.cursor")).toBeNull();
});

it("fails instead of looping when history does not advance", async () => {
  const client: EventDeliveryClient = {
    sourceId: "local:stdio",
    async state() {
      return {
        ledger_id: "019f4b33-c7fb-7652-a127-759143030010",
        oldest_cursor: 1,
        latest_cursor: 2,
      };
    },
    async list(cursor) {
      return { items: [], next_cursor: cursor };
    },
    async *subscribe() {
      throw new Error("live delivery must not start");
    },
  };

  await expect(
    runEventDelivery(client, {
      checkpoint: null,
      onEvent() {},
      onCheckpoint() {},
    }),
  ).rejects.toThrow("Event history did not advance");
});

it("stops live projection immediately after cancellation", async () => {
  const controller = new AbortController();
  const delivered: number[] = [];
  const client: EventDeliveryClient = {
    sourceId: "local:stdio",
    async state() {
      return {
        ledger_id: "019f4b33-c7fb-7652-a127-759143030010",
        oldest_cursor: 0,
        latest_cursor: 0,
      };
    },
    async list(cursor) {
      return { items: [], next_cursor: cursor };
    },
    async *subscribe() {
      yield event(1);
      yield event(2);
    },
  };

  await expect(
    runEventDelivery(client, {
      checkpoint: null,
      signal: controller.signal,
      onEvent(next) {
        delivered.push(next.cursor);
        controller.abort();
      },
      onCheckpoint() {},
    }),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(delivered).toEqual([1]);
});
