import { types } from "mobx-state-tree";

import Registry from "../../core/Registry";
import { guidGenerator } from "../../core/Helpers";
import Types from "../../core/Types";

/**
 * The `FrameAttribute` tag declares an attribute of 3D boxes that can change from frame to frame along a
 * track, such as occlusion, truncation or "parked / moving". Put it inside `<Cuboid>`.
 *
 * Set it for the box on screen ("from this frame on"), or for a range of frames of one or many tracks at
 * once from the timeline. Values are stored in the cuboid result as
 * `"frameAttributes": {"occluded": [{"frame": 0, "value": "0"}, {"frame": 12, "value": "2"}]}`
 * — each value holds until the next change. Attributes named like `occluded` / `truncated` fill the
 * matching KITTI columns on export.
 *
 * For attributes that never change along a track (vehicle color, ...) use per-region `<Choices>` instead.
 * @example
 * <View>
 *   <Labels name="label" toName="pcd">
 *     <Label value="Car"/>
 *   </Labels>
 *   <PointCloud name="pcd" value="$frames"/>
 *   <Cuboid name="box" toName="pcd">
 *     <FrameAttribute value="occluded" title="Occlusion" choices="0,1,2,3" default="0"/>
 *     <FrameAttribute value="moving" title="Moving"/>
 *   </Cuboid>
 * </View>
 * @name FrameAttribute
 * @meta_title FrameAttribute Tag for Per-Frame 3D Box Attributes
 * @meta_description Customize Label Studio with per-frame attributes of 3D boxes and tracks.
 * @param {string} value Attribute key stored in the result
 * @param {string} [title] Name shown in the editor
 * @param {string} [choices] Comma-separated values; without it the attribute is yes / no
 * @param {string} [default] Value for frames that were never set (default: the first choice, or "no")
 */
const TagAttrs = types.model({
  value: types.maybeNull(types.string),
  title: types.maybeNull(types.string),
  choices: types.maybeNull(types.string),
  default: types.maybeNull(types.string),
});

const Model = types.model({
  id: types.optional(types.identifier, guidGenerator),
  type: "frameattribute",
  // the config validator reports a <FrameAttribute> that is not inside <Cuboid>
  parentTypes: Types.tagsTypes(["Cuboid"]),
});

const FrameAttributeModel = types.compose("FrameAttributeModel", TagAttrs, Model);

const HtxFrameAttribute = () => null;

Registry.addTag("frameattribute", FrameAttributeModel, HtxFrameAttribute);

export { HtxFrameAttribute, FrameAttributeModel };
