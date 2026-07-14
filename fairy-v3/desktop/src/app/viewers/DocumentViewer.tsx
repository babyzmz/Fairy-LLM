import { Archive, BookOpen, FileWarning, Image as ImageIcon, Mail } from "lucide-react";
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
  if (payload.kind === "archive") {
    const entries = records(payload.entries);
    return (
      <div className="document-structured">
        <header><Archive size={17} /><strong>{text(payload.format, "Archive")}</strong><span>{entries.length} entries</span></header>
        <div className="document-entry-list" role="list" aria-label="Archive entries">
          {entries.map((entry, index) => (
            <div role="listitem" key={`${text(entry.path, "entry")}-${index}`}>
              <span>{text(entry.path, "Unnamed entry")}</span>
              <small>{text(entry.kind, "file")} · {formatBytes(number(entry.size))}</small>
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (payload.kind === "ebook") {
    const chapters = records(payload.chapters);
    const active = chapters[Math.min(selected, Math.max(0, chapters.length - 1))];
    return (
      <div className="document-reader">
        <nav aria-label="Ebook chapters">
          {chapters.map((chapter, index) => (
            <button type="button" key={index} className={selected === index ? "selected" : undefined} onClick={() => setSelected(index)}>
              <BookOpen size={13} /><span>{text(chapter.title, `Chapter ${index + 1}`)}</span>
            </button>
          ))}
        </nav>
        <article><h2>{text(active?.title, "Chapter")}</h2><p>{text(active?.text, "No extractable text")}</p></article>
      </div>
    );
  }
  if (payload.kind === "mail") {
    const headers = record(payload.headers);
    const attachments = records(payload.attachments);
    return (
      <article className="document-mail" aria-label="Mail message preview">
        <header><Mail size={18} /><div><strong>{text(headers.subject, "No subject")}</strong><span>{text(headers.from, "Unknown sender")}</span></div></header>
        <dl>
          {(["to", "cc", "date"] as const).map((name) => text(headers[name], "") === "" ? null : <div key={name}><dt>{name}</dt><dd>{text(headers[name], "")}</dd></div>)}
        </dl>
        <p>{text(payload.body, "No extractable message body")}</p>
        {attachments.length > 0 ? <section><strong>Attachments</strong>{attachments.map((attachment, index) => <div key={index}><span>{text(attachment.filename, "Attachment")}</span><small>{formatBytes(number(attachment.size))}</small></div>)}</section> : null}
        <footer>Remote content blocked</footer>
      </article>
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

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
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

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
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
