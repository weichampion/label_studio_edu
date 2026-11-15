/**
 * 3D polylines (lane lines, road edges, stop lines) on LiDAR point clouds.
 *
 * A polyline is a list of [x, y, z] vertices in the LiDAR frame. In a sequence it is stored in the coordinates
 * of one reference frame (`frame`) and shown in every other frame through the ego poses, because lane markings
 * don't move: draw once, check everywhere. Without poses all frames share one coordinate system.
 *
 * The Python exporter (label_studio/pointcloud3d/formats.py, lanes_in_frame) implements the same transform.
 */

export type Vec3 = [number, number, number];

/** Apply a 4x4 row-major rigid transform to points. */
export function transformPoints(points: Vec3[], m: number[]): Vec3[] {
  return points.map(([x, y, z]) => [
    m[0] * x + m[1] * y + m[2] * z + m[3],
    m[4] * x + m[5] * y + m[6] * z + m[7],
    m[8] * x + m[9] * y + m[10] * z + m[11],
  ]);
}

export function polylineLength(points: Vec3[]): number {
  let total = 0;

  for (let i = 1; i < points.length; i++) {
    total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1], points[i][2] - points[i - 1][2]);
  }
  return total;
}

/** Valid vertex list from stored data (drops malformed entries). */
export function normalizePoints(raw: unknown): Vec3[] {
  if (!Array.isArray(raw)) return [];
  const out: Vec3[] = [];

  for (const p of raw) {
    const v = Array.isArray(p) ? p.map(Number) : p && typeof p === "object" ? [(p as any).x, (p as any).y, (p as any).z].map(Number) : [];

    if (v.length >= 2 && Number.isFinite(v[0]) && Number.isFinite(v[1])) out.push([v[0], v[1], Number.isFinite(v[2]) ? v[2] : 0]);
  }
  return out;
}

/**
 * Height of the ground at (x, y): a low percentile of the points within `radius` (lane markings lie on the
 * road surface; the percentile ignores single outliers below it). Points more than `maxAbove` over the scan's
 * ground estimate (cars, walls) are ignored. Falls back to `fallback` where there are no points.
 */
export function groundHeightAt(
  positions: Float32Array,
  count: number,
  x: number,
  y: number,
  { radius = 0.8, fallback = 0, groundZ = fallback, maxAbove = 1.2 } = {},
): number {
  const r2 = radius * radius;
  const zs: number[] = [];

  for (let i = 0; i < count; i++) {
    const dx = positions[i * 3] - x;
    const dy = positions[i * 3 + 1] - y;
    const z = positions[i * 3 + 2];

    if (dx * dx + dy * dy <= r2 && z <= groundZ + maxAbove) zs.push(z);
  }
  if (!zs.length) return fallback;
  zs.sort((a, b) => a - b);
  return zs[Math.floor((zs.length - 1) * 0.2)];
}

/** Distance from (px, py) to the segment a-b in 2D, and the position along it (0..1). */
export function segmentDistance2D(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;

  return { distance: Math.hypot(px - (ax + dx * t), py - (ay + dy * t)), t };
}

/** Closest segment of a screen-space polyline to (px, py): {index, t, distance}; index -1 for < 2 vertices. */
export function closestSegment(screen: [number, number][], px: number, py: number) {
  let best = { index: -1, t: 0, distance: Number.POSITIVE_INFINITY };

  for (let i = 1; i < screen.length; i++) {
    const [ax, ay] = screen[i - 1];
    const [bx, by] = screen[i];

    if (![ax, ay, bx, by].every(Number.isFinite)) continue;
    const hit = segmentDistance2D(px, py, ax, ay, bx, by);

    if (hit.distance < best.distance) best = { index: i - 1, t: hit.t, distance: hit.distance };
  }
  return best;
}

export interface MarkingCloud {
  positions: Float32Array;
  count: number;
  intensity?: Float32Array | null;
}

/**
 * Move each vertex sideways onto the painted lane marking under it.
 *
 * Lane paint is retro-reflective, so its LiDAR returns are much brighter than the asphalt around it. For every
 * vertex, the road-surface points within `along` metres along the line, `lateral` metres to either side and
 * `height` of the vertex are taken; the bright ones (at least `contrast` times the local median intensity)
 * give the marking's sideways offset. Vertices with no marking nearby (gaps of dashed lines, sparse far rings)
 * take the average shift of their neighbours. Intensity units don't matter, only the contrast.
 * Returns the moved points and how many vertices found a marking.
 */
export function snapToMarkings(
  points: Vec3[],
  cloud: MarkingCloud,
  { along = 2, lateral = 0.6, height = 0.15, minBright = 2, contrast = 2.5 } = {},
): { points: Vec3[]; snapped: number } {
  const { positions, count, intensity } = cloud;

  if (!intensity || points.length < 2) return { points: points.map((p) => [...p] as Vec3), snapped: 0 };
  const shifts: (number | null)[] = [];
  const normals: [number, number][] = [];

  for (let v = 0; v < points.length; v++) {
    const prev = points[Math.max(0, v - 1)];
    const next = points[Math.min(points.length - 1, v + 1)];
    let tx = next[0] - prev[0];
    let ty = next[1] - prev[1];
    const len = Math.hypot(tx, ty) || 1;

    tx /= len;
    ty /= len;
    const nx = -ty;
    const ny = tx;

    normals.push([nx, ny]);
    const [vx, vy, vz] = points[v];
    const near: { offset: number; value: number }[] = [];

    for (let i = 0; i < count; i++) {
      const dx = positions[i * 3] - vx;
      const dy = positions[i * 3 + 1] - vy;
      const a = dx * tx + dy * ty;
      const l = dx * nx + dy * ny;

      if (Math.abs(a) > along || Math.abs(l) > lateral || Math.abs(positions[i * 3 + 2] - vz) > height) continue;
      near.push({ offset: l, value: intensity[i] });
    }
    if (near.length < minBright * 2) {
      shifts.push(null);
      continue;
    }
    const sorted = near.map((n) => n.value).sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];

    if (!(median > 0)) {
      shifts.push(null);
      continue;
    }
    const bright = near
      .filter((n) => n.value >= contrast * median)
      .map((n) => n.offset)
      .sort((a, b) => a - b);

    shifts.push(bright.length >= minBright ? bright[Math.floor(bright.length / 2)] : null);
  }

  const snapped = shifts.filter((s) => s !== null).length;
  const filled = shifts.map((s, v) => {
    if (s !== null) return s;
    let before: number | null = null;
    let after: number | null = null;

    for (let k = v - 1; k >= 0 && before === null; k--) before = shifts[k];
    for (let k = v + 1; k < shifts.length && after === null; k++) after = shifts[k];
    if (before !== null && after !== null) return (before + after) / 2;
    return before ?? after ?? 0;
  });

  return {
    points: points.map(([x, y, z], v) => [x + normals[v][0] * filled[v], y + normals[v][1] * filled[v], z] as Vec3),
    snapped,
  };
}
