/**
 * Multi-frame (sequence) annotation: keyframes, interpolation, lifespans and a simple tracker.
 *
 * A track (one cuboid region) stores keyframes, each one in the LiDAR frame of *its own* scan:
 *   { frame, enabled, position, dimensions, rotation }
 * `frame` is the 0-based index into the task's frame list. `enabled: false` marks the frame where the
 * object leaves the scene; it is visible again from the next enabled keyframe.
 *
 * When frames carry an ego pose (4x4 row-major LiDAR -> world), interpolation and "hold" happen in world
 * coordinates, so a parked car stays still while the ego vehicle drives past it and a moving car is
 * interpolated along its real path. Without poses all frames share one coordinate system.
 *
 * The Python exporter (label_studio/pointcloud3d/formats.py) implements the same rules.
 */
import {
  type Box3D,
  type CuboidValue,
  boxFromValue,
  localToWorld,
  normalizeAngle,
  pointIndicesInBox,
  valueFromBox,
  worldToLocal,
  type PointCloudData,
} from "./geometry";

export type Pose = number[] | null | undefined;

export interface Keyframe extends CuboidValue {
  frame: number;
  enabled: boolean;
}

/* ------------------------------------------------------------------------------------------------
 * rigid transforms
 * ---------------------------------------------------------------------------------------------- */

export function isPose(m: unknown): m is number[] {
  return Array.isArray(m) && m.length === 16 && m.every((v) => Number.isFinite(Number(v)));
}

export function normalizePose(raw: unknown): number[] | null {
  if (!raw) return null;
  const values = (Array.isArray(raw) ? (raw as unknown[]).flat(2) : []).map(Number);

  if (values.length === 12) values.push(0, 0, 0, 1);
  return isPose(values) ? values : null;
}

export function invertRigid(m: number[]): number[] {
  // R^T, -R^T t
  const r = [m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]];
  const t = [m[3], m[7], m[11]];
  const rt = [r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8]];

  return [
    rt[0],
    rt[1],
    rt[2],
    -(rt[0] * t[0] + rt[1] * t[1] + rt[2] * t[2]),
    rt[3],
    rt[4],
    rt[5],
    -(rt[3] * t[0] + rt[4] * t[1] + rt[5] * t[2]),
    rt[6],
    rt[7],
    rt[8],
    -(rt[6] * t[0] + rt[7] * t[1] + rt[8] * t[2]),
    0,
    0,
    0,
    1,
  ];
}

/** Apply a rigid transform to a box. Only the yaw part of the rotation is kept (boxes stay upright). */
export function transformBox(box: Box3D, m: number[]): Box3D {
  return {
    ...box,
    x: m[0] * box.x + m[1] * box.y + m[2] * box.z + m[3],
    y: m[4] * box.x + m[5] * box.y + m[6] * box.z + m[7],
    z: m[8] * box.x + m[9] * box.y + m[10] * box.z + m[11],
    yaw: normalizeAngle(box.yaw + Math.atan2(m[4], m[0])),
  };
}

export const toWorld = (box: Box3D, pose: Pose) => (isPose(pose) ? transformBox(box, pose) : box);
export const fromWorld = (box: Box3D, pose: Pose) => (isPose(pose) ? transformBox(box, invertRigid(pose)) : box);

export function lerpBox(a: Box3D, b: Box3D, t: number): Box3D {
  const lerp = (p: number, q: number) => p + (q - p) * t;

  return {
    x: lerp(a.x, b.x),
    y: lerp(a.y, b.y),
    z: lerp(a.z, b.z),
    length: lerp(a.length, b.length),
    width: lerp(a.width, b.width),
    height: lerp(a.height, b.height),
    // shortest way round
    yaw: normalizeAngle(a.yaw + normalizeAngle(b.yaw - a.yaw) * t),
  };
}

/* ------------------------------------------------------------------------------------------------
 * keyframes
 * ---------------------------------------------------------------------------------------------- */

export const sortKeyframes = (seq: Keyframe[]) => [...(seq ?? [])].sort((a, b) => a.frame - b.frame);

/** Keyframes without `enabled` (e.g. from an ML backend) count as enabled. */
export const isEnabled = (k: Keyframe | null | undefined) => !!k && k.enabled !== false;

export function keyframeAt(seq: Keyframe[], frame: number) {
  return (seq ?? []).find((k) => k.frame === frame) ?? null;
}

/** Last keyframe at or before `frame`. */
export function previousKeyframe(seq: Keyframe[], frame: number) {
  let result: Keyframe | null = null;

  for (const k of sortKeyframes(seq)) {
    if (k.frame > frame) break;
    result = k;
  }
  return result;
}

/** First keyframe strictly after `frame`. */
export function nextKeyframe(seq: Keyframe[], frame: number) {
  return sortKeyframes(seq).find((k) => k.frame > frame) ?? null;
}

export function isVisibleAt(seq: Keyframe[], frame: number) {
  return isEnabled(previousKeyframe(seq, frame));
}

