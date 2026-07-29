import { useEffect, useRef, useState, type ReactNode } from "react";

export function Category({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return <div className="settings-category"><header><span>{subtitle}</span><h1>{title}</h1></header><div className="settings-category-body">{children}</div></div>;
}

export function SettingToggle({ label, detail, checked, disabled, onChange }: { label: string; detail?: string; checked: boolean; disabled: boolean; onChange(value: boolean): void }) {
  return <label className="settings-row settings-toggle-row"><span><strong>{label}</strong>{detail ? <small>{detail}</small> : null}</span><span className="settings-switch"><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span /></span></label>;
}

export function SettingSelect({ icon, label, detail, value, options, disabled, onChange }: { icon: ReactNode; label: string; detail?: string; value: string; options: { value: string; label: string; disabled?: boolean }[]; disabled: boolean; onChange(value: string): void }) {
  return <label className="settings-row settings-select-row"><span className="settings-row-copy">{icon}<span><strong>{label}</strong>{detail ? <small>{detail}</small> : null}</span></span><select aria-label={label} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option value={option.value} key={option.value} disabled={option.disabled}>{option.label}</option>)}</select></label>;
}

export function SettingTextInput({ icon, label, detail, value, placeholder, disabled, onCommit }: { icon: ReactNode; label: string; detail?: string; value: string; placeholder?: string; disabled: boolean; onCommit(value: string): void }) {
  const [draft, setDraft] = useState(value);
  const composing = useRef(false);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (!composing.current && draft !== value) onCommit(draft);
  };
  return <label className="settings-row settings-text-row"><span className="settings-row-copy">{icon}<span><strong>{label}</strong>{detail ? <small>{detail}</small> : null}</span></span><input aria-label={label} value={draft} placeholder={placeholder} disabled={disabled} spellCheck={false} onChange={(event) => setDraft(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={(event) => { composing.current = false; setDraft(event.currentTarget.value); }} onBlur={commit} onKeyDown={(event) => {
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.blur();
    } else if (event.key === "Escape") {
      event.preventDefault();
      setDraft(value);
      event.currentTarget.blur();
    }
  }} /></label>;
}

export function SettingRange({ label, value, min, max, step = 1, suffix, disabled, onCommit }: { label: string; value: number; min: number; max: number; step?: number; suffix: string; disabled: boolean; onCommit(value: number): void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <label className="settings-row settings-range-row"><span><strong>{label}</strong><small>{draft}{suffix}</small></span><input type="range" min={min} max={max} step={step} value={draft} disabled={disabled} onChange={(event) => setDraft(Number(event.target.value))} onPointerUp={() => onCommit(draft)} onKeyUp={() => onCommit(draft)} /></label>;
}

export function HealthRow({ icon, label, status, tone }: { icon: ReactNode; label: string; status: string; tone: "neutral" | "success" | "error" }) {
  return <div className="settings-row settings-health-row"><span className="settings-row-copy">{icon}<strong>{label}</strong></span><span data-tone={tone}>{status}</span></div>;
}
