import { useEffect, useRef, useState } from "react";

import { subscribeQuipStream, type QuipPayload } from "../../lib/api/companion";
import { BUBBLE_SHOW_MS, FADE_WINDOW_MS, PET_BURST_MS } from "./companionConstants";

export interface BubbleViewState {
  text: string;
  category: string;
  showingSince: number;
  fading: boolean;
  expired: boolean;
}

export interface PetBurstState {
  active: boolean;
  startedAt: number;
}

export interface CompanionStreamState {
  bubble: BubbleViewState | null;
  petBurst: PetBurstState | null;
  triggerPet: () => void;
}

export function useCompanionStream(): CompanionStreamState {
  const [bubble, setBubble] = useState<BubbleViewState | null>(null);
  const [petBurst, setPetBurst] = useState<PetBurstState | null>(null);
  const expiryTimerRef = useRef<number | null>(null);
  const fadeTimerRef = useRef<number | null>(null);
  const petTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const unsubscribe = subscribeQuipStream((payload: QuipPayload) => {
      const now = Date.now();
      setBubble({
        text: payload.text,
        category: payload.category,
        showingSince: now,
        fading: false,
        expired: false,
      });
      if (fadeTimerRef.current !== null) {
        window.clearTimeout(fadeTimerRef.current);
      }
      if (expiryTimerRef.current !== null) {
        window.clearTimeout(expiryTimerRef.current);
      }
      fadeTimerRef.current = window.setTimeout(() => {
        setBubble((current) => (current ? { ...current, fading: true } : current));
      }, BUBBLE_SHOW_MS - FADE_WINDOW_MS);
      expiryTimerRef.current = window.setTimeout(() => {
        setBubble(null);
      }, BUBBLE_SHOW_MS);
    });
    return () => {
      unsubscribe();
      if (fadeTimerRef.current !== null) window.clearTimeout(fadeTimerRef.current);
      if (expiryTimerRef.current !== null) window.clearTimeout(expiryTimerRef.current);
    };
  }, []);

  const triggerPet = () => {
    const now = Date.now();
    setPetBurst({ active: true, startedAt: now });
    if (petTimerRef.current !== null) {
      window.clearTimeout(petTimerRef.current);
    }
    petTimerRef.current = window.setTimeout(() => {
      setPetBurst(null);
    }, PET_BURST_MS);
  };

  useEffect(() => {
    return () => {
      if (petTimerRef.current !== null) window.clearTimeout(petTimerRef.current);
    };
  }, []);

  return { bubble, petBurst, triggerPet };
}
