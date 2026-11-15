import { inject, observer } from "mobx-react";
import Registry from "../../../core/Registry";

import { HtxPointCloud as HtxPointCloudView } from "./HtxPointCloud";
import { PointCloudModel } from "./PointCloud";

const HtxPointCloud = inject("store")(observer(HtxPointCloudView));

Registry.addTag("pointcloud", PointCloudModel, HtxPointCloud);
Registry.addObjectType(PointCloudModel);

export { PointCloudModel, HtxPointCloud };
