import { isAlive, types } from "mobx-state-tree";

import Registry from "../core/Registry";
import { guidGenerator } from "../core/Helpers";
import { AreaMixin } from "../mixins/AreaMixin";
import NormalizationMixin from "../mixins/Normalization";
import RegionsMixin from "../mixins/Regions";
import { PointCloudModel } from "../tags/object/PointCloud/PointCloud";
import { decodeLabels, encodeLabels, usedCodes } from "../tags/object/PointCloud/segmentation";

/**
 * Semantic segmentation of a <PointCloud> by a <PointLabels> control: one region per point cloud holding a
 * class for every point of every frame. See PointCloud/segmentation.ts for the encoding.
 *
 * Result value:
 * {
 *   "classes": ["Road", "Car"],                                   // code k = classes[k - 1], 0 = unlabeled
 *   "frames": [{ "frame": 0, "count": 120000, "rle": "..." }],    // frame 0 for a single scan
 *   "pointlabels": ["Road", "Car"]                                // classes in use (label statistics)
 * }
 */
function normalizeFrames(raw) {
  const list = Array.isArray(raw)
    ? raw
    : Object.entries(raw ?? {}).map(([frame, entry]) => ({ ...entry, frame: Number(frame) }));
  const frames = {};

  for (const entry of list) {
    if (Number.isInteger(entry?.frame) && entry.frame >= 0 && Number.isInteger(entry.count) && typeof entry.rle === "string") {
      frames[entry.frame] = { count: entry.count, rle: entry.rle };
    }
  }
  return frames;
}

const Model = types
  .model("PointSegmentationRegionModel", {
    id: types.optional(types.identifier, guidGenerator),
    pid: types.optional(types.string, guidGenerator),
    type: "pointsegmentationregion",
    object: types.late(() => types.reference(PointCloudModel)),

    classes: types.frozen([]),
    // {frame: {count, rle}}; frames without any labeled point are left out
    frames: types.frozen({}),
  })
  .preProcessSnapshot((snapshot) => {
    const value = snapshot.value ?? {};
    const classes = snapshot.classes ?? value.classes;

    return {
      ...snapshot,
      classes: Array.isArray(classes) ? classes.map(String) : [],
      frames: normalizeFrames(snapshot.frames ?? value.frames),
    };
  })
  .volatile(() => ({
    hideable: true,
    // frame -> {rle, labels}: decoded labels, valid while the stored rle is the same string
    decoded: new Map(),
    // frame -> {rle, codes}: classes used in a stored frame
    codesCache: new Map(),
  }))
  .views((self) => ({
    get parent() {
      return isAlive(self) ? self.object : undefined;
    },

    get editable() {
      return !self.isReadOnly();
    },

    get labeledFrames() {
      return Object.keys(self.frames)
        .map(Number)
        .sort((a, b) => a - b);
    },

    /**
     * Labels of `frame` for a scan of `count` points: a mutable Uint16Array kept until the stored data changes
     * (undo, another stroke). Null when the stored labels were made for a different number of points.
     */
    labelsFor(frame, count) {
      const stored = self.frames[frame];
      const rle = stored?.rle ?? "";
      const cached = self.decoded.get(frame);

      if (cached && cached.rle === rle && cached.labels.length === count) return cached.labels;
      if (stored && stored.count !== count) return null;
      let labels;

      try {
        labels = stored ? decodeLabels(stored.rle, count) : new Uint16Array(count);
      } catch {
        return null;
      }
      self.decoded.set(frame, { rle, labels });
      return labels;
    },

    codesOf(frame) {
      const stored = self.frames[frame];

      if (!stored) return [];
      const cached = self.codesCache.get(frame);

      if (cached?.rle === stored.rle) return cached.codes;
      let codes = [];

      try {
        codes = usedCodes(decodeLabels(stored.rle, stored.count));
      } catch {
        // unreadable data counts as unlabeled
      }
      self.codesCache.set(frame, { rle: stored.rle, codes });
      return codes;
    },

    /** Classes used in any frame, in `classes` order. */
    get usedClasses() {
      const codes = new Set();

      for (const frame of self.labeledFrames) for (const code of self.codesOf(frame)) codes.add(code);
      return self.classes.filter((_, index) => codes.has(index + 1));
    },

    /** Points per class in `labels`: [{name, code, count}], most points first. */
    classCounts(labels) {
      if (!labels) return [];
      const counts = new Map();

      for (let i = 0; i < labels.length; i++) if (labels[i]) counts.set(labels[i], (counts.get(labels[i]) ?? 0) + 1);
      return [...counts.entries()]
        .map(([code, count]) => ({ name: self.classes[code - 1] ?? `#${code}`, code, count }))
        .sort((a, b) => b.count - a.count);
    },
  }))
  .actions((self) => ({
    /** Code of a class name, added to `classes` when it is new. */
    codeFor(name) {
      const index = self.classes.indexOf(name);

      if (index >= 0) return index + 1;
      self.classes = [...self.classes, name];
      return self.classes.length;
    },

    /** Store the labels of a frame (after a stroke or a fill). */
    commitFrame(frame, labels) {
      const frames = { ...self.frames };
      const codes = usedCodes(labels);

      if (codes.length) {
        const rle = encodeLabels(labels);

        frames[frame] = { count: labels.length, rle };
        self.decoded.set(frame, { rle, labels });
        self.codesCache.set(frame, { rle, codes });
      } else {
        delete frames[frame];
        self.decoded.set(frame, { rle: "", labels });
      }
      self.frames = frames;
      self.syncLabels();
    },

    /** Keep the result's `pointlabels` (used for label statistics) equal to the classes in use. */
    syncLabels() {
      const result = self.results.find((r) => r.type === "pointlabels");

      result?.setValue(self.usedClasses);
    },

    /**
     * Clicking a class while this region is selected picks the class to paint with; it must not relabel the
     * region the way it relabels a box, so the generic labeling is ignored.
     */
    setValue() {},

    serialize() {
      return {
        value: {
          classes: [...self.classes],
          frames: self.labeledFrames.map((frame) => ({ frame, ...self.frames[frame] })),
        },
      };
    },
  }));

const PointSegmentationRegionModel = types.compose(
  "PointSegmentationRegionModel",
  RegionsMixin,
  AreaMixin,
  NormalizationMixin,
  Model,
);

Registry.addRegionType(PointSegmentationRegionModel, "pointcloud");

export { PointSegmentationRegionModel };
