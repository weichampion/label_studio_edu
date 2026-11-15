/**
 * Pure geometry helpers for 3D cuboid annotation on LiDAR point clouds.
 *
 * Coordinate convention (same as KITTI velodyne / OpenPCDet / nuScenes lidar):
 *   x = forward, y = left, z = up (right-handed), metres.
 *   yaw = rotation around +z, counter-clockwise from +x, radians in (-PI, PI].
 *   length is measured along the heading (local x), width along local y, height along z.
 *
 * Nothing in this file depends on three.js or MobX so it can be unit tested in isolation.
 */

export interface Box3D {
  x: number;
  y: number;
  z: number;
  length: number;
  width: number;
  height: number;
  yaw: number;
}

/** Serialized form stored in annotation results (`value` of a `cuboid` result). */
export interface CuboidValue {
  position: { x: number; y: number; z: number };
  dimensions: { length: number; width: number; height: number };
  rotation: { x: number; y: number; z: number };
}

export interface PointCloudData {
  /** xyz triplets */
  positions: Float32Array;
  /** optional per-point intensity (raw sensor value) */
  intensity?: Float32Array | null;
  /** optional per-point rgb in 0..1 */
  colors?: Float32Array | null;
  count: number;
  bounds: { min: [number, number, number]; max: [number, number, number] };
}

export const MIN_SIZE = 0.05;

const round = (v: number, digits = 4) => {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
};

export function normalizeAngle(angle: number): number {
  let a = angle % (2 * Math.PI);
  if (a <= -Math.PI) a += 2 * Math.PI;
  if (a > Math.PI) a -= 2 * Math.PI;
  return a;
}

export const toDegrees = (rad: number) => (rad * 180) / Math.PI;
export const toRadians = (deg: number) => (deg * Math.PI) / 180;

export function boxFromValue(value: Partial<CuboidValue> | null | undefined): Box3D {
  const p = value?.position ?? { x: 0, y: 0, z: 0 };
  const d = value?.dimensions ?? { length: 1, width: 1, height: 1 };
  const r = value?.rotation ?? { x: 0, y: 0, z: 0 };

  return {
    x: Number(p.x) || 0,
    y: Number(p.y) || 0,
    z: Number(p.z) || 0,
    length: Math.max(MIN_SIZE, Number(d.length) || MIN_SIZE),
    width: Math.max(MIN_SIZE, Number(d.width) || MIN_SIZE),
    height: Math.max(MIN_SIZE, Number(d.height) || MIN_SIZE),
    yaw: normalizeAngle(Number(r.z) || 0),
  };
}

export function valueFromBox(box: Box3D): CuboidValue {
  return {
    position: { x: round(box.x), y: round(box.y), z: round(box.z) },
    dimensions: {
      length: round(Math.max(MIN_SIZE, box.length)),
      width: round(Math.max(MIN_SIZE, box.width)),
      height: round(Math.max(MIN_SIZE, box.height)),
    },
    rotation: { x: 0, y: 0, z: round(normalizeAngle(box.yaw), 6) },
  };
}

/** World (lidar) point -> box-local coordinates. */
export function worldToLocal(box: Box3D, x: number, y: number, z: number): [number, number, number] {
  const dx = x - box.x;
  const dy = y - box.y;
  const c = Math.cos(box.yaw);
  const s = Math.sin(box.yaw);

  return [c * dx + s * dy, -s * dx + c * dy, z - box.z];
}

/** Box-local point -> world (lidar) coordinates. */
export function localToWorld(box: Box3D, lx: number, ly: number, lz: number): [number, number, number] {
  const c = Math.cos(box.yaw);
  const s = Math.sin(box.yaw);

  return [box.x + c * lx - s * ly, box.y + s * lx + c * ly, box.z + lz];
}

/**
 * 8 corners. Indices 0-3 are the bottom face, 4-7 the top face, in the order
 * front-left, front-right, rear-right, rear-left (front = +heading).
 */
