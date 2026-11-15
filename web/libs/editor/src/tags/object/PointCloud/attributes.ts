/**
 * Per-frame attributes of cuboids (occlusion, truncation, moving / parked, ...), declared with
 * `<FrameAttribute>` inside `<Cuboid>`.
 *
 * A region stores, per attribute, the list of frames where the value changes; a value holds until the next
 * change (like keyframes hold a box):
 *   "frameAttributes": { "occluded": [{ "frame": 0, "value": "0" }, { "frame": 12, "value": "2" }] }
 * A single-scan box uses frame 0. Frames before the first change take the attribute's default.
 *
 * The Python exporter (label_studio/pointcloud3d/formats.py, attribute_at) implements the same rule.
 */

export type AttributeValue = string | boolean;

export interface AttributeChange {
  frame: number;
  value: AttributeValue;
}

export type FrameAttributes = Record<string, AttributeChange[]>;

export interface AttributeDef {
  key: string;
  title: string;
  /** allowed values; null for a yes / no attribute */
  choices: string[] | null;
  default: AttributeValue;
}

const TRUE_WORDS = new Set(["true", "1", "yes", "y"]);

/** Definition from the attributes of a `<FrameAttribute>` tag (MST node or plain object). */
export function attributeDef(tag: {
  value?: string | null;
  title?: string | null;
  choices?: string | null;
  default?: string | null;
}): AttributeDef | null {
  const key = String(tag.value ?? "").trim();

  if (!key) return null;
  const choices = String(tag.choices ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const raw = tag.default == null ? null : String(tag.default).trim();

  if (!choices.length) {
    return { key, title: tag.title || key, choices: null, default: raw != null && TRUE_WORDS.has(raw.toLowerCase()) };
  }
  return { key, title: tag.title || key, choices, default: raw != null && choices.includes(raw) ? raw : choices[0] };
}

/** Coerce a value to what the definition allows; null when it can't be. */
export function coerceValue(def: AttributeDef, value: unknown): AttributeValue | null {
  if (def.choices === null) {
    if (typeof value === "boolean") return value;
    return TRUE_WORDS.has(String(value).trim().toLowerCase());
  }
  const text = String(value ?? "").trim();

  return def.choices.includes(text) ? text : null;
}

const isChange = (c: unknown): c is AttributeChange =>
  !!c && Number.isInteger((c as AttributeChange).frame) && (c as AttributeChange).frame >= 0 && "value" in (c as object);

/** Sorted by frame, one change per frame (the later one wins), no change that repeats the value before it. */
export function compactChanges(changes: AttributeChange[] | null | undefined): AttributeChange[] {
  const byFrame = new Map<number, AttributeChange>();

  for (const c of changes ?? []) if (isChange(c)) byFrame.set(c.frame, { frame: c.frame, value: c.value });
  const sorted = [...byFrame.values()].sort((a, b) => a.frame - b.frame);

  // the first change is always kept, so a stored list doesn't depend on the configured default
  return sorted.filter((c, i) => i === 0 || c.value !== sorted[i - 1].value);
}

/** Value at `frame`: the last change at or before it, otherwise `fallback`. */
export function attributeAt<T>(
  changes: AttributeChange[] | null | undefined,
  frame: number,
  fallback: T,
): AttributeValue | T {
  let result: AttributeValue | T = fallback;
  let best = -1;

  for (const c of changes ?? []) {
    if (isChange(c) && c.frame <= frame && c.frame >= best) {
      best = c.frame;
      result = c.value;
    }
  }
  return result;
}

/** Earlier frames keep `fallback` explicitly once something is changed later on. */
function pinStart(changes: AttributeChange[], start: number, fallback: AttributeValue) {
  return start > 0 && !changes.some((c) => c.frame < start) ? [{ frame: 0, value: fallback }, ...changes] : changes;
}

/** Set the value from `frame` until the next change (editing in one frame). */
export function setFrom(
  changes: AttributeChange[] | null | undefined,
  frame: number,
  value: AttributeValue,
  fallback: AttributeValue,
): AttributeChange[] {
  const kept = (changes ?? []).filter((c) => isChange(c) && c.frame !== frame);

  return compactChanges([...pinStart(kept, frame, fallback), { frame, value }]);
}

/**
 * Set the value in frames `start`..`end` (inclusive) and keep what was there after `end`
 * (batch editing a range of a track).
 */
export function setRange(
  changes: AttributeChange[] | null | undefined,
  start: number,
  end: number,
  value: AttributeValue,
  fallback: AttributeValue,
  framesCount: number,
): AttributeChange[] {
  const from = Math.max(0, Math.min(start, end));
  const to = Math.min(Math.max(start, end), Math.max(0, framesCount - 1));
  const valid = (changes ?? []).filter(isChange);
  const next: AttributeChange[] = [];

  if (to + 1 < framesCount) next.push({ frame: to + 1, value: attributeAt(valid, to + 1, fallback) });
  const kept = valid.filter((c) => c.frame < from || c.frame > to + 1);

  return compactChanges([...pinStart(kept, from, fallback), { frame: from, value }, ...next]);
}

/** Runs of equal value over the whole sequence, for the timeline. */
export function segments(
  changes: AttributeChange[] | null | undefined,
  framesCount: number,
  fallback: AttributeValue,
): { start: number; end: number; value: AttributeValue }[] {
  const out: { start: number; end: number; value: AttributeValue }[] = [];
  const points = compactChanges(changes).filter((c) => c.frame < framesCount);

  if (!points.length || points[0].frame > 0) points.unshift({ frame: 0, value: fallback });
  points.forEach((c, i) => {
    const end = i + 1 < points.length ? points[i + 1].frame - 1 : framesCount - 1;

    if (out.length && out[out.length - 1].value === c.value) out[out.length - 1].end = end;
    else out.push({ start: c.frame, end, value: c.value });
  });
  return out;
}

/** Values of all attributes in one frame. */
export function attributesAt(
  stored: FrameAttributes | null | undefined,
  defs: AttributeDef[],
  frame: number,
): Record<string, AttributeValue> {
  const out: Record<string, AttributeValue> = {};

  for (const def of defs) out[def.key] = attributeAt(stored?.[def.key], frame, def.default);
  return out;
}

/** Drop empty lists and invalid entries; null when nothing is left (keeps results small). */
export function cleanFrameAttributes(stored: unknown): FrameAttributes | null {
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return null;
  const out: FrameAttributes = {};

  for (const [key, changes] of Object.entries(stored as Record<string, unknown>)) {
    const list = compactChanges(Array.isArray(changes) ? (changes as AttributeChange[]) : []);

    if (list.length) out[key] = list;
  }
  return Object.keys(out).length ? out : null;
}

export const formatValue = (def: AttributeDef, value: AttributeValue) =>
  def.choices === null ? (value ? "yes" : "no") : String(value);
