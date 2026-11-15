/**
 * Point cloud file parsers.
 *
 * Supported:
 *  - PCD v0.7 (DATA ascii | binary | binary_compressed), the format used by PCL, SUSTechPOINTS, Xtreme1, CVAT.
 *    The binary_compressed branch follows the layout used by three.js' PCDLoader (MIT): LZF-compressed,
 *    fields stored column by column.
 *  - Raw float32 `.bin` scans: KITTI (x, y, z, intensity => 4 floats/point) and
 *    nuScenes (x, y, z, intensity, ring => 5 floats/point). The stride is configurable.
 *
 * Kept free of three.js / DOM so it can be unit tested in node.
 */
import type { PointCloudData } from "./geometry";

export type PointCloudFormat = "pcd" | "bin";

export function detectFormat(urlOrName: string, hint?: string | null): PointCloudFormat {
  const h = (hint ?? "").toLowerCase();

  if (h === "pcd" || h === "bin") return h;
  const clean = (urlOrName ?? "").split(/[?#]/)[0].toLowerCase();

  // storage proxies keep the original file name in a `fileuri` query argument
  const fileuri = /[?&]fileuri=([^&#]+)/i.exec(urlOrName ?? "");
  let name = clean;

  if (fileuri) {
    try {
      name = atobSafe(decodeURIComponent(fileuri[1])).toLowerCase() || clean;
    } catch {
      name = clean;
    }
  }
  if (name.endsWith(".bin") || clean.endsWith(".bin")) return "bin";
  return "pcd";
}

function atobSafe(s: string) {
  try {
    return atob(s);
  } catch {
    return s;
  }
}

export function parsePointCloud(
  buffer: ArrayBuffer,
  format: PointCloudFormat,
  options: { binDims?: number } = {},
): PointCloudData {
  if (format === "bin") return parseBin(buffer, options.binDims ?? 4);
  return parsePCD(buffer);
}

function finalize(
  positions: Float32Array,
  count: number,
  intensity: Float32Array | null,
  colors: Float32Array | null,
): PointCloudData {
  // drop NaN / infinite points (common in organized clouds)
  let valid = 0;
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];

  for (let i = 0; i < count; i++) {
    const x = positions[i * 3];
    const y = positions[i * 3 + 1];
    const z = positions[i * 3 + 2];

    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
    if (valid !== i) {
      positions[valid * 3] = x;
      positions[valid * 3 + 1] = y;
      positions[valid * 3 + 2] = z;
      if (intensity) intensity[valid] = intensity[i];
      if (colors) {
        colors[valid * 3] = colors[i * 3];
        colors[valid * 3 + 1] = colors[i * 3 + 1];
        colors[valid * 3 + 2] = colors[i * 3 + 2];
      }
    }
    if (x < min[0]) min[0] = x;
    if (y < min[1]) min[1] = y;
    if (z < min[2]) min[2] = z;
    if (x > max[0]) max[0] = x;
    if (y > max[1]) max[1] = y;
    if (z > max[2]) max[2] = z;
    valid++;
  }
  if (!valid) {
    min.fill(0);
    max.fill(0);
  }

  return {
    positions: positions.length === valid * 3 ? positions : positions.slice(0, valid * 3),
    intensity: intensity ? (intensity.length === valid ? intensity : intensity.slice(0, valid)) : null,
    colors: colors ? (colors.length === valid * 3 ? colors : colors.slice(0, valid * 3)) : null,
    count: valid,
    bounds: { min, max },
  };
}

export function parseBin(buffer: ArrayBuffer, dims = 4): PointCloudData {
  const stride = Math.max(3, Math.floor(dims) || 4);
  const floats = new Float32Array(buffer, 0, Math.floor(buffer.byteLength / 4));
  const count = Math.floor(floats.length / stride);
  const positions = new Float32Array(count * 3);
  const intensity = stride >= 4 ? new Float32Array(count) : null;

  for (let i = 0; i < count; i++) {
    positions[i * 3] = floats[i * stride];
    positions[i * 3 + 1] = floats[i * stride + 1];
    positions[i * 3 + 2] = floats[i * stride + 2];
    if (intensity) intensity[i] = floats[i * stride + 3];
  }
  return finalize(positions, count, intensity, null);
}

interface PCDHeader {
  fields: string[];
  size: number[];
  type: string[];
  count: number[];
  width: number;
  height: number;
  points: number;
  data: "ascii" | "binary" | "binary_compressed";
  headerLength: number;
}

function parseHeader(bytes: Uint8Array): PCDHeader {
  // header is ascii and ends with the DATA line
  let text = "";
  let end = -1;
  const limit = Math.min(bytes.length, 64 * 1024);

  for (let i = 0; i < limit; i++) text += String.fromCharCode(bytes[i]);
  const match = /^DATA\s+(\S+)[^\n]*(\r?\n|$)/m.exec(text);

  if (!match) throw new Error("Invalid PCD file: DATA line not found");
  end = match.index + match[0].length;
  const headerText = text.slice(0, end);
  const lines = headerText.split(/\r?\n/).map((line) => line.replace(/#.*/, "").trim());
  const get = (key: string) => {
    const line = lines.find((l) => l.toUpperCase().startsWith(`${key} `) || l.toUpperCase() === key);

    return line ? line.split(/\s+/).slice(1) : [];
  };
  const fields = get("FIELDS");
  const size = get("SIZE").map(Number);
  const type = get("TYPE").map((t) => t.toUpperCase());
  let count = get("COUNT").map(Number);

  if (!count.length) count = fields.map(() => 1);
  const width = Number(get("WIDTH")[0] ?? 0);
  const height = Number(get("HEIGHT")[0] ?? 1);
  const points = Number(get("POINTS")[0] ?? width * height);
  const data = match[1].toLowerCase() as PCDHeader["data"];

  if (!fields.length || fields.length !== size.length || fields.length !== type.length) {
    throw new Error("Invalid PCD header: FIELDS/SIZE/TYPE mismatch");
  }
  if (!["ascii", "binary", "binary_compressed"].includes(data)) {
    throw new Error(`Unsupported PCD DATA type: ${data}`);
  }
  return { fields, size, type, count, width, height, points, data, headerLength: end };
}

function readValue(view: DataView, offset: number, type: string, size: number): number {
  if (type === "F") return size === 8 ? view.getFloat64(offset, true) : view.getFloat32(offset, true);
  if (type === "U") {
    if (size === 1) return view.getUint8(offset);
    if (size === 2) return view.getUint16(offset, true);
    return view.getUint32(offset, true);
  }
  if (size === 1) return view.getInt8(offset);
  if (size === 2) return view.getInt16(offset, true);
  return view.getInt32(offset, true);
}

/** LZF decompression (liblzf format, as written by PCL). */
export function lzfDecompress(input: Uint8Array, outLength: number): Uint8Array {
  const output = new Uint8Array(outLength);
  let i = 0;
  let o = 0;

  while (i < input.length) {
    let ctrl = input[i++];

    if (ctrl < 32) {
      ctrl++;
      if (o + ctrl > outLength) throw new Error("LZF: output overrun");
      if (i + ctrl > input.length) throw new Error("LZF: input overrun");
      while (ctrl--) output[o++] = input[i++];
    } else {
      let len = ctrl >> 5;
      let ref = o - ((ctrl & 0x1f) << 8) - 1;

      if (i >= input.length) throw new Error("LZF: input overrun");
      if (len === 7) {
        len += input[i++];
        if (i >= input.length) throw new Error("LZF: input overrun");
      }
      ref -= input[i++];
      if (o + len + 2 > outLength) throw new Error("LZF: output overrun");
      if (ref < 0) throw new Error("LZF: invalid back reference");
      len += 2;
      while (len--) output[o++] = output[ref++];
    }
  }
  return output;
}

const unpackRgb = (packed: number): [number, number, number] => [
  ((packed >> 16) & 0xff) / 255,
  ((packed >> 8) & 0xff) / 255,
  (packed & 0xff) / 255,
];

export function parsePCD(buffer: ArrayBuffer): PointCloudData {
  const bytes = new Uint8Array(buffer);
  const header = parseHeader(bytes);
  const { fields, size, type, count: fieldCount, data } = header;
  const n = header.points || header.width * header.height;
  const index = (name: string) => fields.findIndex((f) => f.toLowerCase() === name);
  const ix = index("x");
  const iy = index("y");
  const iz = index("z");
  let ii = index("intensity");

  if (ii < 0) ii = index("i");
  if (ii < 0) ii = index("reflectance");
  let ic = index("rgb");

  if (ic < 0) ic = index("rgba");
  if (ix < 0 || iy < 0 || iz < 0) throw new Error("PCD file has no x/y/z fields");

  const positions = new Float32Array(n * 3);
  const intensity = ii >= 0 ? new Float32Array(n) : null;
  const colors = ic >= 0 ? new Float32Array(n * 3) : null;

  if (data === "ascii") {
    const text = new TextDecoder().decode(bytes.subarray(header.headerLength));
    const rows = text.split(/\r?\n/);
    // column offset of every field (fields may have COUNT > 1)
    const columns: number[] = [];
    let col = 0;

    for (let f = 0; f < fields.length; f++) {
      columns.push(col);
      col += fieldCount[f] || 1;
    }
    let p = 0;

    for (const row of rows) {
      if (p >= n) break;
      const line = row.trim();

      if (!line) continue;
      const v = line.split(/\s+/);

      positions[p * 3] = Number.parseFloat(v[columns[ix]]);
      positions[p * 3 + 1] = Number.parseFloat(v[columns[iy]]);
      positions[p * 3 + 2] = Number.parseFloat(v[columns[iz]]);
      if (intensity) intensity[p] = Number.parseFloat(v[columns[ii]]);
      if (colors) {
        // rgb is a float holding packed bytes; reinterpret
        const f = new Float32Array([Number.parseFloat(v[columns[ic]])]);
        const [r, g, b] = unpackRgb(new Uint32Array(f.buffer)[0]);

        colors[p * 3] = r;
        colors[p * 3 + 1] = g;
        colors[p * 3 + 2] = b;
      }
      p++;
    }
    return finalize(positions, p, intensity, colors);
  }

  if (data === "binary") {
    const offsets: number[] = [];
    let rowSize = 0;

    for (let f = 0; f < fields.length; f++) {
      offsets.push(rowSize);
      rowSize += size[f] * (fieldCount[f] || 1);
    }
    const view = new DataView(buffer, header.headerLength);
    const available = Math.min(n, Math.floor(view.byteLength / rowSize));

    for (let p = 0; p < available; p++) {
      const base = p * rowSize;

      positions[p * 3] = readValue(view, base + offsets[ix], type[ix], size[ix]);
      positions[p * 3 + 1] = readValue(view, base + offsets[iy], type[iy], size[iy]);
      positions[p * 3 + 2] = readValue(view, base + offsets[iz], type[iz], size[iz]);
      if (intensity) intensity[p] = readValue(view, base + offsets[ii], type[ii], size[ii]);
      if (colors) {
        const [r, g, b] = unpackRgb(view.getUint32(base + offsets[ic], true));

        colors[p * 3] = r;
        colors[p * 3 + 1] = g;
        colors[p * 3 + 2] = b;
      }
    }
    return finalize(positions, available, intensity, colors);
  }

  // binary_compressed: [compressedSize u32][uncompressedSize u32][lzf data]; columns stored one after another
  const sizes = new DataView(buffer, header.headerLength, 8);
  const compressedSize = sizes.getUint32(0, true);
  const decompressedSize = sizes.getUint32(4, true);
  const compressed = new Uint8Array(buffer, header.headerLength + 8, compressedSize);
  const decompressed = lzfDecompress(compressed, decompressedSize);
  const view = new DataView(decompressed.buffer);
  const columnOffsets: number[] = [];
  let offset = 0;

  for (let f = 0; f < fields.length; f++) {
    columnOffsets.push(offset);
    offset += size[f] * (fieldCount[f] || 1) * n;
  }
  const stride = (f: number) => size[f] * (fieldCount[f] || 1);

  for (let p = 0; p < n; p++) {
    positions[p * 3] = readValue(view, columnOffsets[ix] + p * stride(ix), type[ix], size[ix]);
    positions[p * 3 + 1] = readValue(view, columnOffsets[iy] + p * stride(iy), type[iy], size[iy]);
    positions[p * 3 + 2] = readValue(view, columnOffsets[iz] + p * stride(iz), type[iz], size[iz]);
    if (intensity) intensity[p] = readValue(view, columnOffsets[ii] + p * stride(ii), type[ii], size[ii]);
    if (colors) {
      const [r, g, b] = unpackRgb(view.getUint32(columnOffsets[ic] + p * stride(ic), true));

      colors[p * 3] = r;
      colors[p * 3 + 1] = g;
      colors[p * 3 + 2] = b;
    }
  }
  return finalize(positions, n, intensity, colors);
}
