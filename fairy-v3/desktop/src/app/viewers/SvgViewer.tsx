import { useEffect, useState } from "react";

import ImageViewer from "./ImageViewer";

const BLOCKED_ELEMENTS = new Set(["script", "foreignobject", "iframe", "object", "embed", "audio", "video"]);

export default function SvgViewer({ source, title }: { source: string; title: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let nextUrl: string | null = null;
    try {
      nextUrl = URL.createObjectURL(new Blob([sanitizeSvg(source)], { type: "image/svg+xml" }));
      setUrl(nextUrl);
      setError(null);
    } catch (sanitizeError) {
      setError(sanitizeError instanceof Error ? sanitizeError.message : "SVG could not be sanitized");
    }
    return () => {
      if (nextUrl !== null) URL.revokeObjectURL(nextUrl);
    };
  }, [source]);
  if (error !== null)
    return (
      <div className="document-status" role="alert">
        {error}
      </div>
    );
  if (url === null) return <div className="document-status">Preparing SVG</div>;
  return <ImageViewer src={url} title={title} />;
}

export function sanitizeSvg(source: string): string {
  const document = new DOMParser().parseFromString(source, "image/svg+xml");
  if (document.querySelector("parsererror") !== null || document.documentElement.localName !== "svg") {
    throw new Error("SVG markup is invalid");
  }
  for (const element of Array.from(document.querySelectorAll("*"))) {
    if (BLOCKED_ELEMENTS.has(element.localName.toLowerCase())) {
      element.remove();
      continue;
    }
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (
        name.startsWith("on") ||
        ((name === "href" || name.endsWith(":href") || name === "src") && !value.startsWith("#")) ||
        ((name === "style" || name === "filter") && /url\s*\(/i.test(value))
      ) {
        element.removeAttribute(attribute.name);
      }
    }
  }
  return new XMLSerializer().serializeToString(document);
}
