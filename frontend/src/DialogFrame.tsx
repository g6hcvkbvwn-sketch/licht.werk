import { useEffect, useRef, type ReactNode } from "react";
import "./dialog.css";

export interface DialogFrameProps {
  id: string;
  title: string;
  size: "compact" | "medium" | "wide";
  dirty: boolean;
  busy: boolean;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
  context?: string;
  preview?: boolean;
  status?: ReactNode;
  saveNote?: boolean;
  className?: string;
}

const FOCUSABLE = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, a[href]';

// Shared editor frame: fixed header, scrolling body, always-visible footer.
// The backdrop never closes it, so an accidental click cannot discard a draft.
export default function DialogFrame({
  id, title, size, dirty, busy, onClose, footer, children, context, preview, status, saveNote, className = "",
}: DialogFrameProps) {
  const frame = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    frame.current?.focus();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  const requestClose = () => {
    if (busy) return;
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    onClose();
  };
  return <div className="dialog-backdrop" onMouseDown={(e) => {
    if (e.target === e.currentTarget) { e.preventDefault(); frame.current?.focus(); }
  }}>
    <section className={`show-dialog dialog-frame dialog-size-${size} ${className}`} role="dialog" aria-modal="true"
      aria-labelledby={`${id}-title`} tabIndex={-1} ref={frame} onKeyDown={(e) => {
        if (e.key === "Escape") { e.stopPropagation(); requestClose(); return; }
        if (e.key !== "Tab") return;
        const visible = Array.from(frame.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
          .filter((node) => node.getClientRects().length);
        const first = visible[0], last = visible[visible.length - 1];
        const at = document.activeElement;
        if (e.shiftKey && (at === first || at === frame.current)) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && (at === last || at === frame.current)) { e.preventDefault(); first?.focus(); }
      }}>
      <header>
        <h2 id={`${id}-title`}>{title}</h2>
        <button type="button" disabled={busy} onClick={requestClose}>Close</button>
      </header>
      {context && <p className={preview ? "dialog-context preview" : "dialog-context"}>
        {preview !== undefined && <strong>{preview ? "PREVIEW" : "LIVE"}</strong>}{context}
        {dirty && <em className="dialog-dirty"> · Unsaved changes</em>}
      </p>}
      <div className="dialog-body">{children}</div>
      <footer className="dialog-footer">
        {status && <div className="dialog-status">{status}</div>}
        {saveNote && <small className="muted">Preview stages output, not file edits. Saved definitions change
          immediately for later launches.</small>}
        <div className="dialog-actions">{footer}</div>
      </footer>
    </section>
  </div>;
}
