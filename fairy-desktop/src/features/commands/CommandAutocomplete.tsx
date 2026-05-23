import type { CSSProperties } from "react";

import type { CommandSpec } from "../../lib/api/commands";

interface CommandAutocompleteProps {
  matches: CommandSpec[];
  highlighted: number;
  onPick: (spec: CommandSpec) => void;
}

const LIST_STYLE: CSSProperties = {
  position: "absolute",
  bottom: "100%",
  left: 0,
  right: 0,
  marginBottom: 6,
  borderRadius: 10,
  border: "1px solid rgba(143,181,255,0.45)",
  backgroundColor: "rgba(10, 24, 56, 0.96)",
  color: "rgba(236, 240, 246, 0.96)",
  padding: 6,
  fontSize: 13,
  zIndex: 60,
  maxHeight: 200,
  overflowY: "auto",
  boxShadow: "0 4px 14px rgba(0,0,0,0.45)",
};

const ITEM_BASE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "6px 10px",
  borderRadius: 6,
  cursor: "pointer",
};

export function CommandAutocomplete({ matches, highlighted, onPick }: CommandAutocompleteProps): JSX.Element | null {
  if (matches.length === 0) {
    return null;
  }
  return (
    <ul className="command-autocomplete" style={LIST_STYLE} role="listbox">
      {matches.map((spec, index) => {
        const active = index === highlighted;
        const itemStyle: CSSProperties = {
          ...ITEM_BASE,
          backgroundColor: active ? "rgba(64, 110, 195, 0.45)" : "transparent",
        };
        return (
          <li
            key={spec.name}
            style={itemStyle}
            role="option"
            aria-selected={active}
            onMouseDown={(event) => {
              event.preventDefault();
              onPick(spec);
            }}
          >
            <strong style={{ fontWeight: 600 }}>{spec.usage}</strong>
            <span style={{ opacity: 0.78, fontSize: 12 }}>{spec.description}</span>
          </li>
        );
      })}
    </ul>
  );
}
