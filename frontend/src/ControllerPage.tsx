import { useEffect, useState } from "react";
import { getApc40, type Scene } from "./api";
import Apc40View from "./Apc40View";
import ControllerView from "./ControllerView";

type Surface = "apc40" | "k2";

interface Props {
  scenes: Scene[];
  onScenesChanged: () => void;
}

/**
 * Shows the APC40 or the Xone:K2 surface. Until the user picks one, the choice follows what is
 * plugged in: the K2 only when it alone is connected, otherwise the APC40 (the main controller).
 */
export default function ControllerPage({ scenes, onScenesChanged }: Props) {
  const [chosen, setChosen] = useState<Surface | null>(null);
  const [auto, setAuto] = useState<Surface>("apc40");

  useEffect(() => {
    if (chosen) return;
    let stop = false;
    const check = async () => {
      try {
        const s = await getApc40();
        if (!stop) setAuto(!s.connected && s.other_connected ? "k2" : "apc40");
      } catch {
        // Keep the current choice while the backend is away.
      }
    };
    void check();
    const timer = window.setInterval(check, 2000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [chosen]);

  const surface = chosen ?? auto;
  return (
    <>
      <div className="surface-switch">
        {(["apc40", "k2"] as const).map((id) => (
          <button
            key={id}
            className={surface === id ? "tab active" : "tab"}
            onClick={() => setChosen(id)}
          >
            {id === "apc40" ? "APC40 MKII" : "Xone:K2"}
          </button>
        ))}
        {chosen && (
          <button className="tab" title="Follow the connected controller again" onClick={() => setChosen(null)}>
            Auto
          </button>
        )}
      </div>
      {surface === "apc40" ? (
        <Apc40View scenes={scenes} />
      ) : (
        <ControllerView scenes={scenes} onScenesChanged={onScenesChanged} />
      )}
    </>
  );
}
