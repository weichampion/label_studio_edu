import { flow, getRoot, isAlive, types } from "mobx-state-tree";
import { ff } from "@humansignal/core";

import { AnnotationMixin } from "../../../mixins/AnnotationMixin";
import { parseValue } from "../../../utils/data";
import ObjectBase from "../Base";
import { estimateGroundZ, fitBoxToPoints, parseCameraImages, pointIndicesInBox, valueFromBox } from "./geometry";
import { detectFormat, parsePointCloud } from "./loaders";
import { groundHeightAt } from "./lanes";
import { groundMask, paint, transferLabels } from "./segmentation";
import {
  accumulateFrames,
  estimateVelocity,
  isPose,
  normalizePose,
  overlayIndices,
  relativeTransform,
  trackStep,
} from "./sequence";

/**
 * The `PointCloud` tag displays a LiDAR point cloud in 3D for 3D object detection, e.g. outdoor
 * autonomous-driving scenes. Use it together with `<Cuboid>` and `<Labels>` to draw 3D bounding boxes.
 *
 * Supported files: PCD (ascii / binary / binary_compressed) and raw float32 `.bin` scans
 * (KITTI: 4 floats per point, nuScenes: 5 floats per point — set `binDims="5"`).
 *
 * Coordinates are in the sensor (LiDAR) frame: x forward, y left, z up, metres.
 * The interaction model follows open source 3D tools (SUSTechPOINTS, Xtreme1, CVAT 3D):
 * draw a box in the bird's-eye view, auto-fit it to the points, then refine it in the
 * top / front / side projections or with the keyboard.
 *
 * Optional camera images are shown with the 3D boxes projected onto them (sensor fusion).
 *
 * @example
 * <!-- 3D object detection on LiDAR point clouds -->
 * <View>
 *   <Labels name="label" toName="pcd">
 *     <Label value="Car" background="#FFA39E"/>
 *     <Label value="Pedestrian" background="#D4380D"/>
 *     <Label value="Cyclist" background="#FFC069"/>
 *   </Labels>
 *   <PointCloud name="pcd" value="$pointcloud"/>
 *   <Cuboid name="box" toName="pcd"/>
 * </View>
 * @example
 * <!-- LiDAR + camera fusion; `images` is a list of {url, name, intrinsic, extrinsic} -->
 * <View>
 *   <Labels name="label" toName="pcd">
 *     <Label value="Car"/>
 *   </Labels>
 *   <PointCloud name="pcd" value="$pointcloud" images="$cameras" colorMode="intensity"/>
 *   <Cuboid name="box" toName="pcd"/>
 * </View>
 * @example
 * <!-- Sequence (multi-frame) annotation with tracking. `frames` is a list of
 *      {"pointcloud": url, "pose": 4x4 LiDAR->world (optional), "cameras": [...] (optional)} -->
 * <View>
 *   <Labels name="label" toName="pcd">
 *     <Label value="Car"/>
 *   </Labels>
 *   <PointCloud name="pcd" value="$frames" frameRate="10"/>
 *   <Cuboid name="box" toName="pcd"/>
 * </View>
 * @name PointCloud
 * @meta_title PointCloud Tag for 3D LiDAR Labeling
 * @meta_description Customize Label Studio with the PointCloud tag to annotate 3D LiDAR point clouds with cuboids.
 * @param {string} name Name of the element
 * @param {string} value URL of the point cloud file (.pcd or .bin), or a list of frames for sequence annotation
 * @param {string} [images] Task data field with camera images and calibration for the fusion view
 * @param {pcd|bin} [format] Force the file format instead of detecting it from the URL
 * @param {number} [binDims=4] Number of float32 values per point in `.bin` files (KITTI 4, nuScenes 5)
 * @param {height|intensity|distance|rgb} [colorMode=height] Initial point coloring
 * @param {number} [pointSize=2] Initial point size, pixels
 * @param {number} [height=600] Height of the 3D view, pixels
 * @param {number} [defaultHeight=1.6] Height of a new box (metres) when there are no points to fit it to
 * @param {number} [frameRate=10] Sequences: playback speed, frames per second
 * @param {boolean} [fixedSize=true] Sequences: changing the size of a track changes it in every frame
 * @param {number} [cacheFrames=30] Sequences: how many decoded frames to keep in memory
 * @param {number} [overlayFrames=0] Sequences: initially overlay this many earlier frames (ego-motion compensated)
 * @param {boolean} [overlayFuture=false] Sequences: also overlay the same number of later frames
 * @example
 * <!-- Semantic segmentation: paint a class on every point (brush / lasso), see <PointLabels> -->
 * <View>
 *   <PointCloud name="pcd" value="$pointcloud"/>
 *   <PointLabels name="seg" toName="pcd">
 *     <Label value="Road" background="#FF00FF"/>
 *     <Label value="Car" background="#6496F5"/>
 *   </PointLabels>
 * </View>
 */
