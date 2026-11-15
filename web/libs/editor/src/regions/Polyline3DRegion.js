import { isAlive, types } from "mobx-state-tree";

import Registry from "../core/Registry";
import { guidGenerator } from "../core/Helpers";
import { AreaMixin } from "../mixins/AreaMixin";
import NormalizationMixin from "../mixins/Normalization";
import RegionsMixin from "../mixins/Regions";
import { PointCloudModel } from "../tags/object/PointCloud/PointCloud";
import {
  groundHeightAt,
  normalizePoints,
  polylineLength,
  snapToMarkings as snapPointsToMarkings,
  transformPoints,
} from "../tags/object/PointCloud/lanes";
import { isPose, relativeTransform } from "../tags/object/PointCloud/sequence";

/**
 * 3D polyline (lane line, road edge, stop line) on a <PointCloud>, see PointCloud/lanes.ts.
 *
 * Result value: { "points": [[x, y, z], ...], "frame": 3, "labels": ["Dashed"] }
 * Vertices are in the LiDAR frame of `frame` (sequences) and shown in other frames through the ego poses.
 */
const Model = types
  .model("Polyline3DRegionModel", {
    id: types.optional(types.identifier, guidGenerator),
    pid: types.optional(types.string, guidGenerator),
    type: "polyline3dregion",
    object: types.late(() => types.reference(PointCloudModel)),

    points: types.frozen([]),
    // reference frame of a sequence; null for a single scan
    frame: types.maybeNull(types.integer),
  })
  .preProcessSnapshot((snapshot) => {
    const value = snapshot.value ?? {};
    const frame = snapshot.frame ?? value.frame;

    return {
      ...snapshot,
      points: normalizePoints(snapshot.points ?? value.points),
      frame: Number.isInteger(frame) && frame >= 0 ? frame : null,
    };
  })
  .volatile(() => ({
    hideable: true,
  }))
  .views((self) => ({
    get parent() {
      return isAlive(self) ? self.object : undefined;
    },

    get editable() {
      return !self.isReadOnly();
    },

    get currentFrame() {
      return self.object?.isSequence ? self.object.frame : 0;
    },

    /** Transform from the stored (reference frame) coordinates into `frame`'s coordinates. */
    toFrame(frame) {
      const poses = self.object?.poses ?? [];
      const ref = self.frame ?? frame;

      return ref === frame || !isPose(poses[ref]) || !isPose(poses[frame]) ? null : relativeTransform(poses[ref], poses[frame]);
    },

    pointsInFrame(frame) {
      const m = self.toFrame(frame);

      return m ? transformPoints(self.points, m) : self.points;
    },

    /** Vertices in the coordinates of the frame on screen. */
    get displayPoints() {
      return self.pointsInFrame(self.currentFrame);
    },

    get length() {
      return polylineLength(self.points);
    },

    get color() {
      return self.getOneColor();
    },
  }))
  .actions((self) => ({
    /** Store vertices given in the coordinates of the frame on screen. */
    setDisplayPoints(points) {
      const frame = self.currentFrame;
      const m = self.toFrame(frame);
      // inverse transform: frame on screen -> reference frame
      const back = m ? relativeTransform(self.object.poses[frame], self.object.poses[self.frame]) : null;

      self.points = back ? transformPoints(points, back) : points.map((p) => [...p]);
    },

    /** Height of the road under (x, y) in the scan on screen. */
    groundAt(x, y) {
      const item = self.object;
      const cloud = item?.cloud;

      if (!cloud) return item?.groundZ ?? 0;
      return groundHeightAt(cloud.positions, cloud.count, x, y, { fallback: item.groundZ, groundZ: item.groundZ });
    },

    moveVertex(index, x, y) {
      const points = self.displayPoints.map((p) => [...p]);

      if (!points[index]) return;
      points[index] = [x, y, self.groundAt(x, y)];
      self.setDisplayPoints(points);
    },

    insertVertex(index, x, y) {
      const points = self.displayPoints.map((p) => [...p]);

      points.splice(index, 0, [x, y, self.groundAt(x, y)]);
      self.setDisplayPoints(points);
    },

    /** Remove a vertex; a polyline keeps at least two. */
    removeVertex(index) {
      if (self.points.length <= 2 || index < 0 || index >= self.points.length) return false;
      self.setDisplayPoints(self.displayPoints.filter((_, i) => i !== index));
      return true;
    },

    setVertexHeight(index, z) {
      const points = self.displayPoints.map((p) => [...p]);

      if (!points[index] || !Number.isFinite(z)) return;
      points[index][2] = z;
      self.setDisplayPoints(points);
    },

    reverse() {
      self.points = [...self.points].reverse();
    },

    snapToGround() {
      self.setDisplayPoints(self.displayPoints.map(([x, y]) => [x, y, self.groundAt(x, y)]));
    },

    /** Move the vertices onto the painted marking; returns how many found one (0 = unchanged). */
    snapToMarkings() {
      const cloud = self.object?.cloud;

      if (!cloud?.intensity) return -1;
      const { points, snapped } = snapPointsToMarkings(self.displayPoints, cloud);

      if (snapped) self.setDisplayPoints(points.map(([x, y, z]) => [x, y, z]));
      return snapped;
    },

    serialize() {
      return {
        value: {
          points: self.points.map((p) => p.map((v) => Math.round(v * 1000) / 1000)),
          ...(self.frame !== null ? { frame: self.frame } : {}),
        },
      };
    },
  }));

const Polyline3DRegionModel = types.compose(
  "Polyline3DRegionModel",
  RegionsMixin,
  AreaMixin,
  NormalizationMixin,
  Model,
);

Registry.addRegionType(Polyline3DRegionModel, "pointcloud");

export { Polyline3DRegionModel };
