import {
  boxCorners,
  boxFromValue,
  countPointsInBox,
  fitBoxToPoints,
  localToWorld,
  normalizeAngle,
  normalizeCalibration,
  parseCameraImages,
  pointIndicesInBox,
  projectBoxEdges,
  projectPoint,
  valueFromBox,
  worldToLocal,
} from "../geometry";

const EXTRINSIC = [0, -1, 0, 0, 0, 0, -1, -0.08, 1, 0, 0, -0.27, 0, 0, 0, 1];
const INTRINSIC = [620, 0, 480, 0, 620, 250, 0, 0, 1];

const box = { x: 10, y: 2, z: -1, length: 4, width: 2, height: 1.5, yaw: Math.PI / 6 };

describe("PointCloud geometry", () => {
  it("normalizes angles into (-PI, PI]", () => {
    expect(normalizeAngle(3 * Math.PI)).toBeCloseTo(Math.PI);
    expect(normalizeAngle(-Math.PI)).toBeCloseTo(Math.PI);
    expect(normalizeAngle(0.25)).toBeCloseTo(0.25);
  });

  it("round-trips local and world coordinates", () => {
    const [x, y, z] = localToWorld(box, 1, -0.5, 0.2);
    const local = worldToLocal(box, x, y, z);

    expect(local[0]).toBeCloseTo(1);
    expect(local[1]).toBeCloseTo(-0.5);
    expect(local[2]).toBeCloseTo(0.2);
  });

  it("puts the front face at +length/2 along the heading", () => {
    const corners = boxCorners({ ...box, yaw: Math.PI / 2 });
    // heading +y: front-left bottom corner is (x - w/2, y + l/2, z - h/2)
    expect(corners[0][0]).toBeCloseTo(9);
    expect(corners[0][1]).toBeCloseTo(4);
    expect(corners[0][2]).toBeCloseTo(-1.75);
  });

  it("serializes to the result value and back", () => {
    const value = valueFromBox({ ...box, yaw: 7 });

    expect(value.rotation.z).toBeCloseTo(normalizeAngle(7));
    expect(boxFromValue(value)).toMatchObject({ x: 10, y: 2, z: -1, length: 4, width: 2, height: 1.5 });
    expect(boxFromValue(null)).toMatchObject({ x: 0, length: 1 });
  });

  it("finds points inside a rotated box", () => {
    const inside = localToWorld(box, 1.9, 0.9, 0.7);
    const outside = localToWorld(box, 2.1, 0, 0);
    const positions = new Float32Array([...inside, ...outside, 100, 100, 100]);

    expect(pointIndicesInBox(box, positions, 3)).toEqual([0]);
    expect(countPointsInBox(box, positions, 3)).toBe(1);
    expect(pointIndicesInBox(box, positions, 3, 0.2)).toEqual([0, 1]);
  });

  it("fits a box to an object standing on the ground", () => {
    const pts: number[] = [];

    // ground plane at z = -1.7 and a 4 x 1.8 x 1.5 m object at (12, -2) rotated by 20 degrees
    for (let i = -30; i <= 30; i++) for (let j = -20; j <= 20; j++) pts.push(12 + i * 0.1, -2 + j * 0.1, -1.7);
    const object = { x: 12, y: -2, z: -0.95, length: 4, width: 1.8, height: 1.5, yaw: (20 * Math.PI) / 180 };

    for (let i = 0; i <= 20; i++) {
      for (let k = 1; k <= 10; k++) {
        pts.push(...localToWorld(object, -2 + i * 0.2, 0.9, -0.75 + k * 0.15));
        pts.push(...localToWorld(object, -2 + i * 0.2, -0.9, -0.75 + k * 0.15));
      }
    }
    for (let j = 0; j <= 9; j++) {
      for (let k = 1; k <= 10; k++) {
        pts.push(...localToWorld(object, 2, -0.9 + j * 0.2, -0.75 + k * 0.15));
        pts.push(...localToWorld(object, -2, -0.9 + j * 0.2, -0.75 + k * 0.15));
      }
    }
    const positions = new Float32Array(pts);
    const seed = { x: 12.3, y: -1.8, z: 0, length: 5, width: 3, height: 1, yaw: 0 };
    const fitted = fitBoxToPoints(seed, positions, positions.length / 3, { fitYaw: true });

    expect(fitted.z - fitted.height / 2).toBeCloseTo(-1.7, 1);
    expect(fitted.height).toBeCloseTo(1.5, 1);
    expect(fitted.yaw).toBeCloseTo(object.yaw, 1);
    expect(fitted.length).toBeCloseTo(4, 0);
    expect(fitted.width).toBeCloseTo(1.8, 0);
    expect(fitted.x).toBeCloseTo(12, 0);
  });

  it("keeps a footprint and uses the default height when there are no points", () => {
    const fitted = fitBoxToPoints({ ...box, height: 1 }, new Float32Array(0), 0, { groundZ: -1.7 });

    expect(fitted.z - fitted.height / 2).toBeCloseTo(-1.7);
    expect(fitted.length).toBe(box.length);
  });

  it("accepts generic and KITTI calibration", () => {
    expect(normalizeCalibration({ intrinsic: INTRINSIC, extrinsic: EXTRINSIC })).not.toBeNull();
    const kitti = normalizeCalibration({
      P2: "620 0 480 0 0 620 250 0 0 0 1 0",
      R0_rect: [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
      ],
      Tr_velo_to_cam: EXTRINSIC.slice(0, 12),
    });

    expect(kitti?.intrinsic).toHaveLength(12);
    expect(kitti?.extrinsic.map((v) => Math.round(v * 1e6) / 1e6)).toEqual(EXTRINSIC);
    expect(normalizeCalibration({ intrinsic: [1] })).toBeNull();
  });

  it("projects points and clips box edges behind the camera", () => {
    const calib = normalizeCalibration({ intrinsic: INTRINSIC, extrinsic: EXTRINSIC })!;
    // 10 m straight ahead of the camera, which sits 0.27 m ahead of and 0.08 m below the LiDAR
    const px = projectPoint(calib, 10.27, 0, -0.08);

    expect(px?.[0]).toBeCloseTo(480);
    expect(px?.[1]).toBeCloseTo(250);
    expect(projectPoint(calib, -5, 0, 0)).toBeNull();
    expect(projectBoxEdges(calib, { ...box, x: 12, y: 0 })).toHaveLength(12);
    // a box straddling the camera keeps only the clipped edges in front of it
    const straddling = projectBoxEdges(calib, { ...box, x: 0, y: 0, yaw: 0, length: 4 });

    expect(straddling.length).toBeGreaterThan(0);
    expect(straddling.length).toBeLessThan(12);
    expect(projectBoxEdges(calib, { ...box, x: -20 })).toHaveLength(0);
  });

  it("parses camera lists in several shapes", () => {
    expect(parseCameraImages([{ url: "a.png", intrinsic: INTRINSIC, extrinsic: EXTRINSIC }])[0].calibration).not.toBeNull();
    expect(parseCameraImages({ front: { url: "b.png" } })[0]).toMatchObject({ name: "front", calibration: null });
    expect(parseCameraImages("c.png")[0].url).toBe("c.png");
    expect(parseCameraImages(null)).toEqual([]);
  });
});
