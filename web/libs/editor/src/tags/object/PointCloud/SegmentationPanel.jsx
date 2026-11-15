import { observer } from "mobx-react";
import { useEffect, useState } from "react";
import { Button } from "@humansignal/ui";

import { cn } from "../../../utils/bem";

const block = cn("pointcloud");

const HeightInput = ({ label, value, onCommit }) => {
  const [text, setText] = useState(String(value));

  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const parsed = Number.parseFloat(text);

    if (Number.isFinite(parsed)) onCommit(parsed);
    else setText(String(value));
  };

  return (
    <label className={block.elem("field").toClassName()}>
      <span>{label}</span>
      <input
        type="number"
        step={0.1}
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

/** Tools and statistics for `<PointLabels>`: class to paint, brush options, fill helpers. */
export const SegmentationPanel = observer(({ item }) => {
  const region = item.segRegion;
  const labels = item.segLabels;
  const count = item.cloud?.count ?? 0;
  const counts = region && labels ? region.classCounts(labels) : [];
  const labeled = counts.reduce((n, c) => n + c.count, 0);
  const paintClass = item.paintClass;
  const paintColor = item.segClasses.find((c) => c.name === paintClass)?.color;
  const colorOf = (name) => item.segClasses.find((c) => c.name === name)?.color ?? "#9e9e9e";
  const editable = !item.isReadOnly();
  const painting = item.tool === "brush" || item.tool === "lasso";
  const height = item.segHeight;

  return (
    <div className={block.elem("panel").toClassName()} aria-label="Point labels">
      <div className={block.elem("panel-header").toClassName()}>
        <strong>Point Labels</strong>
        <span>
          {count ? `${labeled.toLocaleString()} / ${count.toLocaleString()} points` : "–"}
          {count ? ` (${Math.round((labeled / count) * 100)}%)` : ""}
        </span>
      </div>

      <div className={block.elem("seg-class").toClassName()}>
        {item.segErase ? (
          <span>Erasing</span>
        ) : paintClass ? (
          <>
            <span className={block.elem("swatch").toClassName()} style={{ background: paintColor }} />
            <span>Painting {paintClass}</span>
          </>
        ) : (
          <span className={block.elem("track-state").toClassName()}>Pick a class in the point labels list.</span>
        )}
      </div>

      {!painting && editable && (
        <span className={block.elem("track-state").toClassName()}>
          Press <kbd>U</kbd> for the brush or <kbd>L</kbd> for the lasso. Hold <kbd>Shift</kbd> to erase.
        </span>
      )}

      <fieldset className={block.elem("attributes").toClassName()} disabled={!editable}>
        <legend>Painting</legend>
        <label className={block.elem("attribute").toClassName()}>
          <span>Eraser (or hold Shift)</span>
          <input
            type="checkbox"
            checked={item.segErase}
            onChange={(e) => item.setSegOption({ segErase: e.target.checked })}
          />
        </label>
        {item.tool === "brush" && (
          <label className={block.elem("attribute").toClassName()}>
            <span>Brush size [ ]</span>
            <input
              type="range"
              min={4}
              max={120}
              step={2}
              value={item.brushSize}
              onChange={(e) => item.setSegOption({ brushSize: Number(e.target.value) })}
            />
          </label>
        )}
        <label className={block.elem("attribute").toClassName()}>
          <span>Only unlabeled points</span>
          <input
            type="checkbox"
            checked={item.segOnlyUnlabeled}
            onChange={(e) => item.setSegOption({ segOnlyUnlabeled: e.target.checked })}
          />
        </label>
        <label className={block.elem("attribute").toClassName()}>
          <span>Height filter (m above ground)</span>
          <input
            type="checkbox"
            checked={!!height}
            onChange={(e) => item.setSegOption({ segHeight: e.target.checked ? { min: 0.3, max: 3 } : null })}
          />
        </label>
        {height && (
          <div className={block.elem("batch-row").toClassName()}>
            <HeightInput label="from" value={height.min} onCommit={(min) => item.setSegOption({ segHeight: { ...height, min } })} />
            <HeightInput label="to" value={height.max} onCommit={(max) => item.setSegOption({ segHeight: { ...height, max } })} />
          </div>
        )}
        <label className={block.elem("attribute").toClassName()}>
          <span>Show labels (V)</span>
          <input
            type="checkbox"
            checked={item.segVisible}
            onChange={(e) => item.setSegOption({ segVisible: e.target.checked })}
          />
        </label>
      </fieldset>

      {editable && (
        <div className={block.elem("actions").toClassName()}>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            disabled={!paintClass || !item.cloud}
            onClick={() => item.labelSegGround()}
            tooltip="Label the ground points of this frame with the selected class"
          >
            Label Ground{paintClass ? ` as ${paintClass}` : ""}
          </Button>
          {item.cuboidControl && (
            <Button
              size="small"
              look="outlined"
              variant="neutral"
              disabled={!item.visibleCuboids.length || !item.cloud}
              onClick={() => item.fillSegFromBoxes()}
              tooltip="Points inside each 3D box take the class with the box's label name"
            >
              Fill From Boxes
            </Button>
          )}
          {item.isSequence && (
            <>
              <Button
                size="small"
                look="outlined"
                variant="neutral"
                disabled={item.frame === 0 || !region?.frames[item.frame - 1]}
                onClick={() => item.copySegFromFrame(-1)}
              >
                Copy From Previous Frame
              </Button>
              <Button
                size="small"
                look="outlined"
                variant="neutral"
                disabled={item.frame >= item.framesCount - 1 || !region?.frames[item.frame + 1]}
                onClick={() => item.copySegFromFrame(1)}
              >
                Copy From Next Frame
              </Button>
            </>
          )}
          <Button
            size="small"
            look="outlined"
            variant="negative"
            disabled={!labeled && !item.segMismatch}
            onClick={() => item.clearSegFrame()}
          >
            Clear Frame
          </Button>
        </div>
      )}

      {item.segMismatch && (
        <span className={block.elem("track-state").toClassName()} role="alert">
          The stored labels of this frame were made for a different number of points; they are not shown.
        </span>
      )}
      {item.segMessage && (
        <span className={block.elem("track-state").toClassName()} role="status">
          {item.segMessage}
        </span>
      )}

      {counts.length > 0 && (
        <ul className={block.elem("seg-counts").toClassName()} aria-label="Points per class in this frame">
          {counts.map((c) => (
            <li key={c.code}>
              <span className={block.elem("swatch").toClassName()} style={{ background: colorOf(c.name) }} />
              <span>{c.name}</span>
              <span>{c.count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