export function boxCorners(box: Box3D): [number, number, number][] {
  const l = box.length / 2;
  const w = box.width / 2;
  const h = box.height / 2;
  const local: [number, number, number][] = [
    [l, w, -h],
    [l, -w, -h],
    [-l, -w, -h],
    [-l, w, -h],
    [l, w, h],
    [l, -w, h],
    [-l, -w, h],
    [-l, w, h],
  ];

  return local.map(([a, b, c]) => localToWorld(box, a, b, c));
}

export const BOX_EDGES: [number, number][] = [
  [0, 1],
  [1, 2],
  [2, 3],
  [3, 0],
  [4, 5],
  [5, 6],
  [6, 7],
  [7, 4],
  [0, 4],
  [1, 5],
  [2, 6],
  [3, 7],
];

/** Edges of the front face, drawn thicker / differently to show the heading. */
export const FRONT_EDGES: [number, number][] = [
  [0, 1],
  [4, 5],
  [0, 4],
  [1, 5],
];

export function isPointInBox(box: Box3D, x: number, y: number, z: number, margin = 0): boolean {
  const [lx, ly, lz] = worldToLocal(box, x, y, z);

  return (
    Math.abs(lx) <= box.length / 2 + margin &&
    Math.abs(ly) <= box.width / 2 + margin &&
    Math.abs(lz) <= box.height / 2 + margin
  );
}

/** Indices of points inside the box (optionally enlarged by `margin` metres on every side). */
export function pointIndicesInBox(box: Box3D, positions: Float32Array, count: number, margin = 0): number[] {
  const result: number[] = [];
  const c = Math.cos(box.yaw);
  const s = Math.sin(box.yaw);
  const hl = box.length / 2 + margin;
  const hw = box.width / 2 + margin;
  const hh = box.height / 2 + margin;
  // cheap circumscribed-circle rejection first
  const r2 = hl * hl + hw * hw;

  for (let i = 0; i < count; i++) {
    const dx = positions[i * 3] - box.x;
    const dy = positions[i * 3 + 1] - box.y;

    if (dx * dx + dy * dy > r2) continue;
    const dz = positions[i * 3 + 2] - box.z;

    if (Math.abs(dz) > hh) continue;
    const lx = c * dx + s * dy;
    const ly = -s * dx + c * dy;

    if (Math.abs(lx) <= hl && Math.abs(ly) <= hw) result.push(i);
  }
  return result;
}

export function countPointsInBox(box: Box3D, positions: Float32Array, count: number): number {
  return pointIndicesInBox(box, positions, count).length;
}

function percentile(sorted: number[], p: number) {
  if (!sorted.length) return Number.NaN;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * p)));

  return sorted[idx];
}

/** Rough ground height around the sensor: low percentile of z within `radius` metres. */
export function estimateGroundZ(positions: Float32Array, count: number, radius = 20): number {
  const zs: number[] = [];
  const r2 = radius * radius;
  // sample at most ~20k points for speed
  const step = Math.max(1, Math.floor(count / 20000));

  for (let i = 0; i < count; i += step) {
    const x = positions[i * 3];
    const y = positions[i * 3 + 1];

    if (x * x + y * y <= r2) zs.push(positions[i * 3 + 2]);
  }
  if (!zs.length) return 0;
  zs.sort((a, b) => a - b);
  return percentile(zs, 0.05);
}

export interface FitOptions {
  /** extra search margin around the footprint, metres */
  margin?: number;
  /** points lower than ground + threshold are treated as ground */
  groundThreshold?: number;
  /** also estimate yaw (minimum-area rectangle), keeping the heading closest to the current one */
  fitYaw?: boolean;
  /** shrink/grow length & width to the object points */
  fitSize?: boolean;
  /** height used when no object points are found */
  defaultHeight?: number;
  /** fallback ground height when the footprint has no points at all */
  groundZ?: number;
  /** restrict search vertically to the current box +/- this many metres (null = unrestricted) */
  verticalWindow?: number | null;
}

