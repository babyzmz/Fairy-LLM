import {
  Bot,
  Brain,
  Check,
  ChevronDown,
  Code2,
  Image,
  Music2,
  Settings2,
  Sparkles,
  Video,
  WalletCards,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Collection,
  Header,
  Menu,
  MenuItem,
  MenuSection,
  MenuTrigger,
  Popover,
} from "react-aria-components";

import type {
  ModelCatalogEntry,
  ModelCatalogPage,
  ModelSelectionPreference,
} from "../core/client";
import { selectedCatalogEntry } from "./modelSelection";
import "./model-selector.css";

interface ModelSelectorProps {
  catalog: ModelCatalogPage | null;
  selection: ModelSelectionPreference | null;
  disabled?: boolean;
  onSelect(mode: "auto" | "manual", modelId: string | null): Promise<void>;
  onOpenSettings(): Promise<void>;
}

const groupOrder = ["auto", "general", "professional", "free"] as const;
type ModelGroup = (typeof groupOrder)[number];

export function ModelSelector({
  catalog,
  selection,
  disabled = false,
  onSelect,
  onOpenSettings,
}: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const selected = selectedCatalogEntry(catalog, selection);
  const selectedKey = selection?.mode === "manual" && typeof selection.model_id === "string"
    ? selection.model_id
    : "auto";
  const grouped = useMemo(() => groupEntries(catalog?.items ?? []), [catalog?.items]);

  useEffect(() => {
    if (!open) return;
    const observePlacement = () => {
      // React Aria owns placement; this read keeps our host listener side-effect free.
      triggerRef.current?.getBoundingClientRect();
    };
    const viewport = window.visualViewport;
    const observer = new ResizeObserver(observePlacement);
    if (triggerRef.current !== null) observer.observe(triggerRef.current);
    window.addEventListener("resize", observePlacement);
    viewport?.addEventListener("resize", observePlacement);
    viewport?.addEventListener("scroll", observePlacement);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", observePlacement);
      viewport?.removeEventListener("resize", observePlacement);
      viewport?.removeEventListener("scroll", observePlacement);
    };
  }, [open]);

  const act = async (key: React.Key) => {
    if (key === "settings") {
      setOpen(false);
      await onOpenSettings();
      return;
    }
    setPending(true);
    try {
      await onSelect(key === "auto" ? "auto" : "manual", key === "auto" ? null : String(key));
      setOpen(false);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="model-selector" ref={triggerRef}>
      <MenuTrigger isOpen={open} onOpenChange={setOpen}>
        <Button
          className="model-selector-trigger"
          isDisabled={disabled || pending}
          aria-label={`Model: ${selection?.mode === "manual" ? selected?.display_name ?? "Unavailable model" : "Auto"}`}
          data-tooltip={selection?.mode === "manual" ? selected?.display_name ?? selection.model_id ?? "Model" : "Auto model routing"}
        >
          <ModelIcon entry={selected} auto={selection?.mode !== "manual"} />
          <span>{triggerLabel(selection, selected)}</span>
          <ChevronDown size={13} aria-hidden="true" />
        </Button>
        <Popover
          className="model-selector-popover"
          placement="top start"
          offset={8}
          containerPadding={8}
          shouldFlip
        >
          <Menu
            aria-label="Choose a model"
            className="model-selector-menu"
            selectionMode="single"
            selectedKeys={new Set([selectedKey])}
            disabledKeys={disabledModelKeys(catalog?.items ?? [])}
            onAction={(key) => void act(key).catch(() => undefined)}
          >
            <MenuSection id="auto-section">
              <Header>Auto</Header>
              <MenuItem id="auto" textValue="Auto" className="model-selector-item">
                <span className="model-item-icon"><Sparkles size={16} /></span>
                <span className="model-item-copy">
                  <strong>Auto</strong>
                  <small>Routes each request to the right specialist</small>
                  <span><em>Text + media</em><em>Recommended</em></span>
                </span>
                {selectedKey === "auto" ? <Check size={15} /> : null}
              </MenuItem>
            </MenuSection>
            <MenuSection id="general-section">
              <Header>General & reasoning</Header>
              <CatalogMenuItems entries={grouped.general} selectedKey={selectedKey} />
            </MenuSection>
            <MenuSection id="professional-section">
              <Header>Professional generation</Header>
              <CatalogMenuItems entries={grouped.professional} selectedKey={selectedKey} />
            </MenuSection>
            <MenuSection id="free-section">
              <Header>Free</Header>
              <CatalogMenuItems entries={grouped.free} selectedKey={selectedKey} />
            </MenuSection>
            <MenuSection id="settings-section">
              <MenuItem id="settings" textValue="Open model settings" className="model-selector-settings">
                <Settings2 size={15} />
                <span>Model settings</span>
              </MenuItem>
            </MenuSection>
          </Menu>
        </Popover>
      </MenuTrigger>
    </div>
  );
}

