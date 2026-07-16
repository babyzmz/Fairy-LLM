import { useEffect, useState } from "react";
import { Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";

import "./action-dialog.css";

interface ActionDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  inputLabel?: string;
  initialValue?: string;
  onCancel(): void;
  onConfirm(value: string | null): void | Promise<void>;
}

export function ActionDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = false,
  busy = false,
  inputLabel,
  initialValue = "",
  onCancel,
  onConfirm,
}: ActionDialogProps) {
  const [value, setValue] = useState(initialValue);

  useEffect(() => {
    if (open) setValue(initialValue);
  }, [initialValue, open]);

  const normalized = inputLabel === undefined ? null : value.trim();
  const confirmDisabled = busy || (normalized !== null && normalized.length === 0);

  return (
    <ModalOverlay
      className="action-dialog-overlay"
      isDismissable={!busy}
      isKeyboardDismissDisabled={busy}
      isOpen={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && !busy) onCancel();
      }}
    >
      <Modal className="action-dialog-modal">
        <Dialog className="action-dialog" role={destructive ? "alertdialog" : "dialog"}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!confirmDisabled) void onConfirm(normalized);
            }}
          >
            <Heading slot="title">{title}</Heading>
            <p>{description}</p>
            {inputLabel !== undefined ? (
              <label className="action-dialog-field">
                <span>{inputLabel}</span>
                <input
                  autoFocus
                  disabled={busy}
                  maxLength={240}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                />
              </label>
            ) : null}
            <div className="action-dialog-actions">
              <button autoFocus={inputLabel === undefined} disabled={busy} type="button" onClick={onCancel}>
                {cancelLabel}
              </button>
              <button
                className={destructive ? "danger" : "primary"}
                disabled={confirmDisabled}
                type="submit"
              >
                {busy ? "Working..." : confirmLabel}
              </button>
            </div>
          </form>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
