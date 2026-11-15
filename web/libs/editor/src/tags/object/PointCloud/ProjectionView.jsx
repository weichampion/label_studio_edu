import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "../../../utils/bem";
import { boxCorners, BOX_EDGES, localToWorld, MIN_SIZE, normalizeAngle, worldToLocal } from "./geometry";

/**
 * Orthographic 2D projections of the selected cuboid (as in SUSTechPOINTS / CVAT 3D):
 *  - top:  looking down,                      u = right side (-y), v = forward (+x)
 *  - side: looking from the right side,       u = forward (+x),    v = up (+z)
 *  - rear: looking forward from behind,       u = right side (-y), v = up (+z)
 *
 * Drag inside the box to move it, drag an edge to resize it, drag the round handle (top view) to rotate it.
 */
export const PROJECTIONS = {
  top: { title: "Top", u: { axis: 1, sign: -1 }, v: { axis: 0, sign: 1 }, depth: 2 },
  side: { title: "Side", u: { axis: 0, sign: 1 }, v: { axis: 2, sign: 1 }, depth: 1 },
  rear: { title: "Rear", u: { axis: 1, sign: -1 }, v: { axis: 2, sign: 1 }, depth: 0 },
};

const EDGE_TOLERANCE = 6;
const HANDLE_OFFSET = 18;
const HANDLE_RADIUS = 6;
const sizesOf = (box) => [box.length, box.width, box.height];

function toView(projection, local) {
  return [projection.u.sign * local[projection.u.axis], projection.v.sign * local[projection.v.axis]];
}

/** New box after dragging `mode` by (du, dv) metres in view space, starting from `start`. */
export function applyProjectionDrag(projection, start, mode, du, dv, angle = 0) {
  const sizes = sizesOf(start);
  const shift = [0, 0, 0];
  const { u, v } = projection;

  if (mode === "rotate") {
    return { ...start, yaw: normalizeAngle(start.yaw + angle) };
  }
  if (mode === "move") {
    shift[u.axis] += u.sign * du;
    shift[v.axis] += v.sign * dv;
  } else {
    const isU = mode === "u+" || mode === "u-";
    const axis = isU ? u.axis : v.axis;
    const sign = isU ? u.sign : v.sign;
    const delta = isU ? du : dv;
    const positive = mode.endsWith("+");
    const oldSize = sizes[axis];
    const newSize = Math.max(MIN_SIZE * 2, oldSize + (positive ? delta : -delta));
    const centerShift = ((newSize - oldSize) / 2) * (positive ? 1 : -1);

    sizes[axis] = newSize;
    shift[axis] += sign * centerShift;
  }
  const [x, y, z] = localToWorld(start, shift[0], shift[1], shift[2]);

  return { ...start, x, y, z, length: sizes[0], width: sizes[1], height: sizes[2] };
}

/** Indices of points within `radius` (xy) of the reference centre. */
function nearbyIndices(points, cx, cy, radius) {
  if (!points) return [];
  const r2 = radius * radius;
  const out = [];

  for (let i = 0; i < points.count; i++) {
    const dx = points.positions[i * 3] - cx;
    const dy = points.positions[i * 3 + 1] - cy;

    if (dx * dx + dy * dy <= r2) out.push(i);
  }
  return out;
}

