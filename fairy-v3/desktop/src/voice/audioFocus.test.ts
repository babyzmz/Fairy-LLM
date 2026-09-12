import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn(), close: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke, isTauri: () => true }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
import { subscribeAudioFocus, type AudioFocusSnapshot } from "./audioFocus";

beforeEach(() => vi.resetAllMocks());

it("subscribes before reading and ignores a late older snapshot", async () => {
  let deliver!: (event: { payload: AudioFocusSnapshot }) => void;
  mocks.listen.mockImplementation(async (_name, callback) => {
    deliver = callback;
    return mocks.close;
  });
  let resolve!: (snapshot: AudioFocusSnapshot) => void;
  mocks.invoke.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const received = vi.fn();
  const close = subscribeAudioFocus(received);
  await Promise.resolve();
  deliver({ payload: { sequence: 4, realtime_active: true } });
  resolve({ sequence: 3, realtime_active: false });
  await Promise.resolve();
  expect(received).toHaveBeenCalledTimes(1);
  expect(received).toHaveBeenLastCalledWith({ sequence: 4, realtime_active: true });
  close();
  deliver({ payload: { sequence: 5, realtime_active: false } });
  expect(received).toHaveBeenCalledTimes(1);
  expect(mocks.close).toHaveBeenCalledOnce();
});

it("cleans a subscription that resolves after unmount", async () => {
  let resolve!: (close: () => void) => void;
  mocks.listen.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const received = vi.fn();
  subscribeAudioFocus(received)();
  resolve(mocks.close);
  await Promise.resolve();
  expect(mocks.close).toHaveBeenCalledOnce();
  expect(mocks.invoke).not.toHaveBeenCalled();
  expect(received).not.toHaveBeenCalled();
});

it("fails closed when host authority is unavailable", async () => {
  mocks.listen.mockResolvedValue(mocks.close);
  mocks.invoke.mockRejectedValue(new Error("host unavailable"));
  const received = vi.fn();
  const close = subscribeAudioFocus(received);
  await vi.waitFor(() => expect(received).toHaveBeenCalledWith({ sequence: 0, realtime_active: true }));
  close();
});
