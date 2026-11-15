import {
  decodeLabels,
  encodeLabels,
  fromBase64,
  groundMask,
  indicesInCircle,
  indicesInPolygon,
  paint,
  projectToScreen,
  toBase64,
  transferLabels,
  usedCodes,
} from "../segmentation";

// shared with label_studio/pointcloud3d/tests/test_segmentation.py
const VECTOR = [...Array(5).fill(0), ...Array(200).fill(3), 1, 1, ...Array(130).fill(0)];
const VECTOR_RLE = "AAUDyAEBAgCCAQ==";

describe("point label encoding", () => {
  it("matches the Python exporter", () => {
    expect(encodeLabels(VECTOR)).toBe(VECTOR_RLE);
    expect(Array.from(decodeLabels(VECTOR_RLE, VECTOR.length))).toEqual(VECTOR);
  });

  it("round trips", () => {
    let seed = 7;
    const random = () => {
      seed = (seed * 16807) % 2147483647;
      return seed / 2147483647;
    };

    for (let t = 0; t < 50; t++) {
      const labels: number[] = [];

      while (labels.length < 3000) {
        const code = [0, 1, 2, 300, 65535][Math.floor(random() * 5)];

        labels.push(...Array(1 + Math.floor(random() * 300)).fill(code));
      }
      expect(Array.from(decodeLabels(encodeLabels(labels), labels.length))).toEqual(labels);
    }
    expect(decodeLabels(encodeLabels([]), 0).length).toBe(0);
  });

  it("rejects labels made for another point count", () => {
    expect(() => decodeLabels(VECTOR_RLE, VECTOR.length + 1)).toThrow();
    expect(() => decodeLabels(VECTOR_RLE, VECTOR.length - 1)).toThrow();
    expect(() => decodeLabels("gA==", 1)).toThrow();
  });

  it("base64 matches the standard alphabet and padding", () => {
    const cases: [number[], string][] = [
      [[], ""],
      [[0], "AA=="],
      [[255, 1], "/wE="],
      [[1, 2, 3], "AQID"],
      [[250, 251, 252, 253], "+vv8/Q=="],
    ];

    for (const [bytes, text] of cases) {
      expect(toBase64(Uint8Array.from(bytes))).toBe(text);
      expect(Array.from(fromBase64(text))).toEqual(bytes);
    }
  });

  it("lists the used codes", () => {
    expect(usedCodes(VECTOR)).toEqual([1, 3]);
  });
});

describe("picking points on screen", () => {
  // orthographic top view: x -> right, y -> up, 10 m across a 100 px canvas (column-major matrix)
  const m = [0.2, 0, 0, 0, 0, 0.2, 0, 0, 0, 0, -0.01, 0, 0, 0, 0, 1];
  const positions = Float32Array.from([0, 0, 0, 2.5, 0, 0, 0, 2.5, 0, -4, -4, 0]);

  it("projects to CSS pixels", () => {
    const screen = projectToScreen(positions, 4, m, 100, 100);

    expect(Array.from(screen)).toEqual([50, 50, 75, 50, 50, 25, 10, 90]);
  });

  it("finds points in a circle and in a polygon", () => {
    const screen = projectToScreen(positions, 4, m, 100, 100);

    expect(indicesInCircle(screen, 50, 50, 26)).toEqual([0, 1, 2]);
    expect(indicesInCircle(screen, 50, 50, 10)).toEqual([0]);
    expect(
      indicesInPolygon(screen, [
        [40, 40],
        [80, 40],
        [80, 60],
        [40, 60],
      ]),
    ).toEqual([0, 1]);
    expect(indicesInPolygon(screen, [[0, 0]])).toEqual([]);
  });

  it("drops points behind a perspective camera", () => {
    const behind = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0];

    expect(Number.isNaN(projectToScreen(Float32Array.from([0, 0, 1]), 1, behind, 10, 10)[0])).toBe(true);
  });
});

describe("label helpers", () => {
  it("finds the ground but not roofs", () => {
    const points: number[] = [];

    for (let x = -5; x <= 5; x += 0.5) for (let y = -5; y <= 5; y += 0.5) points.push(x, y, -1.7);
    // a car body (roof at -0.2) standing on the ground
    for (let x = 0; x <= 2; x += 0.5) for (let z = -1.4; z <= -0.2; z += 0.4) points.push(x, 0, z);
    const mask = groundMask(Float32Array.from(points), points.length / 3, { groundZ: -1.7 });
    const ground = 21 * 21;

    expect(Array.from(mask.subarray(0, ground)).every(Boolean)).toBe(true);
    expect(Array.from(mask.subarray(ground)).some(Boolean)).toBe(false);
  });

  it("carries labels over with a rigid transform", () => {
    const source = Float32Array.from([0, 0, 0, 5, 0, 0]);
    // the target scan was taken 1 m further forward: the same points are 1 m closer
    const target = Float32Array.from([-1, 0, 0, 4.1, 0.1, 0, 20, 0, 0]);
    const transform = [1, 0, 0, -1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

    const moved = transferLabels({ positions: source, count: 2, labels: [1, 2] }, transform, {
      positions: target,
      count: 3,
    });

    expect(Array.from(moved)).toEqual([1, 2, 0]);
  });

  it("paints with options", () => {
    const labels = Uint16Array.from([0, 1, 0, 2]);
    const positions = Float32Array.from([0, 0, -1.7, 0, 0, 0, 0, 0, 0.5, 0, 0, 0]);

    expect(paint(labels, [0, 1, 2, 3], 5, { onlyUnlabeled: true })).toBe(2);
    expect(Array.from(labels)).toEqual([5, 1, 5, 2]);
    expect(paint(labels, [0, 1, 2, 3], 0, { height: { min: 1, max: 3 }, groundZ: -1.7, positions })).toBe(3);
    expect(Array.from(labels)).toEqual([5, 0, 0, 0]);
    expect(paint(labels, [-1, 99], 3)).toBe(0);
  });
});
