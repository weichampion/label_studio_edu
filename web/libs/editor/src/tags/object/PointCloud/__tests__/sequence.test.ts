import { valueFromBox, type Box3D } from "../geometry";
import {
  boxAt,
  estimateVelocity,
  fromWorld,
  invertRigid,
  isVisibleAt,
  type Keyframe,
  lastKnownBox,
  lifespans,
  normalizePose,
  removeKeyframe,
  setDimensionsEverywhere,
  toWorld,
  trackStep,
  upsertKeyframe,
} from "../sequence";

const car: Box3D = { x: 10, y: 0, z: -1, length: 4, width: 2, height: 1.5, yaw: 0 };
const kf = (frame: number, box: Box3D, enabled = true): Keyframe => ({ frame, enabled, ...valueFromBox(box) });

// ego drives along a left curve, like scripts/generate_pointcloud_samples.py
function egoPose(frame: number) {
  const yaw = 0.05 * frame;
  let x = 0;
  let y = 0;

  for (let i = 0; i < frame; i++) {
    x += Math.cos(0.05 * i);
    y += Math.sin(0.05 * i);
  }
  return [Math.cos(yaw), -Math.sin(yaw), 0, x, Math.sin(yaw), Math.cos(yaw), 0, y, 0, 0, 1, 0, 0, 0, 0, 1];
}

describe("sequence keyframes", () => {
  it("inverts rigid poses", () => {
    const pose = egoPose(3);
    const back = fromWorld(toWorld(car, pose), pose);

    expect(back.x).toBeCloseTo(car.x);
    expect(back.y).toBeCloseTo(car.y);
    expect(back.yaw).toBeCloseTo(car.yaw);
    invertRigid(invertRigid(pose)).forEach((v, i) => expect(v).toBeCloseTo(pose[i], 9));
    expect(normalizePose(pose.slice(0, 12))).toHaveLength(16);
    expect(normalizePose([1, 2, 3])).toBeNull();
  });

  it("interpolates in world coordinates when frames have ego poses", () => {
    const poses = [0, 1, 2, 3, 4].map(egoPose);
    // object moves +2 m/frame along world x with a constant world heading of 0.2 rad
    const world = (f: number): Box3D => ({ ...car, x: 10 + 2 * f, yaw: 0.2 });
    const seq = [kf(0, fromWorld(world(0), poses[0])), kf(4, fromWorld(world(4), poses[4]))];

    for (const f of [1, 2, 3]) {
      const box = boxAt(seq, f, poses)!;
      const expected = fromWorld(world(f), poses[f]);

      expect(box.x).toBeCloseTo(expected.x, 4);
      expect(box.y).toBeCloseTo(expected.y, 4);
      expect(box.yaw).toBeCloseTo(expected.yaw, 4);
    }
  });

  it("holds a parked object still in the world after its last keyframe", () => {
    const poses = [0, 1, 2].map(egoPose);
    const seq = [kf(0, car)];
    const held = boxAt(seq, 2, poses)!;
    const expected = fromWorld(toWorld(car, poses[0]), poses[2]);

    expect(held.x).toBeCloseTo(expected.x);
    expect(held.x).toBeLessThan(car.x); // the ego moved forward, so the car is closer
  });

  it("handles lifespans, ended tracks and missing `enabled`", () => {
    const seq = [kf(2, car), kf(5, { ...car, x: 16 }), kf(8, car, false)];

    expect(isVisibleAt(seq, 1)).toBe(false);
    expect(isVisibleAt(seq, 7)).toBe(true);
    expect(isVisibleAt(seq, 8)).toBe(false);
    expect(boxAt(seq, 3)?.x).toBeCloseTo(12);
    expect(boxAt(seq, 7)?.x).toBeCloseTo(16);
    expect(boxAt(seq, 9)).toBeNull();
    expect(lastKnownBox(seq, 9)?.x).toBeCloseTo(16);
    expect(lifespans(seq, 12)).toEqual([{ start: 2, end: 7 }]);
    expect(lifespans(seq.slice(0, 2), 12)).toEqual([{ start: 2, end: 11 }]);
    const fromMl = [{ ...kf(0, car), enabled: undefined }] as unknown as Keyframe[];

    expect(isVisibleAt(fromMl, 3)).toBe(true);
  });

  it("interpolates yaw the short way round", () => {
    const seq = [kf(0, { ...car, yaw: 3 }), kf(2, { ...car, yaw: -3 })];

    expect(Math.abs(Math.abs(boxAt(seq, 1)!.yaw) - Math.PI)).toBeLessThan(0.3);
  });

  it("edits keyframes", () => {
    let seq = upsertKeyframe([], 3, car);

    seq = upsertKeyframe(seq, 1, { ...car, x: 5 });
    seq = upsertKeyframe(seq, 3, { ...car, x: 7 });
    expect(seq.map((k) => [k.frame, k.position.x])).toEqual([
      [1, 5],
      [3, 7],
    ]);
    expect(removeKeyframe(seq, 1)).toHaveLength(1);
    expect(setDimensionsEverywhere(seq, { length: 5, width: 2, height: 2 }).every((k) => k.dimensions.length === 5)).toBe(
      true,
    );
  });

  it("estimates velocity from the track", () => {
    const seq = [kf(0, car), kf(2, { ...car, x: 14 })];

    expect(estimateVelocity(seq, 2)[0]).toBeCloseTo(2);
    expect(estimateVelocity([kf(0, car)], 0)).toEqual([0, 0, 0]);
  });
});

describe("trackStep", () => {
  // the sensor sees the rear face of the car plus the road
  function scan(rearX: number, y: number) {
    const pts: number[] = [];

    for (let i = -10; i <= 10; i++) for (let j = -10; j <= 10; j++) pts.push(rearX + i * 0.3, y + j * 0.3, -1.75);
    for (let i = 0; i <= 10; i++) for (let k = 0; k <= 8; k++) pts.push(rearX, y - 1 + i * 0.2, -1.6 + k * 0.15);
    const positions = new Float32Array(pts);

    return { positions, count: positions.length / 3, bounds: { min: [0, 0, 0], max: [0, 0, 0] } } as any;
  }

  const box: Box3D = { x: 10, y: 0, z: -1, length: 4, width: 2, height: 1.5, yaw: 0 };

  it("re-centres on the moved object keeping the visible-side offset", () => {
    const result = trackStep({
      box,
      sourceCloud: scan(8, 0),
      targetCloud: scan(9.5, 0.3),
      velocity: [1, 0, 0],
    });

    expect(result.lost).toBe(false);
    expect(result.box.x).toBeCloseTo(11.5, 1);
    expect(result.box.y).toBeCloseTo(0.3, 1);
    expect(result.box.length).toBe(4);
    expect(result.pointCount).toBeGreaterThan(50);
  });

  it("returns the prediction and flags the object as lost without points", () => {
    const empty = { positions: new Float32Array(0), count: 0, bounds: { min: [0, 0, 0], max: [0, 0, 0] } } as any;
    const result = trackStep({ box, sourceCloud: scan(8, 0), targetCloud: empty, velocity: [1, 0, 0] });

    expect(result.lost).toBe(true);
    expect(result.box.x).toBeCloseTo(11);
  });
});
