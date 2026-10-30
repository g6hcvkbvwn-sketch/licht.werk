import Apc40View from "./Apc40View";
import type { Scene } from "./api";

interface Props {
  scenes: Scene[];
  onScenesChanged: () => void;
}

// The Xone:K2 surface is on hold; ControllerView.tsx is kept for a later return.
export default function ControllerPage({ scenes }: Props) {
  return <Apc40View scenes={scenes} />;
}
