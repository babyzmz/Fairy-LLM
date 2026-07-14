import { FileWarning, Image as ImageIcon } from "lucide-react";
import { useMemo, useState } from "react";

import type { FilePresentationResult } from "../../core/client";
import "./document-viewer.css";

export default function DocumentViewer({
  presentation,
}: {
  presentation: FilePresentationResult;
}) {
  const payload = useMemo(() => documentPayload(presentation), [presentation]);
  const [selected, setSelected] = useState(0);
  if (payload === null) {
    return <Status message="Document preview metadata is unavailable" />;
  }
  if (payload.kind === "image" && typeof payload.data_base64 === "string") {
    return (
      <div className="document-quicklook">
        <img
          src={`data:image/jpeg;base64,${payload.data_base64}`}
          alt="Embedded iWork Quick Look preview"
        />
        <span>
          Embedded preview, layout may differ from the source application
        </span>
      </div>
    );
  }
  if (payload.kind === "package") {
    return (
      <Status message={text(payload.message, "No embedded document preview")} />
    );
  }
  if (payload.kind === "slides") {
    const slides = records(payload.slides);
    const active = slides[Math.min(selected, Math.max(0, slides.length - 1))];
    return (
      <div className="document-slides">
        <nav aria-label="Slides">
          {slides.map((slide, index) => (
            <button
              type="button"
              key={index}
              className={selected === index ? "selected" : undefined}
              onClick={() => setSelected(index)}
            >
              <span>{index + 1}</span>
              <small>{text(slide.text, "Empty slide")}</small>
            </button>
          ))}
        </nav>
        <article aria-label={`Slide ${selected + 1}`}>
          <p>{text(active?.text, "Empty slide")}</p>
        </article>
      </div>
    );
  }
  if (payload.kind === "workbook") {
    const sheets = records(payload.sheets);
    const active = sheets[Math.min(selected, Math.max(0, sheets.length - 1))];
    return (
      <div className="document-workbook">
        <div role="tablist" aria-label="Workbook sheets">
          {sheets.map((sheet, index) => (
            <button
              type="button"
              role="tab"
              aria-selected={selected === index}
              key={index}
              onClick={() => setSelected(index)}
            >
              {text(sheet.name, `Sheet ${index + 1}`)}
            </button>
          ))}
        </div>
        <Grid rows={arrays(active?.rows)} />
      </div>
    );
  }
  const blocks = records(payload.blocks);
  return (
    <article className="document-page" aria-label="Document content preview">
      {blocks.length === 0 ? (
        <p className="document-empty">
          The document contains no extractable text.
        </p>
      ) : (
        blocks.map((block, index) => <p key={index}>{text(block.text, "")}</p>)
      )}
    </article>
  );
}

export function Grid({ rows }: { rows: unknown[][] }) {
  const columnCount = Math.max(0, ...rows.map((row) => row.length));
  if (rows.length === 0) return <Status message="No tabular rows to display" />;
  return (
    <div className="data-grid-scroll">
      <table className="data-grid">
        <thead>
          <tr>
            <th aria-label="Row number" />
            {Array.from({ length: columnCount }, (_, index) => (
              <th key={index}>{columnName(index)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              <th>{rowIndex + 1}</th>
              {Array.from({ length: columnCount }, (_, columnIndex) => (
                <td key={columnIndex}>{text(row[columnIndex], "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Status({ message }: { message: string }) {
  return (
    <div className="document-status">
      {message.includes("preview") ? (
        <ImageIcon size={20} />
      ) : (
        <FileWarning size={20} />
      )}
      <span>{message}</span>
    </div>
  );
}

function documentPayload(
  presentation: FilePresentationResult,
): Record<string, unknown> | null {
  const payload = presentation.presentation?.assets[0]?.metadata.payload;
  return payload !== null &&
    typeof payload === "object" &&
    !Array.isArray(payload)
    ? (payload as Record<string, unknown>)
    : null;
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          item !== null && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

function arrays(value: unknown): unknown[][] {
  return Array.isArray(value)
    ? value.filter((item): item is unknown[] => Array.isArray(item))
    : [];
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : fallback;
}

function columnName(index: number): string {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}
