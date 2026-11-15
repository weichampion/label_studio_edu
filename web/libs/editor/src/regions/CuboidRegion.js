import { isAlive, types } from "mobx-state-tree";

import Registry from "../core/Registry";
import { guidGenerator } from "../core/Helpers";
import { AreaMixin } from "../mixins/AreaMixin";
import NormalizationMixin from "../mixins/Normalization";
import RegionsMixin from "../mixins/Regions";
import { PointCloudModel } from "../tags/object/PointCloud/PointCloud";
import {
  boxFromValue,
  countPointsInBox,
  fitBoxToPoints,
  normalizeAngle,
  valueFromBox,
} from "../tags/object/PointCloud/geometry";
import {
  boxAt,
  isEnabled,
  isVisibleAt,
  keyframeAt,
  lastKnownBox,
  lifespans,
  removeKeyframe,
  setDimensionsEverywhere,
  sortKeyframes,
  upsertKeyframe,
} from "../tags/object/PointCloud/sequence";
import {
  attributeAt,
  attributesAt,
  cleanFrameAttributes,
  coerceValue,
  setFrom,
  setRange,
} from "../tags/object/PointCloud/attributes";

/**
 * 3D bounding box (cuboid) in a LiDAR point cloud.
 *
 * Single scan — result value (LiDAR frame, metres / radians):
 * {
 *   "position":   { "x": 12.3, "y": -1.5, "z": -0.8 },          // box centre
 *   "dimensions": { "length": 4.2, "width": 1.8, "height": 1.5 }, // length along the heading
 *   "rotation":   { "x": 0, "y": 0, "z": 1.57 },                 // yaw around +z, CCW from +x
 *   "labels": ["Car"]
 * }
 *
 * Sequence — the region is a track (its id is the track id) with keyframes, see PointCloud/sequence.ts:
 * {
 *   "sequence": [{ "frame": 0, "enabled": true, "position": {...}, "dimensions": {...}, "rotation": {...} }, ...],
 *   "framesCount": 80,
 *   "labels": ["Car"]
 * }
 *
 * Both may carry per-frame attributes declared with <FrameAttribute>, see PointCloud/attributes.ts:
 *   "frameAttributes": { "occluded": [{ "frame": 0, "value": "0" }, { "frame": 12, "value": "2" }] }
 */
