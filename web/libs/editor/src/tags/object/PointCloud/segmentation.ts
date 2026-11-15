/**
 * Point cloud semantic segmentation: one class per point.
 *
 * In the editor a frame's labels are a Uint16Array with one code per point in file order: 0 = unlabeled,
 * k = the k-th entry of the result's `classes` list (1-based). Stored compactly as run-length pairs
 * (code, run length), each written as an unsigned LEB128 varint, then base64:
 *
 *   "value": {
 *     "classes": ["Road", "Car"],
 *     "frames": [{ "frame": 0, "count": 120000, "rle": "AYDqAQ..." }],
 *     "pointlabels": ["Road", "Car"]
 *   }
 *
 * LiDAR scans are stored ring by ring, so neighbouring points in the file are mostly neighbours in space and
 * the runs are long. The Python exporter (label_studio/pointcloud3d/formats.py, decode_point_labels) reads
 * the same encoding.
 */

/* ------------------------------------------------------------------------------------------------
 * encoding
 * ---------------------------------------------------------------------------------------------- */

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64_INDEX = new Int16Array(128).fill(-1);

for (let i = 0; i < B64.length; i++) B64_INDEX[B64.charCodeAt(i)] = i;

export function toBase64(bytes: Uint8Array): string {
  let out = "";

  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i];
    const b = i + 1 < bytes.length ? bytes[i + 1] : 0;
    const c = i + 2 < bytes.length ? bytes[i + 2] : 0;

    out += B64[a >> 2] + B64[((a & 3) << 4) | (b >> 4)];
    out += i + 1 < bytes.length ? B64[((b & 15) << 2) | (c >> 6)] : "=";
    out += i + 2 < bytes.length ? B64[c & 63] : "=";
  }
  return out;
}

export function fromBase64(text: string): Uint8Array {
  const clean = text.replace(/[^A-Za-z0-9+/]/g, "");
  const out = new Uint8Array(Math.floor((clean.length * 3) / 4));
  let bits = 0;
  let value = 0;
  let n = 0;

  for (let i = 0; i < clean.length; i++) {
    const v = B64_INDEX[clean.charCodeAt(i)];

    if (v < 0) throw new Error("Invalid base64");
    value = (value << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[n++] = (value >> bits) & 0xff;
    }
  }
  return out.subarray(0, n);
}

function pushVarint(bytes: number[], value: number) {
  let v = value;

  while (v >= 0x80) {
    bytes.push((v & 0x7f) | 0x80);
    v = Math.floor(v / 128);
  }
  bytes.push(v);
}

export function encodeLabels(labels: ArrayLike<number>): string {
  const bytes: number[] = [];
  let i = 0;

  while (i < labels.length) {
    const code = labels[i];
    let j = i + 1;

    while (j < labels.length && labels[j] === code) j++;
    pushVarint(bytes, code);
    pushVarint(bytes, j - i);
    i = j;
  }
  return toBase64(Uint8Array.from(bytes));
}

/** Decode `rle` into `count` codes; throws when the data does not describe exactly `count` points. */
export function decodeLabels(rle: string, count: number): Uint16Array {
  const bytes = fromBase64(rle ?? "");
  const out = new Uint16Array(count);
  let pos = 0;
  let filled = 0;

  const read = () => {
    let result = 0;
    let scale = 1;

    for (;;) {
      if (pos >= bytes.length) throw new Error("Truncated point labels");
      const b = bytes[pos++];

      result += (b & 0x7f) * scale;
      if (b < 0x80) return result;
      scale *= 128;
    }
  };

  while (pos < bytes.length) {
    const code = read();
    const run = read();

    if (code > 0xffff || filled + run > count) throw new Error("Point labels don't match the point cloud");
    out.fill(code, filled, filled + run);
    filled += run;
  }
  if (filled !== count) throw new Error("Point labels don't match the point cloud");
  return out;
}

/** Codes used in a frame, sorted. */
export function usedCodes(labels: ArrayLike<number>): number[] {
  const seen = new Set<number>();

  for (let i = 0; i < labels.length; i++) if (labels[i]) seen.add(labels[i]);
  return [...seen].sort((a, b) => a - b);
}

