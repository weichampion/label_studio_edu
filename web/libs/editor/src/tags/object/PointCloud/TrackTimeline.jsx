import { observer } from "mobx-react";
import { useState } from "react";
import { Button } from "@humansignal/ui";

import { cn } from "../../../utils/bem";
import { AttributeBatch, valueColor } from "./AttributePanels";
import { formatValue, segments } from "./attributes";

const block = cn("pointcloud");
const percent = (frame, count) => `${((frame + 0.5) / Math.max(1, count)) * 100}%`;
const spanStyle = (start, end, count) => ({
  left: `${(start / count) * 100}%`,
  width: `${((end - start + 1) / count) * 100}%`,
});

/** Frames picked for batch attribute editing. */
const RangeBand = ({ range, count }) =>
  range ? <span className={block.elem("range").toClassName()} style={spanStyle(range.start, range.end, count)} /> : null;

/** One stripe per attribute under a selected track: runs of equal value, colored by value. */
const AttributeStripes = observer(({ item, track, onJump }) => {
  const count = item.framesCount;

  return item.attributeDefs.map((def) => (
    <div key={def.key} className={block.elem("track").mod({ attribute: true }).toClassName()}>
      <span className={block.elem("attribute-name").toClassName()} title={def.title}>
        {def.title}
      </span>
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: frames are also reachable with the slider and , . keys */}
      <div className={block.elem("lane").mod({ attribute: true }).toClassName()} onClick={onJump}>
        {segments(track.frameAttributes?.[def.key], count, def.default).map((seg) => {
          const color = valueColor(def, seg.value);

          return (
            <span
              key={seg.start}
              className={block.elem("value").toClassName()}
              style={{ ...spanStyle(seg.start, seg.end, count), background: color ?? "transparent" }}
              title={`${def.title}: ${formatValue(def, seg.value)} · frames ${seg.start + 1}–${seg.end + 1}`}
            />
          );
        })}
        <RangeBand range={item.attributeRange} count={count} />
        <span className={block.elem("playhead").toClassName()} style={{ left: percent(item.frame, count) }} />
      </div>
    </div>
  ));
});

/** Frame controls + one lane per track (lifespans as bars, keyframes as diamonds), like a video timeline. */
export const TrackTimeline = observer(({ item, playing, onTogglePlay }) => {
  const count = item.framesCount;
  const frame = item.frame;
  const tracks = item.cuboids.filter((r) => r.isTrack);
  const hasAttributes = item.attributeDefs.length > 0;
  const [showBatch, setShowBatch] = useState(false);

  const jumpFromLane = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;

    item.setFrame(Math.floor(ratio * count));
  };

  return (
    <div className={block.elem("timeline").toClassName()}>
      <div className={block.elem("frames").toClassName()} role="group" aria-label="Frames">
        <Button size="small" look="outlined" variant="neutral" onClick={() => item.stepFrame(-1)} tooltip=",">
          Previous
        </Button>
        <Button
          size="small"
          look={playing ? "filled" : "outlined"}
          variant="neutral"
          aria-pressed={playing}
          onClick={onTogglePlay}
          tooltip="Space"
        >
          {playing ? "Pause" : "Play"}
        </Button>
        <Button size="small" look="outlined" variant="neutral" onClick={() => item.stepFrame(1)} tooltip=".">
          Next
        </Button>
        <label className={block.elem("frame-slider").toClassName()}>
          <input
            aria-label="Frame"
            type="range"
            min={0}
            max={count - 1}
            step={1}
            value={frame}
            onChange={(e) => item.setFrame(Number(e.target.value))}
          />
        </label>
        <span className={block.elem("frame-label").toClassName()} aria-live="polite">
          Frame {frame + 1} / {count}
          {item.frames[frame]?.name && item.frames[frame].name !== String(frame) ? ` · ${item.frames[frame].name}` : ""}
          {item.loading ? " · loading…" : ""}
          {item.tracking ? " · tracking…" : ""}
        </span>
        {hasAttributes && (
          <Button
            size="small"
            look={showBatch ? "filled" : "outlined"}
            variant="neutral"
            aria-pressed={showBatch}
            aria-expanded={showBatch}
            onClick={() => setShowBatch(!showBatch)}
          >
            Batch Attributes
          </Button>
        )}
      </div>

      {hasAttributes && showBatch && <AttributeBatch item={item} />}

      {tracks.length > 0 && (
        <div className={block.elem("tracks").toClassName()} role="list" aria-label="Tracks">
          {tracks.map((track) => {
            const color = track.getOneColor();

            return (
              <div key={track.id} role="listitem" className={block.elem("track-group").toClassName()}>
                <div className={block.elem("track").mod({ selected: track.selected }).toClassName()}>
                  <button
                    type="button"
                    className={block.elem("track-name").toClassName()}
                    onClick={(event) => {
                      // Ctrl / ⌘ / Shift-click adds or removes a track, for batch editing several tracks
                      if (event.ctrlKey || event.metaKey || event.shiftKey) {
                        item.annotation.toggleRegionSelection(track, !track.selected);
                      } else if (track.selected) {
                        item.annotation.unselectAll();
                      } else {
                        item.annotation.selectArea(track);
                      }
                    }}
                    aria-pressed={track.selected}
                    title="Click to select, Ctrl/⌘-click to select several"
                  >
                    <span className={block.elem("swatch").toClassName()} style={{ background: color }} />
                    {track.labelName || "Unlabeled"} #{track.region_index ?? ""}
                  </button>
                  {/* biome-ignore lint/a11y/useKeyWithClickEvents: frames are also reachable with the slider and , . keys */}
                  <div className={block.elem("lane").toClassName()} onClick={jumpFromLane}>
                    {track.lifespans.map((span) => (
                      <span
                        key={span.start}
                        className={block.elem("span").toClassName()}
                        style={{
                          left: `${(span.start / count) * 100}%`,
                          width: `${((span.end - span.start + 1) / count) * 100}%`,
                          background: color,
                        }}
                      />
                    ))}
                    {track.sequence.map((k) => (
                      <span
                        key={k.frame}
                        className={block.elem("keyframe").mod({ ended: k.enabled === false }).toClassName()}
                        style={{ left: percent(k.frame, count) }}
                        title={`${k.enabled !== false ? "Keyframe" : "Track ends"} at frame ${k.frame + 1}`}
                      />
                    ))}
                    <RangeBand range={item.attributeRange} count={count} />
                    <span className={block.elem("playhead").toClassName()} style={{ left: percent(frame, count) }} />
                  </div>
                </div>
                {hasAttributes && track.selected && (
                  <AttributeStripes item={item} track={track} onJump={jumpFromLane} />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});