/** Box of the track at `frame` in that frame's LiDAR coordinates, or null when the object is absent. */
export function boxAt(seq: Keyframe[], frame: number, poses: Pose[] = []): Box3D | null {
  const prev = previousKeyframe(seq, frame);

  if (!prev || !isEnabled(prev)) return null;
  const prevBox = boxFromValue(prev);

  if (prev.frame === frame) return prevBox;
  const prevWorld = toWorld(prevBox, poses[prev.frame]);
  const next = nextKeyframe(seq, frame);
  let world = prevWorld;

  if (next && isEnabled(next)) {
    const nextWorld = toWorld(boxFromValue(next), poses[next.frame]);

    world = lerpBox(prevWorld, nextWorld, (frame - prev.frame) / (next.frame - prev.frame));
  }
  return fromWorld(world, poses[frame]);
}

/** Last known box of the track at or before `frame`, even if the track ended (used to resume it). */
export function lastKnownBox(seq: Keyframe[], frame: number, poses: Pose[] = []): Box3D | null {
  const sorted = sortKeyframes(seq).filter((k) => k.frame <= frame);
  const source = sorted.reverse().find(isEnabled) ?? sortKeyframes(seq).find(isEnabled);

  if (!source) return null;
  return fromWorld(toWorld(boxFromValue(source), poses[source.frame]), poses[frame]);
}

export function upsertKeyframe(seq: Keyframe[], frame: number, box: Box3D, enabled = true): Keyframe[] {
  const keyframe: Keyframe = { frame, enabled, ...valueFromBox(box) };

  return sortKeyframes([...(seq ?? []).filter((k) => k.frame !== frame), keyframe]);
}

export function removeKeyframe(seq: Keyframe[], frame: number): Keyframe[] {
  return (seq ?? []).filter((k) => k.frame !== frame);
}

/** Objects keep their size through a sequence: apply new dimensions to every keyframe. */
export function setDimensionsEverywhere(seq: Keyframe[], dimensions: CuboidValue["dimensions"]): Keyframe[] {
  return (seq ?? []).map((k) => ({ ...k, dimensions: { ...dimensions } }));
}

/** Frame ranges (inclusive) where the track is present. */
export function lifespans(seq: Keyframe[], framesCount: number): { start: number; end: number }[] {
  const ranges: { start: number; end: number }[] = [];
  let start: number | null = null;

  for (const k of sortKeyframes(seq)) {
    if (isEnabled(k) && start === null) start = k.frame;
    if (!isEnabled(k) && start !== null) {
      ranges.push({ start, end: k.frame - 1 });
      start = null;
    }
  }
  if (start !== null) ranges.push({ start, end: Math.max(start, framesCount - 1) });
  return ranges.filter((r) => r.end >= r.start);
}

/** World-frame displacement per frame, from the track's own box at `frame - 1` (zero when unknown). */
export function estimateVelocity(seq: Keyframe[], frame: number, poses: Pose[] = []): [number, number, number] {
  const current = frame > 0 ? boxAt(seq, frame, poses) : null;
  const before = current ? boxAt(seq, frame - 1, poses) : null;

  if (!current || !before) return [0, 0, 0];
  const here = toWorld(current, poses[frame]);
  const there = toWorld(before, poses[frame - 1]);

  return [here.x - there.x, here.y - there.y, here.z - there.z];
}

/* ------------------------------------------------------------------------------------------------
 * tracker
 * ---------------------------------------------------------------------------------------------- */

export interface TrackStepInput {
  /** box in the source frame's LiDAR coordinates */
  box: Box3D;
  sourceCloud: PointCloudData | null;
  targetCloud: PointCloudData;
  sourcePose?: Pose;
  targetPose?: Pose;
  /** world displacement per frame */
  velocity?: [number, number, number];
  frameDelta?: number;
  groundClearance?: number;
  minPoints?: number;
}

export interface TrackStepResult {
  box: Box3D;
  pointCount: number;
  lost: boolean;
}

function objectPoints(box: Box3D, cloud: PointCloudData, margin: number, clearance: number) {
  const bottom = box.z - box.height / 2 + clearance;
  const enlarged = { ...box, length: box.length + 2 * margin, width: box.width + 2 * margin, height: box.height + 0.6 };

  return pointIndicesInBox(enlarged, cloud.positions, cloud.count).filter(
    (i) => cloud.positions[i * 3 + 2] > bottom,
  );
}

/**
 * Propagate a box to the next frame: constant-velocity prediction (in world coordinates when poses are
 * known), then re-centre on the object's points. The offset between the box centre and the centroid of
 * the visible points is kept, because a LiDAR only sees the near side of an object.
 * Size, height and yaw are kept: objects are rigid and the prediction already accounts for ego motion.
 */