/* ------------------------------------------------------------------------------------------------
 * picking points on screen
 * ---------------------------------------------------------------------------------------------- */

/**
 * Screen positions (CSS pixels, origin top-left) of all points, for the camera's view-projection matrix
 * (`projection * view`, column-major as three.js Matrix4.elements). Points behind the camera get NaN.
 */
export function projectToScreen(
  positions: Float32Array,
  count: number,
  m: ArrayLike<number>,
  width: number,
  height: number,
): Float32Array {
  const out = new Float32Array(count * 2);

  for (let i = 0; i < count; i++) {
    const x = positions[i * 3];
    const y = positions[i * 3 + 1];
    const z = positions[i * 3 + 2];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];

    if (w <= 1e-6) {
      out[i * 2] = Number.NaN;
      out[i * 2 + 1] = Number.NaN;
      continue;
    }
    const nx = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w;
    const ny = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;

    out[i * 2] = ((nx + 1) / 2) * width;
    out[i * 2 + 1] = ((1 - ny) / 2) * height;
  }
  return out;
}

export function indicesInCircle(screen: Float32Array, cx: number, cy: number, radius: number): number[] {
  const out: number[] = [];
  const r2 = radius * radius;

  for (let i = 0; i < screen.length / 2; i++) {
    const dx = screen[i * 2] - cx;
    const dy = screen[i * 2 + 1] - cy;

    if (dx * dx + dy * dy <= r2) out.push(i);
  }
  return out;
}

/** Points whose screen position is inside the polygon (even-odd rule). */
export function indicesInPolygon(screen: Float32Array, polygon: [number, number][]): number[] {
  if (polygon.length < 3) return [];
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (const [x, y] of polygon) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  const out: number[] = [];

  for (let i = 0; i < screen.length / 2; i++) {
    const px = screen[i * 2];
    const py = screen[i * 2 + 1];

    if (!(px >= minX && px <= maxX && py >= minY && py <= maxY)) continue;
    let inside = false;

    for (let a = 0, b = polygon.length - 1; a < polygon.length; b = a++) {
      const [xa, ya] = polygon[a];
      const [xb, yb] = polygon[b];

      if (ya > py !== yb > py && px < ((xb - xa) * (py - ya)) / (yb - ya) + xa) inside = !inside;
    }
    if (inside) out.push(i);
  }
  return out;
}

/* ------------------------------------------------------------------------------------------------
 * helpers that propose labels
 * ---------------------------------------------------------------------------------------------- */

const cellKey = (ix: number, iy: number) => (ix + 32768) * 65536 + (iy + 32768);

/**
 * Ground points: within `tolerance` of the lowest point around them (a `radius`-cell neighbourhood of a
 * `cell`-metre grid), and not more than `maxAboveGround` above the scan's ground estimate (so roofs of large
 * vehicles aren't taken for ground).
 */
export function groundMask(
  positions: Float32Array,
  count: number,
  { groundZ = 0, cell = 1, radius = 2, tolerance = 0.2, maxAboveGround = 1.0 } = {},
): Uint8Array {
  const lowest = new Map<number, number>();
  const cx = new Int32Array(count);
  const cy = new Int32Array(count);

  for (let i = 0; i < count; i++) {
    cx[i] = Math.floor(positions[i * 3] / cell);
    cy[i] = Math.floor(positions[i * 3 + 1] / cell);
    const key = cellKey(cx[i], cy[i]);
    const z = positions[i * 3 + 2];
    const current = lowest.get(key);

    if (current === undefined || z < current) lowest.set(key, z);
  }
  const local = new Map<number, number>();
  const out = new Uint8Array(count);

  for (let i = 0; i < count; i++) {
    const key = cellKey(cx[i], cy[i]);
    let ground = local.get(key);

    if (ground === undefined) {
      ground = Number.POSITIVE_INFINITY;
      for (let dx = -radius; dx <= radius; dx++) {
        for (let dy = -radius; dy <= radius; dy++) {
          const z = lowest.get(cellKey(cx[i] + dx, cy[i] + dy));

          if (z !== undefined && z < ground) ground = z;
        }
      }
      local.set(key, ground);
    }
    const z = positions[i * 3 + 2];

    if (z - ground <= tolerance && ground <= groundZ + maxAboveGround) out[i] = 1;
  }
  return out;
}

