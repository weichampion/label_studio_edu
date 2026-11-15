import { comparer, reaction } from "mobx";
import { observer } from "mobx-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@humansignal/ui";

import ObjectTag from "../../../components/Tags/Object";
import { cn } from "../../../utils/bem";
import { AttributeFields } from "./AttributePanels";
import { CameraView } from "./CameraView";
import { toDegrees, toRadians } from "./geometry";
import { ProjectionView } from "./ProjectionView";
import { SegmentationPanel } from "./SegmentationPanel";
import { OVERLAY_CHOICES } from "./PointCloud";
import { TrackTimeline } from "./TrackTimeline";
import { Viewer3D } from "./Viewer3D";
import "./PointCloud.prefix.css";

const HISTORY_KEY = "pointcloud-edit";
const block = cn("pointcloud");

const SHORTCUTS = [
  ["N", "Draw a new box (bird's-eye view)"],
  ["Esc", "Stop drawing"],
  ["B", "Switch bird's-eye / 3D view"],
  ["↑ ↓ ← →", "Move the selected box (Shift = 5×)"],
  ["R / F", "Raise / lower"],
  ["Q / E", "Rotate left / right 1° (Shift = 5°)"],
  ["T", "Flip heading 180°"],
  ["G", "Fit box to points (Shift+G also fits rotation)"],
  ["Delete", "Delete the selected box (the whole track in a sequence)"],
];

const SEQUENCE_SHORTCUTS = [
  [", / .", "Previous / next frame (Shift = 10 frames)"],
  ["Space", "Play / pause"],
  ["P", "Track the selected box into the next frame"],
  ["Shift+P", "Auto-track the next 10 frames"],
  ["X", "End the track here / bring it back here"],
  ["K", "Remove the keyframe in this frame"],
  ["O", "Overlay neighbouring frames on / off"],
  ["M", "Track the selected box through the sequence with the connected model"],
];

const SEGMENTATION_SHORTCUTS = [
  ["U", "Point label brush"],
  ["L", "Point label lasso"],
  ["Shift + drag", "Erase with the brush / lasso"],
  ["[ / ]", "Smaller / larger brush"],
  ["V", "Show / hide point labels"],
];

const LANE_SHORTCUTS = [
  ["J", "Draw a 3D polyline (lane line); click to add points"],
  ["Enter / double-click", "Finish the polyline"],
  ["Backspace", "Remove the last point while drawing"],
  ["Esc", "Cancel the polyline being drawn"],
  ["Alt+click", "Remove a vertex of the selected polyline"],
  ["Delete", "Remove the selected vertex, or the selected polyline"],
];

const polylineItems = (item) =>
  item.polylines.map((r) => ({
    id: r.id,
    points: r.displayPoints,
    color: r.getOneColor(),
    selected: r.selected,
    hidden: r.hidden,
  }));

const boxItems = (item) =>
  item.cuboids.map((r) => ({
    id: r.id,
    box: r.box,
    color: r.getOneColor(),
    selected: r.selected,
    // tracks are drawn only in the frames where the object is present
    hidden: r.hidden || !r.isVisible,
  }));

const visibleSelectedBox = (item) => {
  const region = item.selectedCuboid;

  return region?.isVisible ? region.box : null;
};