const Model = types
  .model("CuboidRegionModel", {
    id: types.optional(types.identifier, guidGenerator),
    pid: types.optional(types.string, guidGenerator),
    type: "cuboidregion",
    object: types.late(() => types.reference(PointCloudModel)),

    position: types.frozen({ x: 0, y: 0, z: 0 }),
    dimensions: types.frozen({ length: 1, width: 1, height: 1 }),
    rotation: types.frozen({ x: 0, y: 0, z: 0 }),
    // keyframes when the region is a track in a sequence, null for a single-scan box
    sequence: types.frozen(null),
    // per-frame attribute changes {key: [{frame, value}]}, null when none were set
    frameAttributes: types.frozen(null),
  })
  .preProcessSnapshot((snapshot) => {
    const value = snapshot.value ?? {};
    const sequence = snapshot.sequence ?? value.sequence ?? null;

    return {
      ...snapshot,
      position: snapshot.position ?? value.position,
      dimensions: snapshot.dimensions ?? value.dimensions,
      rotation: snapshot.rotation ?? value.rotation,
      sequence: Array.isArray(sequence) ? sortKeyframes(sequence) : null,
      frameAttributes: cleanFrameAttributes(snapshot.frameAttributes ?? value.frameAttributes),
    };
  })
  .volatile(() => ({
    hideable: true,
    pointCount: null,
  }))
  .views((self) => ({
    get parent() {
      return isAlive(self) ? self.object : undefined;
    },

    get editable() {
      return !self.isReadOnly();
    },

    get isTrack() {
      return Array.isArray(self.sequence);
    },

    get currentFrame() {
      return self.object?.frame ?? 0;
    },

    /** Present in the frame on screen (always true for single-scan boxes). */
    get isVisible() {
      return !self.isTrack || isVisibleAt(self.sequence, self.currentFrame);
    },

    get isKeyframe() {
      return self.isTrack && !!keyframeAt(self.sequence, self.currentFrame);
    },

    get lifespans() {
      return self.isTrack ? lifespans(self.sequence, self.object?.framesCount ?? 1) : [];
    },

    boxAtFrame(frame) {
      if (!self.isTrack) return self.staticBox;
      return boxAt(self.sequence, frame, self.object?.poses ?? []);
    },

    get staticBox() {
      return boxFromValue({ position: self.position, dimensions: self.dimensions, rotation: self.rotation });
    },

    /**
     * Box in the current frame. For a track that is absent here this is its last known box (held in world
     * coordinates), so panels and "resume track" still have something to show.
     */
    get box() {
      if (!self.isTrack) return self.staticBox;
      const poses = self.object?.poses ?? [];

      return (
        boxAt(self.sequence, self.currentFrame, poses) ??
        lastKnownBox(self.sequence, self.currentFrame, poses) ??
        boxFromValue(self.sequence[0])
      );
    },

    get color() {
      return self.getOneColor();
    },

    get attributeDefs() {
      return self.object?.attributeDefs ?? [];
    },

    /** Frame used for attributes: the current one for tracks, 0 for a single scan. */
    get attributeFrame() {
      return self.isTrack ? self.currentFrame : 0;
    },

    attributeValue(key, frame = self.attributeFrame) {
      const def = self.attributeDefs.find((d) => d.key === key);

      return attributeAt(self.frameAttributes?.[key], frame, def ? def.default : null);
    },

    /** Values of all declared attributes in a frame. */
    attributesAtFrame(frame = self.attributeFrame) {
      return attributesAt(self.frameAttributes, self.attributeDefs, frame);
    },
  }))
  .actions((self) => ({
    setBox(box) {
      const normalized = { ...box, yaw: normalizeAngle(box.yaw) };

      if (self.isTrack) {
        self.setKeyframe(self.currentFrame, normalized);
      } else {
        const value = valueFromBox(normalized);

        self.position = value.position;
        self.dimensions = value.dimensions;
        self.rotation = value.rotation;
      }
      self.updatePointCount();
      self.notifyDrawingFinished();
    },

    /** Create or update the keyframe at `frame` (tracks only). */
    setKeyframe(frame, box, enabled = true) {
      if (!self.isTrack) return;
      let sequence = upsertKeyframe(self.sequence, frame, box, enabled);

      if (self.object?.fixedSize) {
        sequence = setDimensionsEverywhere(sequence, valueFromBox(box).dimensions);
      }
      self.sequence = sequence;
      if (frame === self.currentFrame) self.updatePointCount();
    },

    /** Replace all keyframes (result of model-based tracking); invalid input is ignored. */
    applySequence(sequence) {
      if (!self.isTrack || !Array.isArray(sequence) || !sequence.some(isEnabled)) return;
      const frames = self.object?.framesCount ?? Number.POSITIVE_INFINITY;
      const valid = sequence.filter((k) => Number.isInteger(k?.frame) && k.frame >= 0 && k.frame < frames && k.position);

      if (!valid.length) return;
      self.sequence = sortKeyframes(valid);
      self.updatePointCount();
      self.notifyDrawingFinished();
    },

    /** Remove the keyframe on screen; the last keyframe of a track can't be removed (delete the track). */
    removeKeyframeHere() {
      if (!self.isTrack || self.sequence.length <= 1) return;
      const remaining = removeKeyframe(self.sequence, self.currentFrame);

      if (!remaining.some(isEnabled)) return;
      self.sequence = remaining;
      self.updatePointCount();
    },

    /** End the track at the current frame, or bring it back here if it is absent. */
    toggleLifespanHere() {
      if (!self.isTrack) return;
      const frame = self.currentFrame;
      const visible = self.isVisible;
      const box = self.box;

      self.sequence = upsertKeyframe(self.sequence, frame, box, !visible);
      if (!self.sequence.some(isEnabled)) {
        // ending the only segment at its first frame would leave nothing; undo that
        self.sequence = upsertKeyframe(self.sequence, frame, box, true);
      }
      self.updatePointCount();
    },

    /** Set an attribute from `frame` until its next change (editing the box on screen). */
    setAttribute(key, value, frame = self.attributeFrame) {
      const def = self.attributeDefs.find((d) => d.key === key);
      const coerced = def && coerceValue(def, value);

      if (coerced === null || coerced === undefined) return;
      self.frameAttributes = cleanFrameAttributes({
        ...self.frameAttributes,
        [key]: setFrom(self.frameAttributes?.[key], frame, coerced, def.default),
      });
    },

    /** Set an attribute in frames `start`..`end` (inclusive); frames after the range keep their value. */
    setAttributeRange(key, value, start, end) {
      const def = self.attributeDefs.find((d) => d.key === key);
      const coerced = def && coerceValue(def, value);

      if (coerced === null || coerced === undefined) return false;
      const framesCount = self.isTrack ? (self.object?.framesCount ?? 1) : 1;
      const before = JSON.stringify(self.frameAttributes?.[key] ?? null);
      const changes = self.isTrack
        ? setRange(self.frameAttributes?.[key], start, end, coerced, def.default, framesCount)
        : setFrom(self.frameAttributes?.[key], 0, coerced, def.default);

      self.frameAttributes = cleanFrameAttributes({ ...self.frameAttributes, [key]: changes });
      return before !== JSON.stringify(self.frameAttributes?.[key] ?? null);
    },

    updateBox(patch) {
      self.setBox({ ...self.box, ...patch });
    },

    /** Move in the box frame (forward/left/up), metres. */
    translateLocal(forward = 0, left = 0, up = 0) {
      const b = self.box;
      const c = Math.cos(b.yaw);
      const s = Math.sin(b.yaw);

      self.setBox({ ...b, x: b.x + c * forward - s * left, y: b.y + s * forward + c * left, z: b.z + up });
    },

    rotate(delta) {
      self.updateBox({ yaw: self.box.yaw + delta });
    },

    fitToPoints({ fitYaw = false } = {}) {
      const cloud = self.object?.cloud;

      if (!cloud) return;
      self.setBox(
        fitBoxToPoints(self.box, cloud.positions, cloud.count, {
          fitYaw,
          // a track keeps its size; only position, height placement and yaw follow the points
          fitSize: !(self.isTrack && self.object?.fixedSize && self.sequence.length > 1),
          verticalWindow: 1.0,
          defaultHeight: self.object.newBoxHeight,
          groundZ: self.object.groundZ,
        }),
      );
    },

    updatePointCount() {
      try {
        const cloud = self.object?.cloud;

        self.pointCount = cloud && self.isVisible ? countPointsInBox(self.box, cloud.positions, cloud.count) : null;
      } catch {
        // the object reference may not be resolvable yet while the annotation is being deserialized
        self.pointCount = null;
      }
    },

    afterAttach() {
      self.updatePointCount();
    },

    onSelectInOutliner() {
      const object = self.object;

      if (!object) return;
      if (self.isTrack && !self.isVisible) {
        const first = self.lifespans[0];

        if (first) {
          object.setFrame(first.start).then(() => object.requestFocus(self));
          return;
        }
      }
      object.requestFocus(self);
    },

    serialize() {
      const attributes = self.frameAttributes
        ? {
            frameAttributes: Object.fromEntries(
              Object.entries(self.frameAttributes).map(([key, changes]) => [key, changes.map((c) => ({ ...c }))]),
            ),
          }
        : {};

      if (self.isTrack) {
        return {
          value: {
            sequence: self.sequence.map((k) => ({ ...k })),
            framesCount: self.object?.framesCount ?? null,
            ...attributes,
          },
        };
      }
      return {
        value: {
          position: { ...self.position },
          dimensions: { ...self.dimensions },
          rotation: { ...self.rotation },
          ...attributes,
        },
      };
    },
  }));

const CuboidRegionModel = types.compose(
  "CuboidRegionModel",
  RegionsMixin,
  AreaMixin,
  NormalizationMixin,
  Model,
);

Registry.addRegionType(CuboidRegionModel, "pointcloud");

export { CuboidRegionModel };
