import { observer } from "mobx-react";
import { types } from "mobx-state-tree";

import Registry from "../../core/Registry";
import { guidGenerator } from "../../core/Helpers";
import Types from "../../core/Types";
import ControlBase from "./Base";
import { attributeDef } from "../object/PointCloud/attributes";
import "./FrameAttribute";

/**
 * The `Cuboid` tag adds 3D bounding box (cuboid) annotation to a `<PointCloud>`.
 * Combine it with `<Labels>` to assign classes to the boxes, and with per-region
 * `<Choices>` / `<TextArea>` for attributes such as occlusion or track id. Attributes that change along a
 * track go into `<FrameAttribute>` children.
 *
 * Use with the following data types: point clouds (.pcd, .bin)
 *
 * Result value, in the LiDAR frame (x forward, y left, z up; metres / radians):
 * `{"position": {"x","y","z"}, "dimensions": {"length","width","height"}, "rotation": {"x":0,"y":0,"z": yaw}, "labels": [...]}`
 * @example
 * <!-- 3D object detection for autonomous driving -->
 * <View>
 *   <Labels name="label" toName="pcd">
 *     <Label value="Car" background="#FFA39E"/>
 *     <Label value="Pedestrian" background="#D4380D"/>
 *   </Labels>
 *   <PointCloud name="pcd" value="$pointcloud"/>
 *   <Cuboid name="box" toName="pcd"/>
 * </View>
 * @name Cuboid
 * @meta_title Cuboid Tag for 3D Bounding Boxes
 * @meta_description Customize Label Studio with the Cuboid tag to draw 3D bounding boxes on LiDAR point clouds.
 * @param {string} name Name of the element
 * @param {string} toName Name of the `<PointCloud>` element to control
 */
const TagAttrs = types.model({
  toname: types.maybeNull(types.string),
});

const ModelAttrs = types
  .model("CuboidModel", {
    pid: types.optional(types.string, guidGenerator),
    type: "cuboid",
    controlledTags: Types.unionTag(["PointCloud"]),
    children: Types.unionArray(["frameattribute"]),
  })
  .views((self) => ({
    /** Per-frame attribute definitions from the `<FrameAttribute>` children (the first one of a key wins). */
    get attributeDefs() {
      const defs = [];

      for (const tag of self.children ?? []) {
        const def = attributeDef(tag);

        if (def && !defs.some((d) => d.key === def.key)) defs.push(def);
      }
      return defs;
    },
  }));

const CuboidModel = types.compose("CuboidModel", ControlBase, ModelAttrs, TagAttrs);

const HtxCuboid = observer(() => null);

Registry.addTag("cuboid", CuboidModel, HtxCuboid);

export { HtxCuboid, CuboidModel };