function minAreaYaw(xy: [number, number][], initialYaw: number): number {
  if (xy.length < 3) return initialYaw;
  let bestAngle = 0;
  let bestArea = Number.POSITIVE_INFINITY;

  // rectangle orientation is periodic with PI/2; 1 degree steps are enough for annotation seeding
  for (let deg = 0; deg < 90; deg += 1) {
    const a = toRadians(deg);
    const c = Math.cos(a);
    const s = Math.sin(a);
    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;

    for (const [x, y] of xy) {
      const lx = c * x + s * y;
      const ly = -s * x + c * y;

      if (lx < minX) minX = lx;
      if (lx > maxX) maxX = lx;
      if (ly < minY) minY = ly;
      if (ly > maxY) maxY = ly;
    }
    const area = (maxX - minX) * (maxY - minY);

    if (area < bestArea - 1e-9) {
      bestArea = area;
      bestAngle = a;
    }
  }
  // choose among bestAngle + k*PI/2 the one closest to the initial heading
  let best = bestAngle;
  let bestDiff = Number.POSITIVE_INFINITY;

  for (let k = -4; k <= 4; k++) {
    const candidate = normalizeAngle(bestAngle + (k * Math.PI) / 2);
    const diff = Math.abs(normalizeAngle(candidate - initialYaw));

    if (diff < bestDiff) {
      bestDiff = diff;
      best = candidate;
    }
  }
  return best;
}

/**
 * Snap a cuboid to the points inside (and slightly around) it — the "auto fit" found in
 * SUSTechPOINTS / Xtreme1. Ground points are removed with a height threshold, the box bottom is placed
 * on the local ground, the top on the highest object point and (optionally) length/width/yaw are fitted
 * to the remaining object points.
 */
export function fitBoxToPoints(box: Box3D, positions: Float32Array, count: number, options: FitOptions = {}): Box3D {
  const {
    margin = 0.3,
    groundThreshold = 0.2,
    fitYaw = false,
    fitSize = true,
    defaultHeight = 1.6,
    groundZ,
    verticalWindow = null,
  } = options;
  const c = Math.cos(box.yaw);
  const s = Math.sin(box.yaw);
  const hl = box.length / 2 + margin;
  const hw = box.width / 2 + margin;
  const zLow = verticalWindow === null ? Number.NEGATIVE_INFINITY : box.z - box.height / 2 - verticalWindow;
  const zHigh = verticalWindow === null ? Number.POSITIVE_INFINITY : box.z + box.height / 2 + verticalWindow;

  const candidates: number[] = [];

  for (let i = 0; i < count; i++) {
    const z = positions[i * 3 + 2];

    if (z < zLow || z > zHigh) continue;
    const dx = positions[i * 3] - box.x;
    const dy = positions[i * 3 + 1] - box.y;
    const lx = c * dx + s * dy;
    const ly = -s * dx + c * dy;

    if (Math.abs(lx) <= hl && Math.abs(ly) <= hw) candidates.push(i);
  }

  if (!candidates.length) {
    const ground = groundZ ?? box.z - box.height / 2;
    const height = box.height > MIN_SIZE ? box.height : defaultHeight;

    return { ...box, height, z: ground + height / 2 };
  }

  const zs = candidates.map((i) => positions[i * 3 + 2]).sort((a, b) => a - b);
  // robust minimum: ignore a couple of noisy low returns
  const ground = percentile(zs, 0.02);
  const objectIdx = candidates.filter((i) => positions[i * 3 + 2] > ground + groundThreshold);

  if (!objectIdx.length) {
    const height = defaultHeight;

    return { ...box, height, z: ground + height / 2 };
  }

  let yaw = box.yaw;

  if (fitYaw) {
    const xy: [number, number][] = objectIdx.map((i) => [positions[i * 3] - box.x, positions[i * 3 + 1] - box.y]);

    yaw = minAreaYaw(xy, box.yaw);
  }

  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  let top = Number.NEGATIVE_INFINITY;

  for (const i of objectIdx) {
    const dx = positions[i * 3] - box.x;
    const dy = positions[i * 3 + 1] - box.y;
    const lx = cy * dx + sy * dy;
    const ly = -sy * dx + cy * dy;

    if (lx < minX) minX = lx;
    if (lx > maxX) maxX = lx;
    if (ly < minY) minY = ly;
    if (ly > maxY) maxY = ly;
    if (positions[i * 3 + 2] > top) top = positions[i * 3 + 2];
  }

  let { x, y, length, width } = box;

  if (fitSize) {
    const midX = (minX + maxX) / 2;
    const midY = (minY + maxY) / 2;

    x = box.x + cy * midX - sy * midY;
    y = box.y + sy * midX + cy * midY;
    length = Math.max(MIN_SIZE * 4, maxX - minX);
    width = Math.max(MIN_SIZE * 4, maxY - minY);
  }

  const height = Math.max(MIN_SIZE * 4, top - ground);

  return { x, y, z: ground + height / 2, length, width, height, yaw };
}

