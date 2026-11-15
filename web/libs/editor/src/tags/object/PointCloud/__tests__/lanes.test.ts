import {
  closestSegment,
  groundHeightAt,
  normalizePoints,
  polylineLength,
  snapToMarkings,
  transformPoints,
  type Vec3,
} from "../lanes";

/** Flat road at z = -1.7 (dim asphalt) with a dashed bright marking at y = 1.75 (3 m paint, 3 m gap). */
function road({ marking = true } = {}) {
  const positions: number[] = [];
  const intensity: number[] = [];
  let seed = 11;
  const random = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };

  for (let x = 0; x <= 30; x += 0.1) {
    for (let y = -4; y <= 4; y += 0.1) {
      positions.push(x, y, -1.7 + (random() - 0.5) * 0.02);
      const paint = marking && Math.abs(y - 1.75) < 0.08 && x % 6 < 3;

      intensity.push(paint ? 0.8 : 0.08 + random() * 0.12);
    }
  }
  return { positions: Float32Array.from(positions), intensity: Float32Array.from(intensity), count: intensity.length };
}

describe("lane geometry", () => {
  it("transforms, measures and cleans points", () => {
    const shift = [1, 0, 0, 2, 0, 1, 0, -1, 0, 0, 1, 0.5, 0, 0, 0, 1];

    expect(transformPoints([[1, 1, 1]], shift)).toEqual([[3, 0, 1.5]]);
    expect(polylineLength([[0, 0, 0], [3, 4, 0], [3, 4, 2]])).toBe(7);
    expect(normalizePoints([[1, 2], { x: 3, y: 4, z: 5 }, ["a"], null, [Number.NaN, 1]])).toEqual([
      [1, 2, 0],
      [3, 4, 5],
    ]);
    expect(normalizePoints("nope")).toEqual([]);
  });

  it("finds the closest screen segment", () => {
    const hit = closestSegment(
      [
        [0, 0],
        [100, 0],
        [100, 100],
      ],
      104,
      50,
    );

    expect(hit.index).toBe(1);
    expect(hit.distance).toBeCloseTo(4);
    expect(hit.t).toBeCloseTo(0.5);
    expect(closestSegment([[0, 0]], 1, 1).index).toBe(-1);
  });

  it("reads the road height and ignores objects on it", () => {
    const { positions, count } = road();
    const withCar = Float32Array.from([...positions, 10, 0, -0.5, 10.2, 0, 0, 10, 0.2, -1.0]);

    expect(groundHeightAt(withCar, count + 3, 10, 0, { fallback: -1.7, groundZ: -1.7 })).toBeCloseTo(-1.7, 1);
    expect(groundHeightAt(positions, count, 100, 100, { fallback: -1.5 })).toBe(-1.5);
  });
});

describe("snap to markings", () => {
  const rough: Vec3[] = [4, 8, 12, 16, 20, 24].map((x) => [x, 1.4, -1.7]);

  it("moves a rough line onto the painted marking, also across gaps of the dashes", () => {
    const { points, snapped } = snapToMarkings(rough, road());

    expect(snapped).toBeGreaterThanOrEqual(4);
    for (const [, y] of points) expect(Math.abs(y - 1.75)).toBeLessThan(0.08);
  });

  it("leaves lines without a marking and scans without intensity alone", () => {
    const plain = road({ marking: false });

    expect(snapToMarkings(rough, plain)).toEqual({ points: rough, snapped: 0 });
    expect(snapToMarkings(rough, { ...plain, intensity: null })).toEqual({ points: rough, snapped: 0 });
  });
});
