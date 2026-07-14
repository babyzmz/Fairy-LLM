import { StrictMode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useDeferredChannelClose } from "./useDeferredChannelClose";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useDeferredChannelClose", () => {
  it("survives the Strict Mode probe and closes after the real unmount", () => {
    vi.useFakeTimers();
    const channel = { close: vi.fn() };
    const view = render(
      <StrictMode>
        <Harness channel={channel} />
      </StrictMode>,
    );

    act(() => vi.runOnlyPendingTimers());
    expect(channel.close).not.toHaveBeenCalled();

    view.unmount();
    act(() => vi.runOnlyPendingTimers());
    expect(channel.close).toHaveBeenCalledOnce();
  });

  it("closes an owned channel after the component switches instances", () => {
    vi.useFakeTimers();
    const first = { close: vi.fn() };
    const second = { close: vi.fn() };
    const view = render(<Harness channel={first} />);

    view.rerender(<Harness channel={second} />);
    act(() => vi.runOnlyPendingTimers());
    expect(first.close).toHaveBeenCalledOnce();
    expect(second.close).not.toHaveBeenCalled();

    view.unmount();
    act(() => vi.runOnlyPendingTimers());
    expect(second.close).toHaveBeenCalledOnce();
  });
});

function Harness({ channel }: { channel: { close(): void } }) {
  useDeferredChannelClose(channel, true);
  return null;
}
