import { useState } from "react";
import type { Scene } from "./api";

interface Props {
  scenes: Scene[];
  activeScene: string | null;
  transitionActive: boolean;
  onRecall: (sceneId: string) => void;
  onDelete: (sceneId: string) => void;
  onCapture: (name: string, fadeSeconds: number) => void;
}

export default function ScenePanel(props: Props) {
  const [name, setName] = useState("");
  const [fade, setFade] = useState(0);

  return (
    <section className="scenes">
      <h2>Looks {props.transitionActive && <span className="fading">FADING…</span>}</h2>
      {props.scenes.length === 0 && <p className="muted">No looks saved yet.</p>}
      <div className="scene-grid">
        {props.scenes.map((scene) => (
          <div
            key={scene.id}
            className={scene.id === props.activeScene ? "scene active" : "scene"}
          >
            <button className="scene-recall" onClick={() => props.onRecall(scene.id)}>
              {scene.name}
              <span className="muted"> · fade {scene.fade_seconds}s</span>
            </button>
            <button
              className="scene-delete"
              aria-label={`Delete ${scene.name}`}
              onClick={() => {
                if (window.confirm(`Delete look "${scene.name}"?`)) props.onDelete(scene.id);
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
      <form
        className="capture"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) {
            props.onCapture(name.trim(), fade);
            setName("");
          }
        }}
      >
        <input
          placeholder="New look name"
          maxLength={80}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label>
          Fade s
          <input
            type="number"
            min={0}
            max={600}
            step={0.5}
            value={fade}
            onChange={(e) => setFade(Math.min(600, Math.max(0, Number(e.target.value) || 0)))}
          />
        </label>
        <button type="submit" disabled={!name.trim()}>
          Save current look
        </button>
      </form>
    </section>
  );
}