export const COLOR_MODES = ["height", "intensity", "distance", "rgb"];

const TagAttrs = types.model({
  value: types.maybeNull(types.string),
  images: types.maybeNull(types.string),
  format: types.maybeNull(types.string),
  bindims: types.optional(types.string, "4"),
  colormode: types.optional(types.string, "height"),
  pointsize: types.optional(types.string, "2"),
  height: types.optional(types.string, "600"),
  defaultheight: types.optional(types.string, "1.6"),
  framerate: types.optional(types.string, "10"),
  fixedsize: types.optional(types.string, "true"),
  cacheframes: types.optional(types.string, "30"),
  overlayframes: types.optional(types.string, "0"),
  overlayfuture: types.optional(types.string, "false"),
});

export const OVERLAY_CHOICES = [0, 1, 2, 3, 5, 10];

/** Label Studio endpoint that forwards a track to the project's interactive ML backend (pointcloud3d/api.py). */
const trackApi = () => `${window.APP_SETTINGS?.hostname ?? ""}/api/pointcloud3d/track/`;

function csrfHeaders() {
  const token = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1];

  return token ? { "X-CSRFToken": decodeURIComponent(token) } : {};
}

/** Frame list of a sequence task: strings (URLs) or objects with the URL, an ego pose and cameras. */
export function parseFrames(raw) {
  if (!Array.isArray(raw)) return null;
  return raw.map((item, index) => {
    if (typeof item === "string") return { url: item, pose: null, cameras: null, name: String(index) };
    const frame = item ?? {};

    return {
      url: String(frame.pointcloud ?? frame.url ?? frame.lidar ?? frame.path ?? ""),
      pose: normalizePose(frame.pose ?? frame.ego_pose ?? frame.lidar_to_world),
      cameras: frame.cameras ?? frame.images ?? null,
      name: String(frame.name ?? frame.timestamp ?? index),
    };
  });
}