export const ProjectionView = ({ name, box, cloud, overlay, color, editable, onChange, onStart, onEnd }) => {
  const projection = PROJECTIONS[name];
  const canvasRef = useRef(null);
  const wrapperRef = useRef(null);
  const [width, setWidth] = useState(240);
  const height = 170;
  // during a drag the frame (centre, yaw, scale) stays fixed, otherwise the view would follow the box
  const [frame, setFrame] = useState(null);
  const dragRef = useRef(null);
  const reference = frame ?? box;

  useEffect(() => {
    const el = wrapperRef.current;

    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(Math.max(120, el.clientWidth)));

    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const scale = useMemo(() => {
    const sizes = sizesOf(reference);
    const extentU = Math.max(0.5, sizes[projection.u.axis]);
    const extentV = Math.max(0.5, sizes[projection.v.axis]);

    return Math.min((width - 40) / (extentU * 1.6), (height - 40) / (extentV * 1.6));
  }, [reference, width, projection]);

  // nearby points, computed once per frame reference
  const radius = Math.hypot(width, height) / scale / 2 + 0.5;
  const nearby = useMemo(
    () => nearbyIndices(cloud, reference.x, reference.y, radius),
    [cloud, reference.x, reference.y, radius],
  );
  // points of the overlaid neighbouring scans (help sizing sparse, static objects)
  const nearbyOverlay = useMemo(
    () => nearbyIndices(overlay, reference.x, reference.y, radius),
    [overlay, reference.x, reference.y, radius],
  );

  const toScreen = useCallback(
    (local) => {
      const [pu, pv] = toView(projection, local);

      return [width / 2 + pu * scale, height / 2 - pv * scale];
    },
    [projection, scale, width],
  );

  useEffect(() => {
    const canvas = canvasRef.current;

    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;

    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = "#14171c";
    ctx.fillRect(0, 0, width, height);

    const sizes = sizesOf(box);
    // slice thickness along the viewing direction
    const refSizes = sizesOf(reference);
    const depthLimit = refSizes[projection.depth] / 2 + 0.3;

    if (overlay) {
      for (const i of nearbyOverlay) {
        const local = worldToLocal(
          reference,
          overlay.positions[i * 3],
          overlay.positions[i * 3 + 1],
          overlay.positions[i * 3 + 2],
        );

        if (Math.abs(local[projection.depth]) > depthLimit) continue;
        const [sx, sy] = toScreen(local);

        if (sx < 0 || sy < 0 || sx > width || sy > height) continue;
        ctx.fillStyle = "#4b5563";
        ctx.fillRect(sx - 1, sy - 1, 2, 2);
      }
    }

    if (cloud) {
      for (const i of nearby) {
        const world = [cloud.positions[i * 3], cloud.positions[i * 3 + 1], cloud.positions[i * 3 + 2]];
        const local = worldToLocal(reference, world[0], world[1], world[2]);

        if (Math.abs(local[projection.depth]) > depthLimit) continue;
        const [sx, sy] = toScreen(local);

        if (sx < 0 || sy < 0 || sx > width || sy > height) continue;
        const inBox = worldToLocal(box, world[0], world[1], world[2]).every(
          (value, axis) => Math.abs(value) <= sizes[axis] / 2,
        );

        ctx.fillStyle = inBox ? "#ffffff" : "#7d8794";
        ctx.fillRect(sx - 1, sy - 1, 2, 2);
      }
    }

    // current box, expressed in the reference frame
    const corners = boxCorners(box).map(([x, y, z]) => toScreen(worldToLocal(reference, x, y, z)));

    ctx.strokeStyle = color || "#ff9a3c";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (const [a, b] of BOX_EDGES) {
      ctx.moveTo(corners[a][0], corners[a][1]);
      ctx.lineTo(corners[b][0], corners[b][1]);
    }
    ctx.stroke();

    // heading: centre -> front face
    const centre = toScreen(worldToLocal(reference, box.x, box.y, box.z));
    const frontWorld = localToWorld(box, box.length / 2, 0, 0);
    const front = toScreen(worldToLocal(reference, frontWorld[0], frontWorld[1], frontWorld[2]));

    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(centre[0], centre[1]);
    ctx.lineTo(front[0], front[1]);
    ctx.stroke();
    ctx.setLineDash([]);

    if (name === "top" && editable) {
      const handle = rotationHandle();

      ctx.fillStyle = color || "#ff9a3c";
      ctx.beginPath();
      ctx.arc(handle[0], handle[1], HANDLE_RADIUS, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = "#c9d1d9";
    ctx.font = "12px sans-serif";
    ctx.fillText(projection.title, 8, 16);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [box, reference, cloud, nearby, overlay, nearbyOverlay, color, width, scale, editable]);

  function rotationHandle() {
    const frontWorld = localToWorld(box, box.length / 2, 0, 0);
    const front = toScreen(worldToLocal(reference, frontWorld[0], frontWorld[1], frontWorld[2]));
    const centre = toScreen(worldToLocal(reference, box.x, box.y, box.z));
    const dx = front[0] - centre[0];
    const dy = front[1] - centre[1];
    const len = Math.hypot(dx, dy) || 1;

    return [front[0] + (dx / len) * HANDLE_OFFSET, front[1] + (dy / len) * HANDLE_OFFSET];
  }

  function hitTest(sx, sy) {
    if (name === "top") {
      const [hx, hy] = rotationHandle();

      if (Math.hypot(sx - hx, sy - hy) <= HANDLE_RADIUS + 3) return "rotate";
    }
    const sizes = sizesOf(box);
    const halfU = (sizes[projection.u.axis] / 2) * scale;
    const halfV = (sizes[projection.v.axis] / 2) * scale;
    const du = sx - width / 2;
    const dv = height / 2 - sy;
    const withinU = Math.abs(du) <= halfU + EDGE_TOLERANCE;
    const withinV = Math.abs(dv) <= halfV + EDGE_TOLERANCE;

    if (!withinU || !withinV) return null;
    if (Math.abs(du - halfU) <= EDGE_TOLERANCE) return "u+";
    if (Math.abs(du + halfU) <= EDGE_TOLERANCE) return "u-";
    if (Math.abs(dv - halfV) <= EDGE_TOLERANCE) return "v+";
    if (Math.abs(dv + halfV) <= EDGE_TOLERANCE) return "v-";
    return "move";
  }

  const cursorFor = (mode) =>
    ({
      move: "move",
      rotate: "grab",
      "u+": "ew-resize",
      "u-": "ew-resize",
      "v+": "ns-resize",
      "v-": "ns-resize",
    })[mode] ?? "default";

  const position = (event) => {
    const rect = canvasRef.current.getBoundingClientRect();

    return [event.clientX - rect.left, event.clientY - rect.top];
  };

  const onPointerDown = (event) => {
    if (!editable || event.button !== 0) return;
    const [sx, sy] = position(event);
    const mode = hitTest(sx, sy);

    if (!mode) return;
    event.preventDefault();
    canvasRef.current.setPointerCapture(event.pointerId);
    const start = { ...box };

    dragRef.current = {
      mode,
      start,
      sx,
      sy,
      angle0: Math.atan2(-(sx - width / 2), height / 2 - sy),
      scale,
    };
    setFrame(start);
    onStart?.();
  };

  const onPointerMove = (event) => {
    const [sx, sy] = position(event);
    const drag = dragRef.current;

    if (!drag) {
      if (editable) canvasRef.current.style.cursor = cursorFor(hitTest(sx, sy));
      return;
    }
    const du = (sx - drag.sx) / drag.scale;
    const dv = -(sy - drag.sy) / drag.scale;
    // top view: u = -ly, v = lx  =>  angle in the box frame = atan2(ly, lx) = atan2(-u, v)
    const angle = Math.atan2(-(sx - width / 2), height / 2 - sy) - drag.angle0;

    onChange?.(applyProjectionDrag(projection, drag.start, drag.mode, du, dv, angle));
  };

  const onPointerUp = (event) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    canvasRef.current.releasePointerCapture?.(event.pointerId);
    setFrame(null);
    onEnd?.();
  };

  return (
    <div ref={wrapperRef} className={cn("pointcloud").elem("projection").toClassName()}>
      <canvas
        ref={canvasRef}
        style={{ width, height }}
        role="img"
        aria-label={`${projection.title} projection of the selected box`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />
    </div>
  );
};
