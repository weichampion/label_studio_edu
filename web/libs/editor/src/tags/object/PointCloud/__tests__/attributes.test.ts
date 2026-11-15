import {
  type AttributeChange,
  attributeAt,
  attributeDef,
  attributesAt,
  cleanFrameAttributes,
  coerceValue,
  compactChanges,
  segments,
  setFrom,
  setRange,
} from "../attributes";

const values = (changes: AttributeChange[], count: number) =>
  Array.from({ length: count }, (_, f) => attributeAt(changes, f, "?"));

describe("attribute definitions", () => {
  it("reads choices and defaults like the exporter", () => {
    expect(attributeDef({ value: "occluded", choices: "0, 1,2", default: "1" })).toEqual({
      key: "occluded",
      title: "occluded",
      choices: ["0", "1", "2"],
      default: "1",
    });
    expect(attributeDef({ value: "occluded", choices: "0,1", default: "7" })?.default).toBe("0");
    expect(attributeDef({ value: "moving", title: "Moving" })).toEqual({
      key: "moving",
      title: "Moving",
      choices: null,
      default: false,
    });
    expect(attributeDef({ value: "parked", default: "yes" })?.default).toBe(true);
    expect(attributeDef({ value: " " })).toBeNull();
  });

  it("coerces values", () => {
    const choice = attributeDef({ value: "o", choices: "a,b" })!;
    const flag = attributeDef({ value: "m" })!;

    expect(coerceValue(choice, "b")).toBe("b");
    expect(coerceValue(choice, "c")).toBeNull();
    expect(coerceValue(flag, "true")).toBe(true);
    expect(coerceValue(flag, false)).toBe(false);
  });
});

describe("per-frame values", () => {
  it("holds a value until the next change", () => {
    const changes = [
      { frame: 12, value: "2" },
      { frame: 0, value: "0" },
    ];

    expect(attributeAt(changes, 11, "x")).toBe("0");
    expect(attributeAt(changes, 12, "x")).toBe("2");
    expect(attributeAt([], 3, "x")).toBe("x");
  });

  it("compacts repeated values and frames", () => {
    expect(
      compactChanges([
        { frame: 3, value: "a" },
        { frame: 0, value: "a" },
        { frame: 5, value: "b" },
        { frame: 5, value: "c" },
      ]),
    ).toEqual([
      { frame: 0, value: "a" },
      { frame: 5, value: "c" },
    ]);
  });

  it("edits from one frame on, keeping earlier frames at the default", () => {
    const changes = setFrom([], 4, "2", "0");

    expect(changes).toEqual([
      { frame: 0, value: "0" },
      { frame: 4, value: "2" },
    ]);
    // a later edit stops at the next change
    const more = setFrom(setFrom(changes, 8, "3", "0"), 6, "1", "0");

    expect(values(more, 10)).toEqual(["0", "0", "0", "0", "2", "2", "1", "1", "3", "3"]);
  });

  it("batch edits a range and keeps what follows it", () => {
    const base = setFrom([], 5, "b", "a"); // a a a a a b b b b b

    const edited = setRange(base, 3, 6, "c", "a", 10);

    expect(values(edited, 10)).toEqual(["a", "a", "a", "c", "c", "c", "c", "b", "b", "b"]);
    // reversed bounds and a range past the end are clamped
    expect(values(setRange(edited, 9, 8, "a", "a", 10), 10).slice(7)).toEqual(["b", "a", "a"]);
    expect(values(setRange([], 2, 99, "z", "a", 5), 5)).toEqual(["a", "a", "z", "z", "z"]);
    // the list stays minimal: frames 0..6 are "a" again, so only two changes are left
    expect(setRange(edited, 3, 6, "a", "a", 10)).toEqual([
      { frame: 0, value: "a" },
      { frame: 7, value: "b" },
    ]);
  });

  it("returns runs for the timeline", () => {
    expect(segments([{ frame: 3, value: true }], 6, false)).toEqual([
      { start: 0, end: 2, value: false },
      { start: 3, end: 5, value: true },
    ]);
  });

  it("resolves all attributes of a frame and cleans stored data", () => {
    const defs = [attributeDef({ value: "occluded", choices: "0,1,2" })!, attributeDef({ value: "moving" })!];

    expect(attributesAt({ occluded: [{ frame: 2, value: "2" }] }, defs, 3)).toEqual({ occluded: "2", moving: false });
    expect(cleanFrameAttributes({ occluded: [], bad: "x" })).toBeNull();
    expect(cleanFrameAttributes([1, 2])).toBeNull();
  });
});