function CatalogMenuItems({ entries, selectedKey }: {
  entries: ModelCatalogEntry[];
  selectedKey: string;
}) {
  return (
    <Collection items={entries}>
      {(entry) => (
        <MenuItem
          id={entry.model_id}
          textValue={entry.display_name}
          className="model-selector-item"
        >
          <span className="model-item-icon"><ModelIcon entry={entry} /></span>
          <span className="model-item-copy">
            <strong>{entry.display_name}</strong>
            <small>{purpose(entry)}</small>
            <span>
              <em>{modality(entry)}</em>
              <em>{entry.paid ? "Paid" : "Free"}</em>
              <em data-availability={entry.availability}>{healthLabel(entry)}</em>
            </span>
          </span>
          {selectedKey === entry.model_id ? <Check size={15} /> : null}
        </MenuItem>
      )}
    </Collection>
  );
}

function groupEntries(entries: ModelCatalogEntry[]): Record<Exclude<ModelGroup, "auto">, ModelCatalogEntry[]> {
  return {
    general: entries.filter((entry) => ["primary", "strongest", "code"].includes(entry.category)),
    professional: entries.filter((entry) => ["image", "music", "video"].includes(entry.category)),
    free: entries.filter((entry) => ["free_general", "free_code"].includes(entry.category)),
  };
}

function disabledModelKeys(entries: ModelCatalogEntry[]): Set<string> {
  return new Set(entries.filter((entry) => entry.availability === "unavailable").map((entry) => entry.model_id));
}

function triggerLabel(selection: ModelSelectionPreference | null, entry: ModelCatalogEntry | null): string {
  if (selection?.mode !== "manual") return "Auto";
  switch (entry?.category) {
    case "primary": return "DeepSeek";
    case "strongest": return "GLM 5.2";
    case "code": return "Kimi Code";
    case "image": return "Gemini Image";
    case "music": return "Lyria Music";
    case "video": return "Seedance Video";
    case "free_general": return "Nemotron";
    case "free_code": return "Qwen Coder";
    default: return "Model";
  }
}

function purpose(entry: ModelCatalogEntry): string {
  switch (entry.category) {
    case "primary": return "Balanced default for everyday work";
    case "strongest": return "Deep reasoning and demanding review";
    case "code": return "Implementation, debugging, and tests";
    case "image": return "Generate and revise images";
    case "music": return "Create original music";
    case "video": return "Create asynchronous video jobs";
    case "free_general": return "General tasks without model charges";
    case "free_code": return "Code work without model charges";
  }
}

function modality(entry: ModelCatalogEntry): string {
  switch (entry.endpoint_kind) {
    case "images": return "Image";
    case "audio": return "Music";
    case "videos": return "Video";
    default: return entry.input_modalities.includes("image") ? "Text + image" : "Text";
  }
}

function healthLabel(entry: ModelCatalogEntry): string {
  if (entry.availability === "available") return "Ready";
  if (entry.availability === "unavailable") return entry.unavailable_reason ?? "Unavailable";
  return "Checking";
}

function ModelIcon({ entry, auto = false }: { entry: ModelCatalogEntry | null; auto?: boolean }) {
  if (auto) return <Sparkles size={15} />;
  switch (entry?.category) {
    case "strongest": return <Brain size={15} />;
    case "code":
    case "free_code": return <Code2 size={15} />;
    case "image": return <Image size={15} />;
    case "music": return <Music2 size={15} />;
    case "video": return <Video size={15} />;
    case "free_general": return <WalletCards size={15} />;
    default: return <Bot size={15} />;
  }
}