/* ------------------------------------------------------------------------------------------------
 * Camera projection (sensor fusion view)
 * ---------------------------------------------------------------------------------------------- */

export interface CameraCalibration {
  /** 3x3 K or 3x4 P, row-major */
  intrinsic: number[];
  /** 4x4 lidar -> camera transform, row-major */
  extrinsic: number[];
}

export interface CameraImage {
  url: string;
  name: string;
  calibration: CameraCalibration | null;
}

const flat = (m: unknown): number[] | null => {
  if (!m) return null;
  if (Array.isArray(m)) {
    const values = (m as unknown[]).flat(2).map(Number);

    return values.every((v) => Number.isFinite(v)) ? values : null;
  }
  if (typeof m === "string") {
    const values = m
      .trim()
      .split(/[\s,]+/)
      .map(Number);

    return values.length && values.every((v) => Number.isFinite(v)) ? values : null;
  }
  return null;
};

const to4x4 = (m: number[]): number[] | null => {
  if (m.length === 16) return m;
  if (m.length === 12) return [...m, 0, 0, 0, 1];
  // KITTI R0_rect is 3x3
  if (m.length === 9) return [m[0], m[1], m[2], 0, m[3], m[4], m[5], 0, m[6], m[7], m[8], 0, 0, 0, 0, 1];
  return null;
};

export function multiply4x4(a: number[], b: number[]): number[] {
  const out = new Array(16).fill(0);

  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      let v = 0;

      for (let k = 0; k < 4; k++) v += a[r * 4 + k] * b[k * 4 + c];
      out[r * 4 + c] = v;
    }
  }
  return out;
}

/**
 * Accepts either the generic form `{ intrinsic, extrinsic }` or KITTI calib fields
 * `{ P2, R0_rect, Tr_velo_to_cam }` (extrinsic = R0_rect * Tr_velo_to_cam). Returns null when unusable.
 */
export function normalizeCalibration(raw: any): CameraCalibration | null {
  if (!raw || typeof raw !== "object") return null;
  let intrinsic = flat(raw.intrinsic ?? raw.K ?? raw.P ?? raw.P2 ?? raw.camera_intrinsic);
  let extrinsic: number[] | null = null;
  const ext = flat(raw.extrinsic ?? raw.lidar_to_camera ?? raw.Tr_velo_to_cam ?? raw.Tr_velo_cam);

  if (ext) extrinsic = to4x4(ext);
  const r0 = flat(raw.R0_rect ?? raw.R_rect);

  if (extrinsic && r0) {
    const r0m = to4x4(r0);

    if (r0m) extrinsic = multiply4x4(r0m, extrinsic);
  }
  if (!intrinsic || !(intrinsic.length === 9 || intrinsic.length === 12)) return null;
  if (!extrinsic) return null;
  intrinsic = intrinsic.slice();
  return { intrinsic, extrinsic };
}

