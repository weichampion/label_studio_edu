import { detectFormat, lzfDecompress, parseBin, parsePCD } from "../loaders";

const encoder = new TextEncoder();

function concat(...parts: (Uint8Array | ArrayBuffer)[]) {
  const arrays = parts.map((p) => (p instanceof Uint8Array ? p : new Uint8Array(p)));
  const out = new Uint8Array(arrays.reduce((n, a) => n + a.length, 0));
  let offset = 0;

  for (const a of arrays) {
    out.set(a, offset);
    offset += a.length;
  }
  return out.buffer;
}

const header = (data: string, points: number, fields = "x y z intensity", size = "4 4 4 4", type = "F F F F") =>
  `# .PCD v0.7\nVERSION 0.7\nFIELDS ${fields}\nSIZE ${size}\nTYPE ${type}\nCOUNT ${fields
    .split(" ")
    .map(() => 1)
    .join(" ")}\nWIDTH ${points}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS ${points}\nDATA ${data}\n`;

describe("point cloud loaders", () => {
  it("detects the format from the URL or a hint", () => {
    expect(detectFormat("/data/upload/1/000001.bin")).toBe("bin");
    expect(detectFormat("/data/upload/1/000001.pcd?x=1")).toBe("pcd");
    expect(detectFormat("/data/a", "bin")).toBe("bin");
    expect(detectFormat(`/tasks/1/resolve/?fileuri=${btoa("s3://bucket/scan.bin")}`)).toBe("bin");
  });

  it("parses ascii PCD and drops NaN points", () => {
    const text = `${header("ascii", 3)}1 2 3 0.5\n4 5 6 0.25\nnan nan nan 0\n`;
    const cloud = parsePCD(encoder.encode(text).buffer);

    expect(cloud.count).toBe(2);
    expect(Array.from(cloud.positions)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(Array.from(cloud.intensity!)).toEqual([0.5, 0.25]);
    expect(cloud.bounds).toEqual({ min: [1, 2, 3], max: [4, 5, 6] });
  });

  it("parses binary PCD with mixed field types", () => {
    const fields = "x y z intensity ring";
    const row = new ArrayBuffer(4 * 4 + 2);
    const view = new DataView(row);

    view.setFloat32(0, 1.5, true);
    view.setFloat32(4, -2, true);
    view.setFloat32(8, 0.25, true);
    view.setFloat32(12, 42, true);
    view.setUint16(16, 7, true);
    const buffer = concat(encoder.encode(header("binary", 2, fields, "4 4 4 4 2", "F F F F U")), row, row);
    const cloud = parsePCD(buffer);

    expect(cloud.count).toBe(2);
    expect(Array.from(cloud.positions.slice(0, 3))).toEqual([1.5, -2, 0.25]);
    expect(cloud.intensity?.[1]).toBe(42);
  });

  it("parses binary_compressed PCD (column layout, LZF literal runs)", () => {
    const columns = new Float32Array([1, 4, 2, 5, 3, 6]); // x0 x1 | y0 y1 | z0 z1
    const raw = new Uint8Array(columns.buffer);
    // LZF literal run: control byte = length - 1 (max 32 bytes per run)
    const compressed = new Uint8Array([raw.length - 1, ...raw]);
    const sizes = new DataView(new ArrayBuffer(8));

    sizes.setUint32(0, compressed.length, true);
    sizes.setUint32(4, raw.length, true);
    const buffer = concat(
      encoder.encode(header("binary_compressed", 2, "x y z", "4 4 4", "F F F")),
      sizes.buffer,
      compressed,
    );
    const cloud = parsePCD(buffer);

    expect(Array.from(cloud.positions)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(cloud.intensity).toBeNull();
  });

  it("decompresses LZF back references", () => {
    // literal "abc", then back reference of length 3+2? -> ctrl: len=1 (=> 3 bytes), offset 2 => "abcabc"
    const input = new Uint8Array([2, 97, 98, 99, (1 << 5) | 0, 2]);

    expect(new TextDecoder().decode(lzfDecompress(input, 6))).toBe("abcabc");
    expect(() => lzfDecompress(new Uint8Array([5, 1]), 6)).toThrow();
  });

  it("parses KITTI (4) and nuScenes (5) float32 scans", () => {
    const kitti = new Float32Array([1, 2, 3, 0.5, 4, 5, 6, 0.75]);
    const nuscenes = new Float32Array([1, 2, 3, 10, 0, 4, 5, 6, 20, 1]);

    expect(parseBin(kitti.buffer).count).toBe(2);
    expect(Array.from(parseBin(kitti.buffer).intensity!)).toEqual([0.5, 0.75]);
    const n = parseBin(nuscenes.buffer, 5);

    expect(Array.from(n.positions)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(Array.from(n.intensity!)).toEqual([10, 20]);
  });

  it("rejects files without coordinates", () => {
    expect(() => parsePCD(encoder.encode(header("ascii", 1, "a b", "4 4", "F F")).buffer)).toThrow(/x\/y\/z/);
    expect(() => parsePCD(encoder.encode("garbage").buffer)).toThrow(/DATA/);
  });
});
