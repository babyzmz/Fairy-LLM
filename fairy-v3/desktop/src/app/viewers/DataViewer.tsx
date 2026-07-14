import { useMemo, useState } from "react";

import { Grid } from "./DocumentViewer";

const PAGE_SIZE = 200;
const MAX_ROWS = 20_000;
const MAX_COLUMNS = 500;

export default function DataViewer({
  text,
  path,
}: {
  text: string;
  path: string;
}) {
  const [page, setPage] = useState(0);
  const result = useMemo(() => {
    try {
      return { ...parseData(text, path), error: null };
    } catch (error) {
      return {
        rows: [] as unknown[][],
        truncated: false,
        error:
          error instanceof Error ? error.message : "Data could not be parsed",
      };
    }
  }, [path, text]);
  if (result.error !== null) {
    return (
      <div className="document-status" role="alert">
        {result.error}
      </div>
    );
  }
  const pageCount = Math.max(1, Math.ceil(result.rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const rows = result.rows.slice(
    safePage * PAGE_SIZE,
    (safePage + 1) * PAGE_SIZE,
  );
  return (
    <div className="data-viewer">
      <div className="data-viewer-toolbar">
        <span>{result.rows.length.toLocaleString()} rows</span>
        {result.truncated ? <strong>Preview limited</strong> : null}
        <i />
        <button
          type="button"
          disabled={safePage === 0}
          onClick={() => setPage(safePage - 1)}
        >
          Previous
        </button>
        <span>
          {safePage + 1} / {pageCount}
        </span>
        <button
          type="button"
          disabled={safePage >= pageCount - 1}
          onClick={() => setPage(safePage + 1)}
        >
          Next
        </button>
      </div>
      <Grid rows={rows} />
    </div>
  );
}

function parseData(
  source: string,
  path: string,
): { rows: unknown[][]; truncated: boolean } {
  if (/\.(json|jsonl|ndjson)$/i.test(path)) return parseJson(source, path);
  const delimiter = /\.tsv$/i.test(path) ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index <= source.length; index += 1) {
    const character = source[index] ?? "\n";
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') quoted = false;
      else field += character;
      continue;
    }
    if (character === '"' && field.length === 0) quoted = true;
    else if (character === delimiter) {
      if (row.length < MAX_COLUMNS) row.push(field);
      field = "";
    } else if (character === "\n") {
      if (row.length < MAX_COLUMNS) row.push(field.replace(/\r$/, ""));
      rows.push(row);
      if (rows.length >= MAX_ROWS) break;
      row = [];
      field = "";
    } else field += character;
  }
  return { rows, truncated: rows.length >= MAX_ROWS };
}

function parseJson(
  source: string,
  path: string,
): { rows: unknown[][]; truncated: boolean } {
  let values: unknown[];
  if (/\.(jsonl|ndjson)$/i.test(path)) {
    values = source
      .split(/\r?\n/)
      .filter(Boolean)
      .slice(0, MAX_ROWS)
      .map((line) => JSON.parse(line));
  } else {
    const parsed: unknown = JSON.parse(source);
    values = Array.isArray(parsed) ? parsed : [parsed];
  }
  const records = values.filter(
    (value): value is Record<string, unknown> =>
      value !== null && typeof value === "object" && !Array.isArray(value),
  );
  if (records.length !== values.length)
    return {
      rows: values.slice(0, MAX_ROWS).map((value) => [value]),
      truncated: values.length > MAX_ROWS,
    };
  const columns = Array.from(
    new Set(records.flatMap((record) => Object.keys(record))),
  ).slice(0, MAX_COLUMNS);
  return {
    rows: [
      columns,
      ...records
        .slice(0, MAX_ROWS - 1)
        .map((record) => columns.map((key) => scalar(record[key]))),
    ],
    truncated: records.length >= MAX_ROWS,
  };
}

function scalar(value: unknown): unknown {
  return value !== null && typeof value === "object"
    ? JSON.stringify(value)
    : value;
}