export function lidarToCamera(calib: CameraCalibration, x: number, y: number, z: number): [number, number, number] {
  const e = calib.extrinsic;

  return [
    e[0] * x + e[1] * y + e[2] * z + e[3],
    e[4] * x + e[5] * y + e[6] * z + e[7],
    e[8] * x + e[9] * y + e[10] * z + e[11],
  ];
}

/** Camera-frame point -> pixel. Returns null behind the camera. */
export function cameraToPixel(calib: CameraCalibration, p: [number, number, number]): [number, number] | null {
  const k = calib.intrinsic;
  let u: number;
  let v: number;
  let w: number;

  if (k.length === 12) {
    u = k[0] * p[0] + k[1] * p[1] + k[2] * p[2] + k[3];
    v = k[4] * p[0] + k[5] * p[1] + k[6] * p[2] + k[7];
    w = k[8] * p[0] + k[9] * p[1] + k[10] * p[2] + k[11];
  } else {
    u = k[0] * p[0] + k[1] * p[1] + k[2] * p[2];
    v = k[3] * p[0] + k[4] * p[1] + k[5] * p[2];
    w = k[6] * p[0] + k[7] * p[1] + k[8] * p[2];
  }
  if (w <= 1e-6) return null;
  return [u / w, v / w];
}

export function projectPoint(calib: CameraCalibration, x: number, y: number, z: number): [number, number] | null {
  const p = lidarToCamera(calib, x, y, z);

  if (p[2] < NEAR_PLANE) return null;
  return cameraToPixel(calib, p);
}

const NEAR_PLANE = 0.1;

/** Project the 12 box edges, clipping each one against the camera near plane. */
export function projectBoxEdges(
  calib: CameraCalibration,
  box: Box3D,
): { from: [number, number]; to: [number, number]; front: boolean }[] {
  const corners = boxCorners(box).map(([x, y, z]) => lidarToCamera(calib, x, y, z));
  const frontSet = new Set(FRONT_EDGES.map(([a, b]) => `${a}-${b}`));
  const segments: { from: [number, number]; to: [number, number]; front: boolean }[] = [];

  for (const [a, b] of BOX_EDGES) {
    let p = corners[a];
    let q = corners[b];

    if (p[2] < NEAR_PLANE && q[2] < NEAR_PLANE) continue;
    if (p[2] < NEAR_PLANE || q[2] < NEAR_PLANE) {
      const t = (NEAR_PLANE - p[2]) / (q[2] - p[2]);
      const clipped: [number, number, number] = [
        p[0] + (q[0] - p[0]) * t,
        p[1] + (q[1] - p[1]) * t,
        NEAR_PLANE,
      ];

      if (p[2] < NEAR_PLANE) p = clipped;
      else q = clipped;
    }
    const from = cameraToPixel(calib, p);
    const to = cameraToPixel(calib, q);

    if (from && to) segments.push({ from, to, front: frontSet.has(`${a}-${b}`) });
  }
  return segments;
}

/** Parse the task's camera list. Accepts an array, an object keyed by camera name, or a single camera. */
export function parseCameraImages(raw: any): CameraImage[] {
  if (!raw) return [];
  let list: any[];

  if (typeof raw === "string") {
    try {
      return parseCameraImages(JSON.parse(raw));
    } catch {
      return [{ url: raw, name: "camera", calibration: null }];
    }
  }
  if (Array.isArray(raw)) list = raw;
  else if (raw.url || raw.image) list = [raw];
  else list = Object.entries(raw).map(([name, cam]: [string, any]) => ({ name, ...(cam || {}) }));

  return list
    .map((cam: any, index: number) => {
      if (typeof cam === "string") return { url: cam, name: `camera ${index + 1}`, calibration: null };
      const url = cam?.url ?? cam?.image ?? cam?.src;

      if (!url) return null;
      return {
        url: String(url),
        name: String(cam.name ?? cam.camera ?? `camera ${index + 1}`),
        calibration: normalizeCalibration(cam.calibration ?? cam.calib ?? cam),
      };
    })
    .filter(Boolean) as CameraImage[];
}
