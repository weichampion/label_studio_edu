import { accumulateFrames, overlayIndices, relativeTransform } from "../sequence";
import { overlayColors } from "../Viewer3D";

jest.mock("three/examples/jsm/controls/OrbitControls.js", () => ({ OrbitControls: class {} }));

const pose = (x: number, yaw: number) => [
  Math.cos(yaw),
  -Math.sin(yaw),
  0,
  x,
  Math.sin(yaw),
  Math.cos(yaw),
  0,
  0,
  0,
  0,
  1,
  0,
  0,
  0,
  0,
  1,
];

// a static world point seen from an ego pose
function scanOf(worldX: number, worldY: number, p: number[]) {
  const dx = worldX - p[3];
  const dy = worldY - p[7];
  const positions = new Float32Array([p[0] * dx + p[4] * dy, p[1] * dx + p[5] * dy, -1]);

  return { positions, count: 1, bounds: { min: [0, 0, 0], max: [0, 0, 0] } } as any;
}

describe("multi-frame overlay", () => {
  it("lines up static points of earlier scans with the current scan", () => {
    const poses = [pose(0, 0), pose(1.5, 0.05), pose(3, 0.1)];
    const current = scanOf(20, 4, poses[2]);
    const merged = accumulateFrames(
      [
        { offset: -2, cloud: scanOf(20, 4, poses[0]), pose: poses[0] },
        { offset: -1, cloud: scanOf(20, 4, poses[1]), pose: poses[1] },
      ],
      poses[2],
    );

    expect(merged.count).toBe(2);
    expect(Array.from(merged.ages)).toEqual([-2, -1]);
    for (let i = 0; i < 2; i++) {
      expect(merged.positions[i * 3]).toBeCloseTo(current.positions[0], 4);
      expect(merged.positions[i * 3 + 1]).toBeCloseTo(current.positions[1], 4);
    }
  });

  it("overlays scans unchanged when poses are unknown", () => {
    const cloud = scanOf(5, 1, pose(0, 0));
    const merged = accumulateFrames([{ offset: 1, cloud, pose: null }], pose(3, 0.2));

    expect(Array.from(merged.positions)).toEqual(Array.from(cloud.positions));
    expect(relativeTransform(null, pose(1, 0))).toEqual([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  });

  it("decimates evenly to stay under the point budget", () => {
    const big = { positions: new Float32Array(3000), count: 1000, bounds: {} } as any;
    const merged = accumulateFrames(
      [
        { offset: -1, cloud: big, pose: null },
        { offset: -2, cloud: big, pose: null },
      ],
      null,
      500,
    );

    expect(merged.count).toBeLessThanOrEqual(500);
    expect(merged.count).toBeGreaterThan(400);
  });

  it("picks neighbouring frames inside the sequence", () => {
    expect(overlayIndices(1, 3, 10)).toEqual([0]);
    expect(overlayIndices(5, 2, 10, true)).toEqual([4, 6, 3, 7]);
    expect(overlayIndices(9, 2, 10, true)).toEqual([8, 7]);
    expect(overlayIndices(4, 0, 10, true)).toEqual([]);
  });

  it("dims older scans more", () => {
    const overlay = {
      positions: new Float32Array([0, 0, 0, 0, 0, 0]),
      ages: new Float32Array([-1, -3]),
      count: 2,
      offsets: [-1, -3],
    };
    const colors = overlayColors(overlay, 0);
    const brightness = (i: number) => colors[i * 3] + colors[i * 3 + 1] + colors[i * 3 + 2];

    expect(brightness(0)).toBeGreaterThan(brightness(1));
  });
});
