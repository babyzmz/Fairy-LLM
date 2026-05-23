import { ChatPage } from "../features/chat/ChatPage";
import { PetSurface } from "../features/pet/PetSurface";

export function App(): JSX.Element {
  const surface = new URLSearchParams(window.location.search).get("surface");
  if (surface === "pet") {
    return <PetSurface />;
  }
  return <ChatPage />;
}
