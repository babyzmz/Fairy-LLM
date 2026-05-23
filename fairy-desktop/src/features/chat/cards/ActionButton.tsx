import { executeCardAction } from "../../../lib/actions/cardActions";
import type { CardAction } from "../../../lib/types/api";

interface ActionButtonProps {
  action: CardAction | null | undefined;
  className?: string;
  label?: string;
}

export function ActionButton({ action, className = "card__action-button", label }: ActionButtonProps): JSX.Element | null {
  if (!action) {
    return null;
  }
  return (
    <button className={className} type="button" onClick={() => executeCardAction(action)}>
      {label || action.label}
    </button>
  );
}