function fetchCloud(url, format, binDims) {
  return fetch(url, { credentials: "same-origin" }).then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status} while loading ${url}`);
    return response.arrayBuffer().then((buffer) => {
      const cloud = parsePointCloud(buffer, detectFormat(response.url || url, format), { binDims });

      return { cloud, groundZ: estimateGroundZ(cloud.positions, cloud.count) };
    });
  });
}

const Model = types
  .model({
    type: "pointcloud",
    _url: types.maybeNull(types.string),
    // store labels inside the cuboid result (same approach as <Video> + <VideoRectangle>)
    mergeLabelsAndResults: true,
  })
  .volatile(() => ({
    // frames: [{url, pose, cameras, name}]; one entry for a single scan
    frames: [],
    isSequence: false,
    frame: 0,
    // decoded scans by frame index (not observable on purpose: only `cloud` drives rendering)
    frameCache: new Map(),
    pendingFrames: new Map(),
    taskCameras: null,
    cloud: null,
    groundZ: 0,
    loading: false,
    tracking: false,
    error: null,
    cameras: [],
    // "perspective" orbit view or "bev" (bird's-eye) view; drawing happens in BEV
    viewMode: "bev",
    // "select", "draw" (boxes), "brush" or "lasso" (point labels), "polyline" (lane lines)
    tool: "select",
    colorMode: "height",
    pointSize: 2,
    // incremented to ask the view to frame a region
    focusRequest: null,
    // multi-frame overlay: neighbouring scans moved into the current frame's coordinates
    overlayCount: 0,
    overlayFuture: false,
    overlay: null,
    overlayLoading: false,
    overlayToken: 0,
    // model-based tracking through the project's ML backend: {available, backend} or null
    modelTracker: null,
    modelTracking: false,
    modelTrackMessage: null,
    // frames (inclusive) picked for batch attribute editing, shown on the timeline
    attributeRange: null,
    // semantic segmentation (<PointLabels>)
    segVisible: true,
    segErase: false,
    segOnlyUnlabeled: false,
    // only paint points whose height above the ground estimate is within {min, max} metres
    segHeight: null,
    brushSize: 24,
    segMessage: null,
    // bumped while a stroke changes labels, so the view repaints before the stroke is stored
    segVersion: 0,
    segStroke: null,
    // polyline being drawn: vertices in the coordinates of the frame on screen
    lineDraft: null,
    // vertex of the selected polyline picked for Delete / height editing
    selectedVertex: null,
  }))
  .views((self) => ({
    get store() {
      return getRoot(self);
    },

    get cuboidControl() {
      return self.annotation?.toNames.get(self.name)?.find((s) => s.type === "cuboid");
    },

    /** Labels for boxes (`<PointLabels>` classes are painted on points instead). */
    states() {
      return self.annotation?.toNames
        .get(self.name)
        ?.filter((s) => s.type.endsWith("labels") && s.type !== "pointlabels");
    },

    get polylineControl() {
      return self.annotation?.toNames.get(self.name)?.find((s) => s.type === "polyline3d") ?? null;
    },

    get polylines() {
      return self.regs.filter((r) => r.type === "polyline3dregion");
    },

    get selectedPolyline() {
      return self.polylines.find((r) => r.selected) ?? null;
    },

    get segControl() {
      return self.annotation?.toNames.get(self.name)?.find((s) => s.type === "pointlabels") ?? null;
    },

    get segRegion() {
      return self.regs.find((r) => r.type === "pointsegmentationregion") ?? null;
    },

    /** Classes of `<PointLabels>`: [{name, color}]. */
    get segClasses() {
      return (self.segControl?.children ?? [])
        .filter((c) => c.type === "label" && c.value)
        .map((c) => ({ name: c.value, color: c.background }));
    },

    /** Class to paint with: the selected `<PointLabels>` label. */
    get paintClass() {
      return self.segControl?.selectedLabels?.[0]?.value ?? null;
    },

    get segFrame() {
      return self.isSequence ? self.frame : 0;
    },

    /** Labels of the scan on screen (mutable, see PointSegmentationRegion.labelsFor); null when none. */
    get segLabels() {
      // re-evaluated while a stroke changes the labels in place
      void self.segVersion;
      const region = self.segRegion;

      if (!self.cloud || !region) return null;
      void region.frames;
      return region.labelsFor(self.segFrame, self.cloud.count);
    },

    /** Stored labels were made for a scan with a different number of points. */
    get segMismatch() {
      const stored = self.segRegion?.frames[self.segFrame];

      return !!(self.cloud && stored && stored.count !== self.cloud.count);
    },

    /** Color per code (index 0 = unlabeled) for the region's class table. */
    get segPalette() {
      const region = self.segRegion;

      if (!region) return [null];
      return [
        null,
        ...region.classes.map((name) => self.segClasses.find((c) => c.name === name)?.color ?? "#9e9e9e"),
      ];
    },

    activeStates() {
      const states = self.states();

      return states ? states.filter((c) => c.isSelected === true) : null;
    },

    get hasStates() {
      const states = self.states();

      return states && states.length > 0;
    },

    /** `<FrameAttribute>` definitions of the connected `<Cuboid>`. */
    get attributeDefs() {
      return self.cuboidControl?.attributeDefs ?? [];
    },

    get cuboids() {
      return self.regs.filter((r) => r.type === "cuboidregion");
    },

    /** Boxes present in the current frame. */
    get visibleCuboids() {
      return self.cuboids.filter((r) => r.isVisible);
    },

    get selectedCuboid() {
      return self.cuboids.find((r) => r.selected) ?? null;
    },

    get framesCount() {
      return Math.max(1, self.frames.length);
    },

    /** Ego poses by frame index (null entries when unknown). */
    get poses() {
      return self.frames.map((f) => f.pose);
    },

    /** Frames carry ego poses, so overlays and interpolation are motion compensated. */
    get hasPoses() {
      return self.frames.some((f) => isPose(f.pose));
    },

    get overlayIndices() {
      if (!self.isSequence || !self.overlayCount) return [];
      return overlayIndices(self.frame, self.overlayCount, self.frames.length, self.overlayFuture);
    },

    get fixedSize() {
      return String(self.fixedsize).toLowerCase() !== "false";
    },

    get playbackFps() {
      return Math.min(30, Math.max(0.5, Number(self.framerate) || 10));
    },

    get viewHeight() {
      return Math.max(200, Number(self.height) || 600);
    },

    get newBoxHeight() {
      return Math.max(0.2, Number(self.defaultheight) || 1.6);
    },

    get canDraw() {
      return !!self.cuboidControl && !self.annotation?.isReadOnly();
    },

    isReadOnly() {
      return !!self.annotation?.isReadOnly();
    },

    /** Decoded scan of a frame, fetched once and shared by concurrent callers. */
    ensureFrame(index) {
      const cached = self.frameCache.get(index);

      if (cached) return Promise.resolve(cached);
      const pending = self.pendingFrames.get(index);

      if (pending) return pending;
      const url = self.frames[index]?.url;

      if (!url) return Promise.reject(new Error(`Frame ${index + 1} has no point cloud URL`));
      const promise = fetchCloud(url, self.format, Number(self.bindims) || 4)
        .then((entry) => {
          self.frameCache.set(index, entry);
          return entry;
        })
        .finally(() => self.pendingFrames.delete(index));

      self.pendingFrames.set(index, promise);
      return promise;
    },
  }))
  .actions((self) => ({
    afterCreate() {
      const mode = String(self.colormode || "").toLowerCase();

      self.colorMode = COLOR_MODES.includes(mode) ? mode : "height";
      self.pointSize = Math.min(10, Math.max(1, Number(self.pointsize) || 2));
      self.overlayCount = Math.min(10, Math.max(0, Math.round(Number(self.overlayframes) || 0)));
      self.overlayFuture = String(self.overlayfuture).toLowerCase() === "true";
    },

    updateValue(store) {
      const data = store.task?.dataObj;
      const raw = parseValue(self.value, data);
      const frames = parseFrames(raw);
      const key = frames ? JSON.stringify(frames.map((f) => f.url)) : String(raw ?? "");

      self.taskCameras = self.images ? parseValue(self.images, data) : null;
      if (key === self._url && (self.cloud || self.loading)) {
        self.updateCameras();
        return;
      }
      self._url = key;
      self.isSequence = !!frames;
      self.frames = frames ?? [{ url: String(raw ?? ""), pose: null, cameras: null, name: "0" }];
      self.frameCache.clear();
      self.pendingFrames.clear();
      self.cloud = null;
      self.overlay = null;
      self.setFrame(Math.min(self.frame, self.frames.length - 1));
      if (self.isSequence) self.checkModelTracker();
    },

    updateCameras() {
      const own = self.frames[self.frame]?.cameras;

      self.cameras = parseCameraImages(own ?? self.taskCameras);
    },

    applyFrame(index, entry) {
      if (index !== self.frame) return;
      self.cloud = entry.cloud;
      self.groundZ = entry.groundZ;
      self.error = null;
      if (self.colorMode === "rgb" && !entry.cloud.colors) {
        self.colorMode = entry.cloud.intensity ? "intensity" : "height";
      }
      // refresh cached point counts of the boxes for this scan
      self.cuboids.forEach((r) => r.updatePointCount?.());
    },

    setLoadState(index, loading, error = null) {
      if (index !== self.frame) return;
      self.loading = loading;
      if (error) self.error = error;
    },

    /** Show a frame; resolves when its scan is on screen. */
    setFrame: flow(function* (index) {
      const target = Math.min(Math.max(0, Math.round(Number(index) || 0)), self.frames.length - 1);

      self.frame = target;
      self.updateCameras();
      const cached = self.frameCache.get(target);

      if (cached) {
        self.applyFrame(target, cached);
        self.setLoadState(target, false);
      } else {
        self.setLoadState(target, true);
        try {
          const entry = yield self.ensureFrame(target);

          self.applyFrame(target, entry);
        } catch (e) {
          console.error(e);
          self.setLoadState(target, false, e?.message ?? String(e));
        } finally {
          self.setLoadState(target, false);
        }
      }
      self.prefetch(target);
      self.refreshOverlay();
    }),

    setOverlay(count, future = self.overlayFuture) {
      self.overlayCount = Math.min(10, Math.max(0, Math.round(Number(count) || 0)));
      self.overlayFuture = !!future;
      self.refreshOverlay();
    },

    applyOverlay(token, overlay, loading = false) {
      if (token !== self.overlayToken) return;
      self.overlay = overlay;
      self.overlayLoading = loading;
    },

    /** Rebuild the overlay for the current frame; stale results (frame changed meanwhile) are dropped. */
    refreshOverlay: flow(function* () {
      self.overlayToken += 1;
      const token = self.overlayToken;
      const frame = self.frame;
      const indices = self.overlayIndices;

      if (!indices.length) {
        self.applyOverlay(token, null);
        return;
      }
      // an overlay built for another frame would be misaligned; cached frames resolve within a microtask
      self.applyOverlay(token, null, true);
      try {
        const entries = yield Promise.all(
          indices.map((index) =>
            self
              .ensureFrame(index)
              .then((entry) => ({ offset: index - frame, cloud: entry.cloud, pose: self.poses[index] }))
              .catch(() => null),
          ),
        );
        const sources = entries.filter(Boolean);

        self.applyOverlay(token, sources.length ? accumulateFrames(sources, self.poses[frame]) : null);
      } catch (e) {
        console.error(e);
        self.applyOverlay(token, null);
      }
    }),

    stepFrame(delta) {
      if (!self.isSequence) return null;
      return self.setFrame(self.frame + delta);
    },

    /** Load the next frames in the background and drop scans far from the current frame. */
    prefetch(index) {
      if (!self.isSequence) return;
      for (const next of [index + 1, index + 2]) {
        if (next < self.frames.length) self.ensureFrame(next).catch(() => {});
      }
      // keep every overlaid frame (both directions) plus a little look-ahead in memory
      const limit = Math.max(3, Number(self.cacheframes) || 30, 2 * self.overlayCount + 3);

      if (self.frameCache.size > limit) {
        const byDistance = [...self.frameCache.keys()].sort((a, b) => Math.abs(b - index) - Math.abs(a - index));

        for (const key of byDistance.slice(0, self.frameCache.size - limit)) self.frameCache.delete(key);
      }
    },

    setTracking(value) {
      self.tracking = value;
    },

    setModelTrackState(patch) {
      Object.assign(self, patch);
    },

    /** Ask Label Studio whether a tracking model is connected to this project (hidden button otherwise). */
    checkModelTracker: flow(function* () {
      const taskId = self.store?.task?.id;

      if (!taskId || typeof fetch !== "function") return;
      try {
        const response = yield fetch(`${trackApi()}?task=${encodeURIComponent(taskId)}`, {
          credentials: "same-origin",
        });
        const body = response.ok ? yield response.json() : null;

        self.setModelTrackState({ modelTracker: body?.available ? body : null });
      } catch {
        // not running inside Label Studio, or the endpoint is unavailable: keep the local tracker only
        self.setModelTrackState({ modelTracker: null });
      }
    }),

    /**
     * Send one track (the annotator's keyframes) to the tracking model and replace its keyframes with the
     * result. The annotator's keyframes come back unchanged; the whole update is one undo step.
     */
    trackWithModel: flow(function* (region) {
      const taskId = self.store?.task?.id;

      if (!taskId || !region?.isTrack || region.isReadOnly() || self.modelTracking) return;
      self.setModelTrackState({ modelTracking: true, modelTrackMessage: null });
      try {
        const response = yield fetch(trackApi(), {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            task: taskId,
            result: [
              {
                id: region.cleanId,
                from_name: self.cuboidControl?.name,
                to_name: self.name,
                type: "cuboid",
                value: { sequence: region.sequence, framesCount: self.framesCount, labels: region.labels },
              },
            ],
          }),
        });
        const body = yield response.json().catch(() => ({}));

        if (!response.ok) throw new Error(body?.detail ?? `HTTP ${response.status}`);
        region.applySequence(body.sequence);
        const tracked = body.meta?.tracked_frames?.length ?? 0;

        self.setModelTrackState({
          modelTrackMessage: body.meta?.static
            ? "The model sees a parked object: the box is held in place"
            : `The model tracked ${tracked} more frame${tracked === 1 ? "" : "s"} (frames ${(body.meta?.first ?? 0) + 1}–${(body.meta?.last ?? 0) + 1})`,
        });
      } catch (e) {
        console.error(e);
        self.setModelTrackState({ modelTrackMessage: `Tracking failed: ${e?.message ?? e}` });
      } finally {
        self.setModelTrackState({ modelTracking: false });
      }
    }),

    /**
     * Track `region` forward from the current frame for up to `steps` frames: each step predicts the box
     * with the track's own velocity (ego-motion compensated when poses exist) and re-centres it on the
     * points of the next scan. Stops at the end of the sequence or when the object is lost.
     */
    autoTrack: flow(function* (region, steps = 1) {
      if (!self.isSequence || self.tracking || !region?.isTrack || region.isReadOnly()) return;
      const history = self.annotation?.history;
      const key = "pointcloud-track";

      self.setTracking(true);
      history?.freeze(key);
      let frame = self.frame;

      try {
        for (let k = 0; k < steps && frame + 1 < self.frames.length; k++) {
          const box = region.boxAtFrame(frame);

          if (!box) break;
          const source = yield self.ensureFrame(frame);
          const target = yield self.ensureFrame(frame + 1);
          const result = trackStep({
            box,
            sourceCloud: source.cloud,
            targetCloud: target.cloud,
            sourcePose: self.poses[frame],
            targetPose: self.poses[frame + 1],
            velocity: estimateVelocity(region.sequence, frame, self.poses),
          });

          // the first step always produces a keyframe (the prediction) so the user sees where it went
          if (result.lost && k > 0) break;
          region.setKeyframe(frame + 1, result.box);
          frame += 1;
          if (result.lost) break;
        }
      } catch (e) {
        console.error(e);
      } finally {
        history?.unfreeze(key);
        self.setTracking(false);
      }
      yield self.setFrame(frame);
    }),

    setViewMode(mode) {
      self.viewMode = mode;
      if (mode !== "bev" && self.tool === "draw") self.tool = "select";
    },

    setTool(tool) {
      if (tool === "draw" && !self.canDraw) return;
      if ((tool === "brush" || tool === "lasso") && !self.segControl) return;
      if (tool === "polyline" && (!self.polylineControl || self.annotation?.isReadOnly())) return;
      if (tool !== "polyline") self.lineDraft = null;
      self.tool = tool;
      if (tool === "draw") self.viewMode = "bev";
      // a selected box would take the class clicks; painting works on points, not on regions
      if (tool === "brush" || tool === "lasso") self.annotation?.unselectAll?.();
    },

    setSegOption(patch) {
      const allowed = ["segVisible", "segErase", "segOnlyUnlabeled", "segHeight", "brushSize", "segMessage"];

      for (const key of allowed) if (key in patch) self[key] = patch[key];
      if ("brushSize" in patch) self.brushSize = Math.min(150, Math.max(2, Number(patch.brushSize) || 24));
    },

    /** The segmentation result of this point cloud, created on first use. */
    ensureSegRegion() {
      if (self.segRegion) return self.segRegion;
      const control = self.segControl;

      if (!control || self.annotation.isReadOnly()) return null;
      // skip "select after create": selecting it would only get in the way of picking classes
      return self.annotation.createResult({ classes: [], frames: {} }, { pointlabels: [] }, control, self, true);
    },

    /**
     * Run `apply(labels, region)` on the labels of the frame on screen and store the result as one undo step.
     * `apply` returns the number of changed points.
     */
    editSegmentation(apply) {
      const cloud = self.cloud;

      if (!cloud || !self.segControl || self.annotation.isReadOnly()) return 0;
      if (self.segMismatch) {
        self.segMessage = "The stored labels of this frame don't match its point count; clear the frame first.";
        return 0;
      }
      const history = self.annotation.history;

      history?.freeze("pointcloud-seg");
      try {
        const region = self.ensureSegRegion();
        const labels = region?.labelsFor(self.segFrame, cloud.count);

        if (!labels) return 0;
        const changed = apply(labels, region);

        if (changed) region.commitFrame(self.segFrame, labels);
        return changed;
      } finally {
        history?.unfreeze("pointcloud-seg");
      }
    },

    /** Start a brush / lasso stroke; Shift (or the eraser) erases. Returns false when nothing can be painted. */
    beginSegStroke(erase = false) {
      const cloud = self.cloud;
      const removing = erase || self.segErase;

      if (!cloud || !self.segControl || self.annotation.isReadOnly() || self.segStroke) return false;
      if (!removing && !self.paintClass) {
        self.segMessage = "Pick a class above to paint with.";
        return false;
      }
      if (self.segMismatch) {
        self.segMessage = "The stored labels of this frame don't match its point count; clear the frame first.";
        return false;
      }
      self.annotation.history?.freeze("pointcloud-seg-stroke");
      const region = self.ensureSegRegion();
      const labels = region?.labelsFor(self.segFrame, cloud.count);

      if (!labels) {
        self.annotation.history?.unfreeze("pointcloud-seg-stroke");
        return false;
      }
      self.segMessage = null;
      self.segStroke = {
        region,
        labels,
        frame: self.segFrame,
        code: removing ? 0 : region.codeFor(self.paintClass),
        changed: 0,
      };
      return true;
    },

    paintSeg(indices) {
      const stroke = self.segStroke;

      if (!stroke || stroke.frame !== self.segFrame || !self.cloud) return;
      const changed = paint(stroke.labels, indices, stroke.code, {
        onlyUnlabeled: self.segOnlyUnlabeled,
        height: self.segHeight,
        groundZ: self.groundZ,
        positions: self.cloud.positions,
      });

      if (changed) {
        stroke.changed += changed;
        self.segVersion += 1;
      }
    },

    endSegStroke() {
      const stroke = self.segStroke;

      if (!stroke) return;
      self.segStroke = null;
      if (stroke.changed && isAlive(stroke.region)) stroke.region.commitFrame(stroke.frame, stroke.labels);
      self.annotation.history?.unfreeze("pointcloud-seg-stroke");
    },

    /** Points inside the 3D boxes of this frame take the box's class when `<PointLabels>` has the same name. */
    fillSegFromBoxes() {
      const cloud = self.cloud;
      const byName = new Map(self.segClasses.map((c) => [c.name.toLowerCase(), c.name]));
      let boxes = 0;
      let unmatched = 0;
      const changed = self.editSegmentation((labels, region) => {
        let total = 0;

        for (const cuboid of self.visibleCuboids) {
          const name = byName.get(String(cuboid.labelName ?? "").toLowerCase());

          if (!name) {
            unmatched++;
            continue;
          }
          const box = cuboid.box;
          // leave the ground under the box alone
          const bottom = box.z - box.height / 2 + 0.1;
          const inside = pointIndicesInBox(box, cloud.positions, cloud.count).filter(
            (i) => cloud.positions[i * 3 + 2] > bottom,
          );

          total += paint(labels, inside, region.codeFor(name), { onlyUnlabeled: self.segOnlyUnlabeled });
          boxes++;
        }
        return total;
      });

      self.segMessage =
        `${changed.toLocaleString()} points labeled from ${boxes} box${boxes === 1 ? "" : "es"}` +
        (unmatched ? ` · ${unmatched} box${unmatched === 1 ? " has" : "es have"} no class of the same name` : "");
    },

    /** Label the ground points of this frame with the selected class (e.g. Road). */
    labelSegGround() {
      const name = self.paintClass;
      const cloud = self.cloud;

      if (!name || !cloud) {
        self.segMessage = "Pick the ground class (e.g. Road) above first.";
        return;
      }
      const mask = groundMask(cloud.positions, cloud.count, { groundZ: self.groundZ });
      const indices = [];

      for (let i = 0; i < mask.length; i++) if (mask[i]) indices.push(i);
      const changed = self.editSegmentation((labels, region) =>
        paint(labels, indices, region.codeFor(name), { onlyUnlabeled: self.segOnlyUnlabeled }),
      );

      self.segMessage = `${changed.toLocaleString()} ground points labeled ${name}`;
    },

    clearSegFrame() {
      const changed = self.editSegmentation((labels) => {
        let n = 0;

        for (let i = 0; i < labels.length; i++) {
          if (labels[i]) {
            labels[i] = 0;
            n++;
          }
        }
        return n;
      });

      self.segMessage = `${changed.toLocaleString()} labels removed from this frame`;
    },

    /**
     * Sequences: carry labels over from a neighbouring frame (ego-motion compensated when poses are known).
     * Static scenery lines up; points of moving objects must be checked.
     */
    copySegFromFrame: flow(function* (offset = -1) {
      const region = self.segRegion;
      const source = self.frame + offset;

      if (!self.isSequence || !region || source < 0 || source >= self.framesCount || !region.frames[source]) {
        self.segMessage = `Frame ${source + 1} has no point labels to copy.`;
        return;
      }
      const target = self.frame;
      let entry;

      try {
        entry = yield self.ensureFrame(source);
      } catch (e) {
        self.segMessage = `Can't load frame ${source + 1}: ${e?.message ?? e}`;
        return;
      }
      if (self.frame !== target || !self.cloud) return;
      const sourceLabels = region.labelsFor(source, entry.cloud.count);

      if (!sourceLabels) {
        self.segMessage = `The labels of frame ${source + 1} don't match its point count.`;
        return;
      }
      const moved = transferLabels(
        { positions: entry.cloud.positions, count: entry.cloud.count, labels: sourceLabels },
        relativeTransform(self.poses[source], self.poses[target]),
        { positions: self.cloud.positions, count: self.cloud.count },
      );
      const changed = self.editSegmentation((labels) => {
        let n = 0;

        for (let i = 0; i < labels.length; i++) {
          if (!moved[i] || labels[i] === moved[i]) continue;
          if (self.segOnlyUnlabeled && labels[i]) continue;
          labels[i] = moved[i];
          n++;
        }
        return n;
      });

      self.segMessage =
        `${changed.toLocaleString()} points labeled from frame ${source + 1}` +
        (self.hasPoses ? "" : " (no ego poses: frames compared without motion compensation)");
    }),

    setColorMode(mode) {
      self.colorMode = mode;
    },

    setPointSize(size) {
      self.pointSize = Math.min(10, Math.max(1, Number(size) || 1));
    },

    requestFocus(region) {
      self.focusRequest = { id: region?.id ?? null, at: Date.now() };
    },

    /**
     * Create a cuboid from a footprint drawn in the bird's-eye view and snap it to the points.
     * @param {{x:number,y:number,length:number,width:number,yaw:number}} footprint
     */
    addCuboidFromFootprint(footprint, { autoFit = true } = {}) {
      const height = self.newBoxHeight;
      let box = {
        ...footprint,
        height,
        z: self.groundZ + height / 2,
      };

      if (autoFit && self.cloud) {
        box = fitBoxToPoints(box, self.cloud.positions, self.cloud.count, {
          fitSize: false,
          defaultHeight: height,
          groundZ: self.groundZ,
        });
      }
      return self.addCuboid(valueFromBox(box));
    },

    setAttributeRange(range) {
      if (!range) {
        self.attributeRange = null;
        return;
      }
      const last = self.framesCount - 1;
      const clamp = (v) => Math.min(Math.max(0, Math.round(Number(v) || 0)), last);
      const start = clamp(range.start);
      const end = clamp(range.end);

      self.attributeRange = { start: Math.min(start, end), end: Math.max(start, end) };
    },

    /**
     * Batch edit: set `key` to `value` in frames `start`..`end` of every given track, as one undo step.
     * Returns the number of tracks that changed.
     */
    applyAttributeToTracks(regions, key, value, start, end) {
      const history = self.annotation?.history;
      let changed = 0;

      history?.freeze("pointcloud-attributes");
      try {
        for (const region of regions) {
          if (!region.isReadOnly() && region.setAttributeRange(key, value, start, end)) changed++;
        }
      } finally {
        history?.unfreeze("pointcloud-attributes");
      }
      return changed;
    },

    /** Ground height under (x, y) in the scan on screen. */
    groundAt(x, y) {
      const cloud = self.cloud;

      if (!cloud) return self.groundZ;
      return groundHeightAt(cloud.positions, cloud.count, x, y, { fallback: self.groundZ, groundZ: self.groundZ });
    },

    addDraftPoint(x, y) {
      if (!self.polylineControl || self.annotation.isReadOnly()) return;
      self.lineDraft = [...(self.lineDraft ?? []), [x, y, self.groundAt(x, y)]];
    },

    removeDraftPoint() {
      if (!self.lineDraft?.length) return false;
      self.lineDraft = self.lineDraft.slice(0, -1);
      return true;
    },

    cancelDraft() {
      self.lineDraft = null;
    },

    /** Finish the polyline being drawn (needs 2 vertices); stays in the polyline tool for the next one. */
    finishDraft() {
      const points = self.lineDraft ?? [];

      self.lineDraft = null;
      if (points.length < 2) return null;
      // in a sequence the frame on screen becomes the polyline's reference frame
      const value = { points, ...(self.isSequence ? { frame: self.frame } : {}) };
      const area = self.createRegionWithLabels(value, self.polylineControl);

      if (area) self.annotation.selectArea(area);
      return area;
    },

    setSelectedVertex(index) {
      self.selectedVertex = Number.isInteger(index) ? index : null;
    },

    /** Region for a box / polyline control, labeled with the labels selected right now. */
    createRegionWithLabels(areaValue, control) {
      const activeStates = self.activeStates() ?? [];
      const area = ff.isActive(ff.FF_MULTIPLE_LABELS_REGIONS)
        ? self.annotation.createResult(areaValue, {}, control, self, false, activeStates)
        : self.annotation.createResult(areaValue, {}, control, self, false);

      if (area && !ff.isActive(ff.FF_MULTIPLE_LABELS_REGIONS)) {
        for (const tag of activeStates) {
          area.setValue(tag);
        }
      }
      return area;
    },

    /** New box; in a sequence it becomes a track with one keyframe at the current frame. */
    addCuboid(value) {
      const control = self.cuboidControl;

      if (!control) {
        console.error("No <Cuboid> control is connected to this <PointCloud>");
        return null;
      }
      if (self.annotation.isReadOnly()) return null;

      const areaValue = self.isSequence
        ? { sequence: [{ frame: self.frame, enabled: true, ...value }], framesCount: self.framesCount }
        : value;
      const area = self.createRegionWithLabels(areaValue, control);

      area?.updatePointCount?.();
      return area;
    },
  }));

export const PointCloudModel = types.compose(
  "PointCloudModel",
  TagAttrs,
  ObjectBase,
  AnnotationMixin,
  Model,
);
