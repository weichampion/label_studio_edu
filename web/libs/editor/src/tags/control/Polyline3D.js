import { observer } from "mobx-react";
import { types } from "mobx-state-tree";

import Registry from "../../core/Registry";
import { guidGenerator } from "../../core/Helpers";
import Types from "../../core/Types";
import ControlBase from "./Base";

/**
 * The `Polyline3D` tag adds 3D polylines to a `<PointCloud>`: lane lines, road edges, stop lines, crosswalk
 * borders. Combine it with `<Labels>` for the line type and per-region `<Choices>` for attributes such as color.
 *
 * Click on the road to add vertices (heights snap to the ground), double-click or Enter to finish. Drag
 * vertices to adjust, drag the small midpoint handles to insert one, Alt-click a vertex to remove it.
 * "Snap to Markings" moves a rough line onto the painted marking using LiDAR intensity.
 *
 * In a sequence a polyline is drawn once and shown in every frame through the ego poses (lane markings don't
 * move). Result value, LiDAR frame of `frame` (omitted for single scans):
 * `{"points": [[x, y, z], ...], "frame": 0, "labels": ["Dashed"]}`
 * @example
 * <View>
 *   <Labels name="lane_type" toName="pcd">
 *     <Label value="Solid" background="#FFFFFF"/>
 *     <Label value="Dashed" background="#FADB14"/>
 *     <Label value="Road Edge" background="#FA541C"/>
 *   </Labels>
 *   <PointCloud name="pcd" value="$pointcloud" colorMode="intensity"/>
 *   <Polyline3D name="lane" toName="pcd"/>
 * </View>
 * @name Polyline3D
 * @meta_title Polyline3D Tag for 3D Lane Lines
 * @meta_description Customize Label Studio with the Polyline3D tag to annotate lane lines and road edges in LiDAR point clouds.
 * @param {string} name Name of the element
 * @param {string} toName Name of the `<PointCloud>` element to control
 */
const TagAttrs = types.model({
  toname: types.maybeNull(types.string),
});

const ModelAttrs = types.model("Polyline3DModel", {
  pid: types.optional(types.string, guidGenerator),
  type: "polyline3d",
  controlledTags: Types.unionTag(["PointCloud"]),
});

const Polyline3DModel = types.compose("Polyline3DModel", ControlBase, ModelAttrs, TagAttrs);

const HtxPolyline3D = observer(() => null);

Registry.addTag("polyline3d", Polyline3DModel, HtxPolyline3D);

export { HtxPolyline3D, Polyline3DModel };
