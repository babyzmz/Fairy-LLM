import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import MediaViewer from "./MediaViewer";
import { sanitizeSvg } from "./SvgViewer";

afterEach(cleanup);

describe("media viewers", () => {
  it("attaches bounded caption tracks and changes playback rate", async () => {
    const user = userEvent.setup();
    render(
      <MediaViewer
        src="http://127.0.0.1/video.mp4"
        mediaType="video/mp4"
        title="Demo"
        captions={[{ label: "English", language: "en", src: "http://127.0.0.1/demo.en.vtt" }]}
      />,
    );
    const video = screen.getByLabelText("Demo") as HTMLVideoElement;
    expect(video.querySelector("track")).toHaveAttribute("srclang", "en");
    await user.selectOptions(screen.getByLabelText("Playback speed"), "1.5");
    expect(video.playbackRate).toBe(1.5);
  });

  it("removes active SVG content and external resource references", () => {
    const sanitized = sanitizeSvg(
      '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><image href="https://attacker.invalid/a.png" onload="alert(2)"/><use href="#safe"/></svg>',
    );
    expect(sanitized).not.toContain("script");
    expect(sanitized).not.toContain("attacker.invalid");
    expect(sanitized).not.toContain("onload");
    expect(sanitized).toContain('href="#safe"');
  });
});