export function trackStep({
  box,
  sourceCloud,
  targetCloud,
  sourcePose,
  targetPose,
  velocity = [0, 0, 0],
  frameDelta = 1,
  groundClearance = 0.2,
  minPoints = 3,
}: TrackStepInput): TrackStepResult {
  // centroid offset in the box frame, measured on the source scan
  let offset: [number, number] = [0, 0];

  if (sourceCloud) {
    const idx = objectPoints(box, sourceCloud, 0, groundClearance);

    if (idx.length >= minPoints) {
      let sx = 0;
      let sy = 0;

      for (const i of idx) {
        sx += sourceCloud.positions[i * 3];
        sy += sourceCloud.positions[i * 3 + 1];
      }
      const [lx, ly] = worldToLocal(box, sx / idx.length, sy / idx.length, box.z);

      offset = [lx, ly];
    }
  }

  const sourceWorld = toWorld(box, sourcePose);
  const predictedWorld = {
    ...sourceWorld,
    x: sourceWorld.x + velocity[0] * frameDelta,
    y: sourceWorld.y + velocity[1] * frameDelta,
    z: sourceWorld.z + velocity[2] * frameDelta,
  };
  let estimate = fromWorld(predictedWorld, targetPose);
  const speed = Math.hypot(velocity[0], velocity[1]) * frameDelta;
  let found = false;

  for (const margin of [1.0 + 0.5 * speed, 0.4]) {
    const idx = objectPoints(estimate, targetCloud, margin, groundClearance);

    if (idx.length < minPoints) break;
    let sx = 0;
    let sy = 0;

    for (const i of idx) {
      sx += targetCloud.positions[i * 3];
      sy += targetCloud.positions[i * 3 + 1];
    }
    const cx = sx / idx.length;
    const cy = sy / idx.length;
    // centroid = centre + R(yaw) * offset  =>  centre = centroid - R(yaw) * offset
    const shifted = localToWorld({ ...estimate, x: 0, y: 0, z: 0 }, offset[0], offset[1], 0);

    estimate = { ...estimate, x: cx - shifted[0], y: cy - shifted[1] };
    found = true;
  }

  const pointCount = pointIndicesInBox(estimate, targetCloud.positions, targetCloud.count).length;

  return { box: estimate, pointCount, lost: !found || pointCount === 0 };
}

/* ------------------------------------------------------------------------------------------------
 * multi-frame accumulation (overlay of neighbouring scans)
 * ---------------------------------------------------------------------------------------------- */

const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

export function multiplyRigid(a: number[], b: number[]): number[] {
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
 * Transform taking points of scan `from` into the LiDAR coordinates of scan `to`:
 * inverse(pose_to) * pose_from. Identity when either pose is unknown.
 */
export function relativeTransform(poseFrom: Pose, poseTo: Pose): number[] {
  if (!isPose(poseFrom) || !isPose(poseTo)) return IDENTITY.slice();
  return multiplyRigid(invertRigid(poseTo), poseFrom);
}

export interface AccumulatedPoints {
  positions: Float32Array;
  /** signed frame offset of every point relative to the target frame (-2 = two frames earlier) */
  ages: Float32Array;
  count: number;
  /** frames that were merged, by offset */
  offsets: number[];
}

/**
 * Merge neighbouring scans into the coordinates of the target frame. Static scenery lines up when poses are
 * known; moving objects leave trails. Each scan is decimated evenly so the total stays under `maxPoints`.
 */
export function accumulateFrames(
  sources: { offset: number; cloud: PointCloudData; pose: Pose }[],
  targetPose: Pose,
  maxPoints = 1_500_000,
): AccumulatedPoints {
  const total = sources.reduce((n, s) => n + s.cloud.count, 0);
  const stride = Math.max(1, Math.ceil(total / Math.max(1, maxPoints)));
  const size = sources.reduce((n, s) => n + Math.ceil(s.cloud.count / stride), 0);
  const positions = new Float32Array(size * 3);
  const ages = new Float32Array(size);
  let out = 0;

  for (const { offset, cloud, pose } of sources) {
    const m = relativeTransform(pose, targetPose);
    const p = cloud.positions;

    for (let i = 0; i < cloud.count; i += stride) {
      const x = p[i * 3];
      const y = p[i * 3 + 1];
      const z = p[i * 3 + 2];

      positions[out * 3] = m[0] * x + m[1] * y + m[2] * z + m[3];
      positions[out * 3 + 1] = m[4] * x + m[5] * y + m[6] * z + m[7];
      positions[out * 3 + 2] = m[8] * x + m[9] * y + m[10] * z + m[11];
      ages[out] = offset;
      out++;
    }
  }
  return { positions, ages, count: out, offsets: sources.map((s) => s.offset) };
}

/** Frame indices to overlay around `frame`: up to `count` earlier frames, and later ones when `future`. */
export function overlayIndices(frame: number, count: number, framesCount: number, future = false): number[] {
  const indices: number[] = [];

  for (let d = 1; d <= count; d++) {
    if (frame - d >= 0) indices.push(frame - d);
    if (future && frame + d < framesCount) indices.push(frame + d);
  }
  return indices;
}
