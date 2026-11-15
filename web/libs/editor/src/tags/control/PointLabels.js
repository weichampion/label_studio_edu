import { observer } from "mobx-react";
import { types } from "mobx-state-tree";

import LabelMixin from "../../mixins/LabelMixin";
import Registry from "../../core/Registry";
import SelectedModelMixin from "../../mixins/SelectedModel";
import Types from "../../core/Types";
import { HtxLabels, LabelsModel } from "./Labels/Labels";
import ControlBase from "./Base";

/**
 * The `PointLabels` tag adds semantic segmentation of LiDAR point clouds: every point gets one class.
 * Pick a class, then paint points with the brush or the lasso of `<PointCloud>`; ground, the points inside
 * 3D boxes and (in sequences) the previous frame's labels can be filled in automatically.
 *
 * One result per point cloud holds the labels of all frames, run-length encoded:
 * `{"classes": ["Road", "Car"], "frames": [{"frame": 0, "count": 120000, "rle": "..."}], "pointlabels": ["Road", "Car"]}`.
 * Export it as SemanticKITTI `.label` files.
 *
 * Use with the following data types: point clouds (.pcd, .bin), single scans or sequences.
 * @example
 * <View>
 *   <PointCloud name="pcd" value="$pointcloud"/>
 *   <PointLabels name="seg" toName="pcd">
 *     <Label value="Road" background="#FF00FF"/>
 *     <Label value="Sidewalk" background="#4B004B"/>
 *     <Label value="Vegetation" background="#00AF00"/>
 *     <Label value="Car" background="#6496F5"/>
 *   </PointLabels>
 * </View>
 * @name PointLabels
 * @regions PointSegmentationRegion
 * @meta_title PointLabels Tag for Point Cloud Semantic Segmentation
 * @meta_description Customize Label Studio with the PointLabels tag to label every point of a LiDAR point cloud.
 * @param {string} name                      - Name of the element
 * @param {string} toName                    - Name of the `<PointCloud>` to label
 * @param {boolean} [showInline=true]        - Show labels in the same visual line
 */
const Validation = types.model({
  controlledTags: Types.unionTag(["PointCloud"]),
});

const ModelAttrs = types.model("PointLabelsModel", {
  type: "pointlabels",
  children: Types.unionArray(["label", "header", "view", "hypertext"]),
});

const PointLabelsModel = types.compose(
  "PointLabelsModel",
  ControlBase,
  LabelsModel,
  ModelAttrs,
  Validation,
  LabelMixin,
  SelectedModelMixin.props({ _child: "LabelModel" }),
);

const HtxPointLabels = observer(({ item }) => <HtxLabels item={item} />);

Registry.addTag("pointlabels", PointLabelsModel, HtxPointLabels);

export { HtxPointLabels, PointLabelsModel };
