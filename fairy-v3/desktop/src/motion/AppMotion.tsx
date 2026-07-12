import { domAnimation, LazyMotion, MotionConfig } from "motion/react";
import type { ReactNode } from "react";

export const enterTransition = {
  duration: 0.18,
  ease: [0.2, 0.8, 0.2, 1],
} as const;
export const collapseTransition = {
  duration: 0.16,
  ease: [0.4, 0, 0.2, 1],
} as const;

export function AppMotion({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user" transition={enterTransition}>
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}