const NumberField = ({ label, value, step, onCommit, disabled, digits = 2 }) => {
  const [text, setText] = useState(value.toFixed(digits));

  useEffect(() => setText(value.toFixed(digits)), [value, digits]);

  const commit = () => {
    const parsed = Number.parseFloat(text);

    if (Number.isFinite(parsed) && Math.abs(parsed - value) > 1e-9) onCommit(parsed);
    else setText(value.toFixed(digits));
  };

  return (
    <label className={block.elem("field").toClassName()}>
      <span>{label}</span>
      <input
        type="number"
        step={step}
        value={text}
        disabled={disabled}
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

const TrackControls = observer(({ item, region, editable }) => {
  if (!region.isTrack) return null;
  const atEnd = item.frame >= item.framesCount - 1;

  return (
    <div className={block.elem("track-controls").toClassName()}>
      <span className={block.elem("track-state").toClassName()}>
        {region.isVisible ? (region.isKeyframe ? "Keyframe in this frame" : "Interpolated in this frame") : "Not in this frame"}
      </span>
      {editable && (
        <div className={block.elem("actions").toClassName()}>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            disabled={!region.isVisible || atEnd || item.tracking}
            onClick={() => item.autoTrack(region, 1)}
            tooltip="P"
          >
            Track Next Frame
          </Button>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            disabled={!region.isVisible || atEnd || item.tracking}
            onClick={() => item.autoTrack(region, 10)}
            tooltip="Shift+P"
          >
            Auto-Track 10 Frames
          </Button>
          {item.modelTracker && (
            <Button
              size="small"
              look="outlined"
              variant="neutral"
              waiting={item.modelTracking}
              disabled={item.modelTracking || item.tracking}
              onClick={() => item.trackWithModel(region)}
              tooltip={`M · ${item.modelTracker.backend ?? "ML backend"}`}
            >
              Track With Model
            </Button>
          )}
          <Button size="small" look="outlined" variant="neutral" onClick={() => region.toggleLifespanHere()} tooltip="X">
            {region.isVisible ? "End Track Here" : "Resume Track Here"}
          </Button>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            disabled={!region.isKeyframe || region.sequence.length <= 1}
            onClick={() => region.removeKeyframeHere()}
            tooltip="K"
          >
            Remove Keyframe
          </Button>
        </div>
      )}
      {item.modelTrackMessage && (
        <span className={block.elem("track-state").toClassName()} role="status">
          {item.modelTrackMessage}
        </span>
      )}
    </div>
  );
});

const LanePanel = observer(({ item, region }) => {
  const editable = !region.isReadOnly();
  const vertex = item.selectedVertex;
  const points = region.displayPoints;
  const selected = vertex !== null && points[vertex] ? points[vertex] : null;
  const [message, setMessage] = useState(null);

  useEffect(() => setMessage(null), [region.id]);
  const snap = () => {
    const snapped = region.snapToMarkings();

    setMessage(
      snapped < 0
        ? "This scan has no intensity, so markings can't be found."
        : snapped
          ? `${snapped} of ${points.length} points found a marking; the others follow their neighbours.`
          : "No painted marking found near this line.",
    );
  };

  return (
    <div className={block.elem("panel").toClassName()}>
      <div className={block.elem("panel-header").toClassName()}>
        <strong>{region.labelName || "Unlabeled line"}</strong>
        <span>
          {points.length} points · {region.length.toFixed(1)} m
        </span>
      </div>
      {item.isSequence && region.frame !== null && (
        <span className={block.elem("track-state").toClassName()}>
          Drawn in frame {region.frame + 1}
          {item.hasPoses ? ", shown in every frame through the ego poses" : ""}
        </span>
      )}
      {selected && (
        <fieldset className={block.elem("fields").toClassName()} disabled={!editable}>
          <legend>Point {vertex + 1}</legend>
          <NumberField label="x" value={selected[0]} step={0.1} onCommit={(x) => region.moveVertex(vertex, x, selected[1])} />
          <NumberField label="y" value={selected[1]} step={0.1} onCommit={(y) => region.moveVertex(vertex, selected[0], y)} />
          <NumberField label="z" value={selected[2]} step={0.05} onCommit={(z) => region.setVertexHeight(vertex, z)} />
        </fieldset>
      )}
      {editable && (
        <div className={block.elem("actions").toClassName()}>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            disabled={!item.cloud?.intensity}
            onClick={snap}
            tooltip="Move the points sideways onto the painted marking (uses LiDAR intensity)"
          >
            Snap to Markings
          </Button>
          <Button size="small" look="outlined" variant="neutral" onClick={() => region.snapToGround()}>
            Snap to Ground
          </Button>
          <Button size="small" look="outlined" variant="neutral" onClick={() => region.reverse()} tooltip="Lines follow the driving direction">
            Reverse Direction
          </Button>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            disabled={!selected || points.length <= 2}
            onClick={() => region.removeVertex(vertex) && item.setSelectedVertex(null)}
          >
            Remove Point
          </Button>
        </div>
      )}
      {message && (
        <span className={block.elem("track-state").toClassName()} role="status">
          {message}
        </span>
      )}
      <span className={block.elem("track-state").toClassName()}>
        Drag a point to move it (its height follows the road), drag a small midpoint handle to add one, Alt-click a
        point to remove it.
      </span>
    </div>
  );
});

const BoxPanel = observer(({ item, region, onDragStart, onDragEnd }) => {
  const box = region.box;
  const editable = !region.isReadOnly();
  const color = region.getOneColor();
  const update = (patch) => region.updateBox(patch);

  if (!region.isVisible) {
    return (
      <div className={block.elem("panel").toClassName()}>
        <div className={block.elem("panel-header").toClassName()}>
          <strong>{region.labelName || "Unlabeled box"}</strong>
        </div>
        <TrackControls item={item} region={region} editable={editable} />
      </div>
    );
  }

  return (
    <div className={block.elem("panel").toClassName()}>
      <div className={block.elem("panel-header").toClassName()}>
        <strong>{region.labelName || "Unlabeled box"}</strong>
        <span>{region.pointCount ?? "–"} points</span>
      </div>
      <TrackControls item={item} region={region} editable={editable} />
      <AttributeFields region={region} editable={editable} />
      <ProjectionView
        name="top"
        box={box}
        cloud={item.cloud}
        overlay={item.overlay}
        color={color}
        editable={editable}
        onStart={onDragStart}
        onChange={(b) => region.setBox(b)}
        onEnd={onDragEnd}
      />
      <ProjectionView
        name="side"
        box={box}
        cloud={item.cloud}
        overlay={item.overlay}
        color={color}
        editable={editable}
        onStart={onDragStart}
        onChange={(b) => region.setBox(b)}
        onEnd={onDragEnd}
      />
      <ProjectionView
        name="rear"
        box={box}
        cloud={item.cloud}
        overlay={item.overlay}
        color={color}
        editable={editable}
        onStart={onDragStart}
        onChange={(b) => region.setBox(b)}
        onEnd={onDragEnd}
      />
      <fieldset className={block.elem("fields").toClassName()} disabled={!editable}>
        <legend>Position (m)</legend>
        <NumberField label="x" value={box.x} step={0.1} onCommit={(x) => update({ x })} />
        <NumberField label="y" value={box.y} step={0.1} onCommit={(y) => update({ y })} />
        <NumberField label="z" value={box.z} step={0.1} onCommit={(z) => update({ z })} />
      </fieldset>
      <fieldset className={block.elem("fields").toClassName()} disabled={!editable}>
        <legend>Size (m)</legend>
        <NumberField label="length" value={box.length} step={0.1} onCommit={(length) => update({ length })} />
        <NumberField label="width" value={box.width} step={0.1} onCommit={(width) => update({ width })} />
        <NumberField label="height" value={box.height} step={0.1} onCommit={(height) => update({ height })} />
      </fieldset>
      <fieldset className={block.elem("fields").toClassName()} disabled={!editable}>
        <legend>Rotation</legend>
        <NumberField
          label="yaw (°)"
          value={toDegrees(box.yaw)}
          step={1}
          digits={1}
          onCommit={(deg) => update({ yaw: toRadians(deg) })}
        />
      </fieldset>
      {editable && (
        <div className={block.elem("actions").toClassName()}>
          <Button size="small" look="outlined" variant="neutral" onClick={() => region.fitToPoints()} tooltip="G">
            Fit to Points
          </Button>
          <Button
            size="small"
            look="outlined"
            variant="neutral"
            onClick={() => region.fitToPoints({ fitYaw: true })}
            tooltip="Shift+G"
          >
            Fit With Rotation
          </Button>
          <Button size="small" look="outlined" variant="neutral" onClick={() => region.rotate(Math.PI)} tooltip="T">
            Flip Heading
          </Button>
        </div>
      )}
    </div>
  );
});

const HtxPointCloudView = ({ item }) => {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const dragStartRef = useRef(null);
  const [showHelp, setShowHelp] = useState(false);
  const [showCameraPoints, setShowCameraPoints] = useState(false);
  const [playing, setPlaying] = useState(false);
  // the overlay size restored by the O key
  const lastOverlayRef = useRef(item.overlayCount || 3);

  // playback: advance one frame per tick once the current frame is on screen
  useEffect(() => {
    if (!playing) return undefined;
    const timer = setInterval(() => {
      if (item.loading) return;
      if (item.frame >= item.framesCount - 1) setPlaying(false);
      else item.stepFrame(1);
    }, 1000 / item.playbackFps);

    return () => clearInterval(timer);
  }, [playing, item]);

  const freezeHistory = useCallback(() => item.annotation?.history?.freeze(HISTORY_KEY), [item]);
  const unfreezeHistory = useCallback(() => item.annotation?.history?.unfreeze(HISTORY_KEY), [item]);

  useEffect(() => {
    const findRegion = (id) => item.regs.find((r) => r.id === id);
    let viewer;

    try {
      viewer = new Viewer3D(containerRef.current, {
      getSelectedId: () => (item.selectedCuboid?.isVisible ? item.selectedCuboid.id : null),
      canEdit: () => !!item.selectedCuboid && !item.selectedCuboid.isReadOnly(),
      onSelect: (id) => {
        const region = id ? findRegion(id) : null;

        if (region) {
          if (!region.selected) item.annotation.selectArea(region);
        } else if (item.selectedCuboid || item.selectedPolyline) {
          item.annotation.unselectAll();
        }
      },
      onDraw: (footprint) => {
        const area = item.addCuboidFromFootprint(footprint);

        if (area) item.annotation.selectArea(area);
      },
      onMoveStart: (id) => {
        dragStartRef.current = findRegion(id)?.box ?? null;
        freezeHistory();
      },
      onMove: (id, dx, dy) => {
        const start = dragStartRef.current;
        const region = findRegion(id);

        if (start && region) region.setBox({ ...start, x: start.x + dx, y: start.y + dy });
      },
      onMoveEnd: () => {
        dragStartRef.current = null;
        unfreezeHistory();
      },
      canEditLine: () => !!item.selectedPolyline && !item.selectedPolyline.isReadOnly(),
      onLineClick: (x, y) => item.addDraftPoint(x, y),
      onLineFinish: () => item.finishDraft(),
      onVertexSelect: (_, index) => item.setSelectedVertex(index),
      onVertexRemove: (id, index) => {
        if (findRegion(id)?.removeVertex(index)) item.setSelectedVertex(null);
      },
      onVertexInsert: (id, index, x, y) => {
        findRegion(id)?.insertVertex(index, x, y);
        item.setSelectedVertex(index);
      },
      onVertexMove: (id, index, x, y) => findRegion(id)?.moveVertex(index, x, y),
      onVertexDragStart: () => freezeHistory(),
      onVertexDragEnd: () => unfreezeHistory(),
      onPaintStart: (erase) => item.beginSegStroke(erase),
      onPaint: (indices) => item.paintSeg(indices),
      onPaintEnd: () => item.endSegStroke(),
      });
    } catch (e) {
      item.setLoadState(0, false, e?.message || String(e));
      return;
    }

    viewerRef.current = viewer;

    const disposers = [
      reaction(
        () => [item.cloud, item.groundZ],
        ([cloud, groundZ]) => {
          viewer.setGroundZ(groundZ);
          viewer.setCloud(cloud, item.colorMode);
          viewer.highlightBox(visibleSelectedBox(item));
        },
        { fireImmediately: true },
      ),
      reaction(
        () => item.colorMode,
        (mode) => {
          viewer.setColorMode(mode);
          viewer.highlightBox(visibleSelectedBox(item));
        },
      ),
      reaction(
        () => item.overlay,
        (overlay) => viewer.setOverlay(overlay),
        { fireImmediately: true },
      ),
      reaction(
        () => item.pointSize,
        (size) => viewer.setPointSize(size),
        { fireImmediately: true },
      ),
      reaction(
        () => item.viewMode,
        (mode) => viewer.setViewMode(mode),
        { fireImmediately: true },
      ),
      reaction(
        () => item.tool,
        (tool) => viewer.setTool(tool),
        { fireImmediately: true },
      ),
      reaction(
        () => boxItems(item),
        (items) => viewer.syncBoxes(items),
        { fireImmediately: true, equals: comparer.structural },
      ),
      reaction(
        () => visibleSelectedBox(item),
        (box) => viewer.highlightBox(box),
        { fireImmediately: true, equals: comparer.structural },
      ),
      reaction(
        // segVersion changes while a stroke paints into the same label array
        () => [item.segLabels, item.segVersion, item.segPalette, item.segVisible],
        ([labels, , palette, visible]) => viewer.setSegmentation(labels, palette, visible),
        { fireImmediately: true },
      ),
      reaction(
        () => [polylineItems(item), item.selectedVertex],
        ([items, vertex]) => viewer.syncPolylines(items, vertex),
        { fireImmediately: true, equals: comparer.structural },
      ),
      reaction(
        () => item.lineDraft,
        (draft) => viewer.setDraft(draft),
        { fireImmediately: true },
      ),
      reaction(
        () => item.selectedPolyline?.id ?? null,
        () => item.setSelectedVertex(null),
      ),
      reaction(
        () => item.brushSize,
        (size) => viewer.setBrushSize(size),
        { fireImmediately: true },
      ),
      reaction(
        () => item.focusRequest,
        (request) => {
          const region = request?.id ? findRegion(request.id) : null;

          if (region) viewer.focus(region.box);
        },
      ),
    ];

    return () => {
      disposers.forEach((dispose) => dispose());
      viewer.dispose();
      viewerRef.current = null;
    };
  }, [item]);

  const onKeyDown = (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
    const region = item.selectedCuboid;
    const editable = region && !region.isReadOnly();
    const fine = event.shiftKey ? 5 : 1;
    const key = event.key.toLowerCase();
    let handled = true;

    const plain = !event.ctrlKey && !event.metaKey && !event.altKey;

    if (item.isSequence && plain && (key === "," || key === "<")) item.stepFrame(event.shiftKey ? -10 : -1);
    else if (item.isSequence && plain && (key === "." || key === ">")) item.stepFrame(event.shiftKey ? 10 : 1);
    else if (item.isSequence && plain && key === " ") setPlaying(!playing);
    else if (item.isSequence && plain && key === "o") {
      if (item.overlayCount) {
        lastOverlayRef.current = item.overlayCount;
        item.setOverlay(0);
      } else {
        item.setOverlay(lastOverlayRef.current);
      }
    }
    else if (item.lineDraft && plain && key === "enter") item.finishDraft();
    else if (item.lineDraft && key === "escape") item.cancelDraft();
    else if (item.lineDraft && plain && key === "backspace") item.removeDraftPoint();
    else if (item.polylineControl && plain && key === "j") item.setTool(item.tool === "polyline" ? "select" : "polyline");
    else if (
      item.selectedPolyline &&
      !item.selectedPolyline.isReadOnly() &&
      plain &&
      (key === "delete" || key === "backspace")
    ) {
      const line = item.selectedPolyline;

      if (item.selectedVertex !== null && line.removeVertex(item.selectedVertex)) item.setSelectedVertex(null);
      else if (item.selectedVertex === null) line.deleteRegion();
    }
    else if (key === "n" && plain) item.setTool(item.tool === "draw" ? "select" : "draw");
    else if (key === "escape" && item.tool !== "select") item.setTool("select");
    else if (item.segControl && plain && key === "u") item.setTool(item.tool === "brush" ? "select" : "brush");
    else if (item.segControl && plain && key === "l") item.setTool(item.tool === "lasso" ? "select" : "lasso");
    else if (item.segControl && plain && key === "v") item.setSegOption({ segVisible: !item.segVisible });
    else if (item.tool === "brush" && plain && key === "[") item.setSegOption({ brushSize: item.brushSize - 4 });
    else if (item.tool === "brush" && plain && key === "]") item.setSegOption({ brushSize: item.brushSize + 4 });
    else if (key === "b" && !event.ctrlKey && !event.metaKey)
      item.setViewMode(item.viewMode === "bev" ? "perspective" : "bev");
    else if (!editable) handled = false;
    else if (region.isTrack && plain && key === "x") region.toggleLifespanHere();
    else if (region.isTrack && plain && key === "p") item.autoTrack(region, event.shiftKey ? 10 : 1);
    else if (region.isTrack && plain && key === "k") region.removeKeyframeHere();
    else if (region.isTrack && plain && key === "m" && item.modelTracker) item.trackWithModel(region);
    else if (key === "delete") region.deleteRegion();
    // everything below edits the box, which needs it to be present in this frame
    else if (!region.isVisible) handled = false;
    else if (key === "arrowup") region.translateLocal(0.1 * fine, 0, 0);
    else if (key === "arrowdown") region.translateLocal(-0.1 * fine, 0, 0);
    else if (key === "arrowleft") region.translateLocal(0, 0.1 * fine, 0);
    else if (key === "arrowright") region.translateLocal(0, -0.1 * fine, 0);
    else if (key === "r" && !event.ctrlKey && !event.metaKey) region.translateLocal(0, 0, 0.05 * fine);
    else if (key === "f" && !event.ctrlKey && !event.metaKey) region.translateLocal(0, 0, -0.05 * fine);
    else if (key === "q") region.rotate(toRadians(1) * fine);
    else if (key === "e") region.rotate(-toRadians(1) * fine);
    else if (key === "t") region.rotate(Math.PI);
    else if (key === "g") region.fitToPoints({ fitYaw: event.shiftKey });
    else handled = false;

    if (handled) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const selected = item.selectedCuboid;
  const cloud = item.cloud;
  const boxes = boxItems(item);
  const toggle = (active) => ({ variant: "neutral", look: active ? "filled" : "outlined", size: "small" });

  return (
    <ObjectTag item={item}>
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: the whole canvas area takes keyboard shortcuts */}
      <div className={block.toClassName()} tabIndex={0} onKeyDown={onKeyDown} aria-label="Point cloud annotation">
        <div className={block.elem("toolbar").toClassName()} role="toolbar" aria-label="Point cloud tools">
          <Button {...toggle(item.tool === "select")} aria-pressed={item.tool === "select"} onClick={() => item.setTool("select")}>
            Select
          </Button>
          <Button
            {...toggle(item.tool === "draw")}
            aria-pressed={item.tool === "draw"}
            disabled={!item.canDraw}
            tooltip="N"
            onClick={() => item.setTool(item.tool === "draw" ? "select" : "draw")}
          >
            Draw Box
          </Button>
          {item.polylineControl && (
            <Button
              {...toggle(item.tool === "polyline")}
              aria-pressed={item.tool === "polyline"}
              disabled={item.isReadOnly()}
              tooltip="J · click points along a lane line, double-click to finish"
              onClick={() => item.setTool(item.tool === "polyline" ? "select" : "polyline")}
            >
              Lane Line
            </Button>
          )}
          {item.segControl && (
            <>
              <Button
                {...toggle(item.tool === "brush")}
                aria-pressed={item.tool === "brush"}
                disabled={item.isReadOnly()}
                tooltip="U · paint point labels"
                onClick={() => item.setTool(item.tool === "brush" ? "select" : "brush")}
              >
                Brush
              </Button>
              <Button
                {...toggle(item.tool === "lasso")}
                aria-pressed={item.tool === "lasso"}
                disabled={item.isReadOnly()}
                tooltip="L · label the points inside a drawn outline"
                onClick={() => item.setTool(item.tool === "lasso" ? "select" : "lasso")}
              >
                Lasso
              </Button>
            </>
          )}
          <span className={block.elem("divider").toClassName()} />
          <Button
            {...toggle(item.viewMode === "bev")}
            aria-pressed={item.viewMode === "bev"}
            tooltip="B"
            onClick={() => item.setViewMode("bev")}
          >
            {"Bird's-Eye"}
          </Button>
          <Button
            {...toggle(item.viewMode === "perspective")}
            aria-pressed={item.viewMode === "perspective"}
            tooltip="B"
            onClick={() => item.setViewMode("perspective")}
          >
            3D
          </Button>
          <span className={block.elem("divider").toClassName()} />
          <label className={block.elem("control").toClassName()}>
            <span>Color</span>
            <select value={item.colorMode} onChange={(e) => item.setColorMode(e.target.value)}>
              <option value="height">Height</option>
              <option value="intensity" disabled={!cloud?.intensity}>
                Intensity
              </option>
              <option value="distance">Distance</option>
              <option value="rgb" disabled={!cloud?.colors}>
                RGB
              </option>
            </select>
          </label>
          <label className={block.elem("control").toClassName()}>
            <span>Point size</span>
            <input
              type="range"
              min={1}
              max={6}
              step={0.5}
              value={item.pointSize}
              onChange={(e) => item.setPointSize(e.target.value)}
            />
          </label>
          {item.isSequence && (
            <>
              <span className={block.elem("divider").toClassName()} />
              <label className={block.elem("control").toClassName()}>
                <span>Overlay</span>
                <select
                  value={item.overlayCount}
                  onChange={(e) => item.setOverlay(Number(e.target.value))}
                  title="Show neighbouring frames in this frame's coordinates (O)"
                >
                  {OVERLAY_CHOICES.map((n) => (
                    <option key={n} value={n}>
                      {n === 0 ? "Off" : `${n} frame${n > 1 ? "s" : ""}`}
                    </option>
                  ))}
                </select>
              </label>
              <label className={block.elem("control").toClassName()}>
                <input
                  type="checkbox"
                  checked={item.overlayFuture}
                  disabled={!item.overlayCount}
                  onChange={(e) => item.setOverlay(item.overlayCount, e.target.checked)}
                />
                <span>Later frames too</span>
              </label>
            </>
          )}
          <span className={block.elem("spacer").toClassName()} />
          <Button {...toggle(showHelp)} aria-pressed={showHelp} onClick={() => setShowHelp(!showHelp)}>
            Shortcuts
          </Button>
        </div>

        {showHelp && (
          <dl className={block.elem("help").toClassName()}>
            {[
              ...SHORTCUTS,
              ...(item.isSequence ? SEQUENCE_SHORTCUTS : []),
              ...(item.segControl ? SEGMENTATION_SHORTCUTS : []),
              ...(item.polylineControl ? LANE_SHORTCUTS : []),
            ].map(([key, text]) => (
              <div key={key}>
                <dt>
                  <kbd>{key}</kbd>
                </dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
        )}

        <div className={block.elem("body").toClassName()} style={{ height: item.viewHeight }}>
          <div className={block.elem("main").toClassName()}>
            <div ref={containerRef} className={block.elem("canvas").toClassName()} />
            <div className={block.elem("status").toClassName()} aria-live="polite">
              {item.loading && "Loading point cloud…"}
              {item.error && `Can't load the point cloud: ${item.error}`}
              {!item.loading && !item.error && cloud && (
                <>
                  {item.isSequence && `Frame ${item.frame + 1}/${item.framesCount} · `}
                  {cloud.count.toLocaleString()} points
                  {item.cuboidControl && ` · ${item.visibleCuboids.length} boxes`}
                  {item.overlay && ` · +${item.overlay.offsets.length} frames overlaid`}
                  {item.overlayLoading && " · loading overlay…"}
                  {item.overlayCount > 0 &&
                    !item.hasPoses &&
                    " · no ego poses: frames overlaid without motion compensation"}
                  {item.tool === "draw" && " · drag on the ground to draw a box, right-drag to pan"}
                  {item.tool === "brush" && " · drag to paint, Shift erases, right-drag pans"}
                  {item.tool === "lasso" && " · draw an outline around points, Shift erases, right-drag pans"}
                  {item.tool === "polyline" &&
                    (item.lineDraft?.length
                      ? ` · ${item.lineDraft.length} point${item.lineDraft.length === 1 ? "" : "s"}: click to continue, double-click or Enter to finish, Esc to cancel`
                      : " · click on the road to start a lane line, right-drag to pan")}
                </>
              )}
            </div>
          </div>
          <aside className={block.elem("side").toClassName()} aria-label="Details">
            {item.segControl && (item.tool === "brush" || item.tool === "lasso" || (!selected && !item.selectedPolyline)) && (
              <SegmentationPanel item={item} />
            )}
            {item.tool === "brush" || item.tool === "lasso" ? null : item.selectedPolyline ? (
              <LanePanel item={item} region={item.selectedPolyline} />
            ) : selected ? (
              <BoxPanel
                item={item}
                region={selected}
                onDragStart={freezeHistory}
                onDragEnd={unfreezeHistory}
              />
            ) : !item.cuboidControl && !item.polylineControl ? null : (
              <p className={block.elem("hint").toClassName()}>
                {item.cuboidControl && (
                  <>
                    Pick a label, press <kbd>N</kbd> and drag a rectangle in the bird&rsquo;s-eye view to add a box. Click
                    a box to select it, then refine it in the top, side and rear views.{" "}
                  </>
                )}
                {item.polylineControl && (
                  <>
                    Press <kbd>J</kbd> and click along a lane marking to draw a 3D line; intensity coloring makes paint
                    easy to see.
                  </>
                )}
              </p>
            )}
          </aside>
        </div>

        {item.isSequence && (
          <TrackTimeline item={item} playing={playing} onTogglePlay={() => setPlaying(!playing)} />
        )}

        {item.cameras.length > 0 && (
          <div className={block.elem("cameras-wrapper").toClassName()}>
            <label className={block.elem("control").toClassName()}>
              <input
                type="checkbox"
                checked={showCameraPoints}
                onChange={(e) => setShowCameraPoints(e.target.checked)}
              />
              <span>Project points onto camera images</span>
            </label>
            <div className={block.elem("cameras").toClassName()}>
              {item.cameras.map((camera) => (
                <CameraView
                  key={`${camera.name}-${camera.url}`}
                  camera={camera}
                  boxes={boxes}
                  cloud={cloud}
                  showPoints={showCameraPoints}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </ObjectTag>
  );
};

export const HtxPointCloud = observer(HtxPointCloudView);
