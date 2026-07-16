import {
  ChevronLeft,
  ChevronRight,
  FileWarning,
  LoaderCircle,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  GlobalWorkerOptions,
  PasswordResponses,
  TextLayer,
  getDocument,
  type PDFDocumentProxy,
} from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";

import { pdfCanvasScale } from "./previewLimits";
import "./pdf-viewer.css";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

interface PdfViewerProps {
  title: string;
  url: string;
}

export default function PdfViewer({ title, url }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1);
  const [rendering, setRendering] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setDocument(null);
    setPageNumber(1);
    setRendering(true);
    setError(null);
    const loading = getDocument({
      url,
      disableAutoFetch: false,
      disableRange: false,
      disableStream: false,
      enableXfa: false,
      isEvalSupported: false,
      rangeChunkSize: 64 * 1024,
      withCredentials: false,
    });
    loading.onPassword = (
      _updatePassword: (password: string) => void,
      reason: number,
    ) => {
      if (!active) return;
      setError(
        reason === PasswordResponses.INCORRECT_PASSWORD
          ? "The PDF password is incorrect"
          : "Password-protected PDFs require an unlocked copy",
      );
      void loading.destroy();
    };
    void loading.promise
      .then((nextDocument) => {
        if (!active) {
          void nextDocument.destroy();
          return;
        }
        setDocument(nextDocument);
      })
      .catch((loadError: unknown) => {
        if (!active) return;
        setError(
          loadError instanceof Error
            ? loadError.message
            : "PDF could not be opened",
        );
        setRendering(false);
      });
    return () => {
      active = false;
      void loading.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (document === null) return;
    let active = true;
    let renderTask: { cancel(): void; promise: Promise<unknown> } | null = null;
    let textLayer: TextLayer | null = null;
    setRendering(true);
    setError(null);
    void document
      .getPage(pageNumber)
      .then(async (page) => {
        if (!active) return;
        const canvas = canvasRef.current;
        const textContainer = textLayerRef.current;
        const context = canvas?.getContext("2d", { alpha: false });
        if (canvas === null || textContainer === null || context === null) {
          throw new Error("PDF canvas is unavailable");
        }
        const viewport = page.getViewport({ scale });
        const outputScale = pdfCanvasScale(
          viewport.width,
          viewport.height,
          window.devicePixelRatio || 1,
        );
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        textContainer.replaceChildren();
        textContainer.style.width = `${Math.floor(viewport.width)}px`;
        textContainer.style.height = `${Math.floor(viewport.height)}px`;
        textContainer.style.setProperty("--total-scale-factor", String(scale));
        renderTask = page.render({
          canvas,
          canvasContext: context,
          transform:
            outputScale === 1
              ? undefined
              : [outputScale, 0, 0, outputScale, 0, 0],
          viewport,
        });
        textLayer = new TextLayer({
          container: textContainer,
          textContentSource: page.streamTextContent(),
          viewport,
        });
        await Promise.all([renderTask.promise, textLayer.render()]);
        if (active) setRendering(false);
      })
      .catch((renderError: unknown) => {
        if (!active || isCanceled(renderError)) return;
        setError(
          renderError instanceof Error
            ? renderError.message
            : "PDF page could not render",
        );
        setRendering(false);
      });
    return () => {
      active = false;
      renderTask?.cancel();
      textLayer?.cancel();
    };
  }, [document, pageNumber, scale]);

  const pageCount = document?.numPages ?? 0;
  return (
    <div className="pdf-viewer" aria-label={`PDF viewer: ${title}`}>
      <div className="pdf-toolbar" aria-label="PDF controls">
        <button
          type="button"
          className="icon-button"
          aria-label="Previous PDF page"
          disabled={pageNumber <= 1}
          onClick={() => setPageNumber((current) => Math.max(1, current - 1))}
        >
          <ChevronLeft size={15} />
        </button>
        <span>
          {pageCount === 0 ? "Loading" : `${pageNumber} / ${pageCount}`}
        </span>
        <button
          type="button"
          className="icon-button"
          aria-label="Next PDF page"
          disabled={pageCount === 0 || pageNumber >= pageCount}
          onClick={() =>
            setPageNumber((current) => Math.min(pageCount, current + 1))
          }
        >
          <ChevronRight size={15} />
        </button>
        <i aria-hidden="true" />
        <button
          type="button"
          className="icon-button"
          aria-label="Zoom out PDF"
          disabled={scale <= 0.5}
          onClick={() => setScale((current) => Math.max(0.5, current - 0.25))}
        >
          <ZoomOut size={15} />
        </button>
        <span>{Math.round(scale * 100)}%</span>
        <button
          type="button"
          className="icon-button"
          aria-label="Zoom in PDF"
          disabled={scale >= 2.5}
          onClick={() => setScale((current) => Math.min(2.5, current + 0.25))}
        >
          <ZoomIn size={15} />
        </button>
      </div>
      <div className="pdf-page-scroll">
        {error !== null ? (
          <div className="pdf-status" role="alert">
            <FileWarning size={22} />
            <span>{error}</span>
          </div>
        ) : (
          <div className="pdf-page" aria-busy={rendering}>
            <canvas
              ref={canvasRef}
              role="img"
              aria-label={`${title}, page ${pageNumber}`}
            />
            <div ref={textLayerRef} className="textLayer" />
            {rendering ? (
              <div className="pdf-rendering">
                <LoaderCircle size={18} className="spin" />
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function isCanceled(error: unknown): boolean {
  return error instanceof Error && /cancel/i.test(error.name + error.message);
}