/**
 * Labels carried over from another scan: points of `source` are moved into the target scan's coordinates with
 * `transform` (4x4 row-major, e.g. relativeTransform(sourcePose, targetPose)) and binned into `voxel`-metre
 * cells; each target point takes the majority label of its cell, or of a neighbouring cell. Unknown -> 0.
 * Static scenery lines up when ego poses are known; moving objects must be checked by hand.
 */
export function transferLabels(
  source: { positions: Float32Array; count: number; labels: ArrayLike<number> },
  transform: number[],
  target: { positions: Float32Array; count: number },
  voxel = 0.3,
): Uint16Array {
  const m = transform;
  const key = (x: number, y: number, z: number) =>
    ((Math.floor(x / voxel) + 524288) * 1048576 + (Math.floor(y / voxel) + 524288)) * 4096 +
    (Math.floor(z / voxel) + 2048);
  // Boyer-Moore majority vote per cell
  const cells = new Map<number, { code: number; n: number }>();

  for (let i = 0; i < source.count; i++) {
    const code = source.labels[i];

    if (!code) continue;
    const x = source.positions[i * 3];
    const y = source.positions[i * 3 + 1];
    const z = source.positions[i * 3 + 2];
    const k = key(
      m[0] * x + m[1] * y + m[2] * z + m[3],
      m[4] * x + m[5] * y + m[6] * z + m[7],
      m[8] * x + m[9] * y + m[10] * z + m[11],
    );
    const cell = cells.get(k);

    if (!cell) cells.set(k, { code, n: 1 });
    else if (cell.code === code) cell.n++;
    else if (--cell.n === 0) cells.set(k, { code, n: 1 });
  }

  const out = new Uint16Array(target.count);

  for (let i = 0; i < target.count; i++) {
    const x = target.positions[i * 3];
    const y = target.positions[i * 3 + 1];
    const z = target.positions[i * 3 + 2];
    let cell = cells.get(key(x, y, z));

    for (let d = 0; !cell && d < NEIGHBOURS.length; d++) {
      const [dx, dy, dz] = NEIGHBOURS[d];

      cell = cells.get(key(x + dx * voxel, y + dy * voxel, z + dz * voxel));
    }
    if (cell) out[i] = cell.code;
  }
  return out;
}

// face neighbours first, then edges and corners
const NEIGHBOURS: [number, number, number][] = [];

for (let dx = -1; dx <= 1; dx++) {
  for (let dy = -1; dy <= 1; dy++) {
    for (let dz = -1; dz <= 1; dz++) {
      if (dx || dy || dz) NEIGHBOURS.push([dx, dy, dz]);
    }
  }
}
NEIGHBOURS.sort((a, b) => Math.abs(a[0]) + Math.abs(a[1]) + Math.abs(a[2]) - (Math.abs(b[0]) + Math.abs(b[1]) + Math.abs(b[2])));

/* ------------------------------------------------------------------------------------------------
 * applying a stroke
 * ---------------------------------------------------------------------------------------------- */

export interface PaintOptions {
  /** leave points that already have a class alone */
  onlyUnlabeled?: boolean;
  /** only points whose height above `groundZ` is within [min, max] */
  height?: { min: number; max: number } | null;
  groundZ?: number;
  positions?: Float32Array;
}

/** Set `code` (0 erases) on the given points; returns how many points changed. */
export function paint(labels: Uint16Array, indices: Iterable<number>, code: number, options: PaintOptions = {}) {
  const { onlyUnlabeled = false, height = null, groundZ = 0, positions } = options;
  let changed = 0;

  for (const i of indices) {
    if (i < 0 || i >= labels.length) continue;
    if (onlyUnlabeled && code !== 0 && labels[i] !== 0) continue;
    if (height && positions) {
      const h = positions[i * 3 + 2] - groundZ;

      if (h < height.min || h > height.max) continue;
    }
    if (labels[i] !== code) {
      labels[i] = code;
      changed++;
    }
  }
  return changed;
}
