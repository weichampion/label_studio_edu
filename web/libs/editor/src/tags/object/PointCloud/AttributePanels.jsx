import { observer } from "mobx-react";
import { useEffect, useState } from "react";
import { Button } from "@humansignal/ui";

import { cn } from "../../../utils/bem";
import { formatValue } from "./attributes";

const block = cn("pointcloud");

/** Colors for attribute values on the timeline, by position in the choice list. */
const VALUE_COLORS = ["#8c8c8c", "#36cfc9", "#faad14", "#f5222d", "#722ed1", "#52c41a", "#2f54eb", "#eb2f96"];

export function valueColor(def, value) {
  if (def.choices === null) return value ? VALUE_COLORS[1] : null;
  const index = def.choices.indexOf(String(value));

  return index < 0 ? null : VALUE_COLORS[index % VALUE_COLORS.length];
}

const AttributeInput = ({ def, value, disabled, onChange }) => {
  if (def.choices === null) {
    return (
      <label className={block.elem("attribute").toClassName()}>
        <input type="checkbox" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span>{def.title}</span>
      </label>
    );
  }
  return (
    <label className={block.elem("attribute").toClassName()}>
      <span>{def.title}</span>
      <select value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {def.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    </label>
  );
};

/** Attributes of the selected box in the frame on screen. For a track a change holds until its next change. */
export const AttributeFields = observer(({ region, editable }) => {
  const defs = region.attributeDefs;

  if (!defs.length) return null;
  const values = region.attributesAtFrame();

  return (
    <fieldset className={block.elem("attributes").toClassName()} disabled={!editable}>
      <legend>{region.isTrack ? `Attributes in frame ${region.currentFrame + 1}` : "Attributes"}</legend>
      {defs.map((def) => (
        <AttributeInput
          key={def.key}
          def={def}
          value={values[def.key]}
          disabled={!editable}
          onChange={(value) => region.setAttribute(def.key, value)}
        />
      ))}
      {region.isTrack && (
        <span className={block.elem("track-state").toClassName()}>
          Applies from this frame until the next change. Use “Batch Attributes” below the timeline for a range of
          frames or several tracks.
        </span>
      )}
    </fieldset>
  );
});

const SCOPES = [
  ["selected", "Selected tracks"],
  ["label", "Tracks with the same label"],
  ["all", "All tracks"],
];

export function batchTargets(item, scope) {
  const tracks = item.cuboids.filter((r) => r.isTrack);
  const selected = tracks.filter((r) => r.selected);

  if (scope === "all") return tracks;
  if (scope === "label") {
    const labels = new Set(selected.map((r) => r.labelName ?? null));

    return tracks.filter((r) => labels.has(r.labelName ?? null));
  }
  return selected;
}

/** 1-based frame number input, committed on blur / Enter. */
const FrameInput = ({ label, frame, max, onCommit }) => {
  const [text, setText] = useState(String(frame + 1));

  useEffect(() => setText(String(frame + 1)), [frame]);
  const commit = () => {
    const parsed = Number.parseInt(text, 10);

    if (Number.isFinite(parsed)) onCommit(Math.min(Math.max(1, parsed), max) - 1);
    else setText(String(frame + 1));
  };

  return (
    <label className={block.elem("field").toClassName()}>
      <span>{label}</span>
      <input
        type="number"
        min={1}
        max={max}
        step={1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") commit();
        }}
      />
    </label>
  );
};

/**
 * Batch editing along tracks: set one attribute in a range of frames for the selected tracks, all tracks of a
 * label, or every track — one undo step. The range is drawn on the timeline.
 */
export const AttributeBatch = observer(({ item }) => {
  const defs = item.attributeDefs;
  const [key, setKey] = useState(defs[0]?.key ?? "");
  const def = defs.find((d) => d.key === key) ?? defs[0];
  const [value, setValue] = useState(def ? def.default : "");
  const [scope, setScope] = useState("selected");
  const [message, setMessage] = useState(null);

  // a fresh range covers the whole sequence; it is cleared when the panel closes
  useEffect(() => {
    if (!item.attributeRange) item.setAttributeRange({ start: 0, end: item.framesCount - 1 });
    return () => item.setAttributeRange(null);
  }, [item]);

  useEffect(() => {
    if (def) setValue(def.default);
    setMessage(null);
  }, [def?.key]);

  if (!def) return null;
  const range = item.attributeRange ?? { start: 0, end: item.framesCount - 1 };
  const targets = batchTargets(item, scope);
  const editable = !item.isReadOnly();
  const setRange = (patch) => {
    item.setAttributeRange({ ...range, ...patch });
    setMessage(null);
  };
  const lifespan = () => {
    const spans = targets.flatMap((r) => r.lifespans);

    if (spans.length) {
      setRange({ start: Math.min(...spans.map((s) => s.start)), end: Math.max(...spans.map((s) => s.end)) });
    }
  };
  const apply = () => {
    const changed = item.applyAttributeToTracks(targets, def.key, value, range.start, range.end);
    const frames = `frames ${range.start + 1}–${range.end + 1}`;

    setMessage(
      changed
        ? `${def.title} = ${formatValue(def, value)} in ${frames} of ${changed} track${changed > 1 ? "s" : ""}`
        : `Nothing to change: ${def.title} is already ${formatValue(def, value)} there`,
    );
  };

  return (
    <section className={block.elem("batch").toClassName()} aria-label="Batch attributes">
      <div className={block.elem("batch-row").toClassName()}>
        <label className={block.elem("control").toClassName()}>
          <span>Tracks</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            {SCOPES.map(([id, text]) => (
              <option key={id} value={id}>
                {text}
              </option>
            ))}
          </select>
        </label>
        <span className={block.elem("track-state").toClassName()}>
          {targets.length} track{targets.length === 1 ? "" : "s"}
          {scope !== "all" && " · Ctrl/⌘-click track names to select several"}
        </span>
      </div>
      <div className={block.elem("batch-row").toClassName()}>
        <FrameInput label="From frame" frame={range.start} max={item.framesCount} onCommit={(start) => setRange({ start })} />
        <FrameInput label="To frame" frame={range.end} max={item.framesCount} onCommit={(end) => setRange({ end })} />
        <Button size="small" look="outlined" variant="neutral" onClick={() => setRange({ start: item.frame })}>
          Start Here
        </Button>
        <Button size="small" look="outlined" variant="neutral" onClick={() => setRange({ end: item.frame })}>
          End Here
        </Button>
        <Button size="small" look="outlined" variant="neutral" disabled={!targets.length} onClick={lifespan}>
          Track Lifespan
        </Button>
        <Button
          size="small"
          look="outlined"
          variant="neutral"
          onClick={() => setRange({ start: 0, end: item.framesCount - 1 })}
        >
          All Frames
        </Button>
      </div>
      <div className={block.elem("batch-row").toClassName()}>
        <label className={block.elem("control").toClassName()}>
          <span>Attribute</span>
          <select value={def.key} onChange={(e) => setKey(e.target.value)}>
            {defs.map((d) => (
              <option key={d.key} value={d.key}>
                {d.title}
              </option>
            ))}
          </select>
        </label>
        <AttributeInput def={{ ...def, title: "Value" }} value={value} onChange={setValue} />
        <Button
          size="small"
          look="filled"
          variant="primary"
          disabled={!editable || !targets.length}
          onClick={apply}
        >
          Apply to {targets.length} Track{targets.length === 1 ? "" : "s"}
        </Button>
      </div>
      {message && (
        <span className={block.elem("track-state").toClassName()} role="status">
          {message}
        </span>
      )}
    </section>
  );
});
