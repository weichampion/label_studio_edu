"""Label Studio export converter with the 3D point cloud formats added.

Drop-in replacement for ``label_studio_sdk.converter.Converter``: all SDK formats keep working. Projects with a
``<Cuboid>`` on a ``<PointCloud>`` additionally offer ``OPENPCDET_3D``, ``KITTI_3D`` and ``KITTI_TRACKING``,
projects with ``<PointLabels>`` offer ``SEMANTIC_KITTI`` and projects with ``<Polyline3D>`` offer ``LANE3D_JSON``,
in the export dialog, snapshot exports and
``/api/projects/<id>/export?exportType=...``. Going through the regular export API means the school edition's
"导出数据" (export_data) grant is enforced unchanged.
"""

from label_studio_sdk.converter import Converter
from pointcloud3d.formats import (
    BOX_FORMAT_NAMES,
    FORMAT_INFO,
    LANE_FORMAT_NAMES,
    SEGMENTATION_FORMAT_NAMES,
    convert_lanes,
    convert_segmentation,
    convert_tasks,
    load_tasks,
    pointcloud_spec,
    polyline_spec,
    polyline_spec_from_parsed_config,
    segmentation_spec,
    segmentation_spec_from_parsed_config,
    spec_from_parsed_config,
)


class PointCloudConverter(Converter):
    def __init__(self, *args, label_config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pointcloud_spec = pointcloud_spec(label_config) if label_config else []
        if not self._pointcloud_spec:
            self._pointcloud_spec = spec_from_parsed_config(self._schema)
        self._segmentation_spec = segmentation_spec(label_config) if label_config else []
        if not self._segmentation_spec:
            self._segmentation_spec = segmentation_spec_from_parsed_config(self._schema)
        self._polyline_spec = polyline_spec(label_config) if label_config else []
        if not self._polyline_spec:
            self._polyline_spec = polyline_spec_from_parsed_config(self._schema)
        extra = set()
        if self._pointcloud_spec:
            extra |= BOX_FORMAT_NAMES
        if self._segmentation_spec:
            extra |= SEGMENTATION_FORMAT_NAMES
        if self._polyline_spec:
            extra |= LANE_FORMAT_NAMES
        if extra:
            self._supported_formats = list(self._supported_formats) + sorted(extra)

    def all_formats(self):
        return {**super().all_formats(), **FORMAT_INFO}

    def convert(self, input_data, output_data, format, is_dir=True, **kwargs):
        name = getattr(format, 'name', format)
        if name in SEGMENTATION_FORMAT_NAMES:
            tasks = load_tasks(input_data, is_dir=is_dir)
            return convert_segmentation(tasks, output_data, self._segmentation_spec)
        if name in LANE_FORMAT_NAMES:
            tasks = load_tasks(input_data, is_dir=is_dir)
            return convert_lanes(tasks, output_data, self._polyline_spec)
        if name in BOX_FORMAT_NAMES:
            tasks = load_tasks(input_data, is_dir=is_dir)
            return convert_tasks(tasks, output_data, name, self._pointcloud_spec)
        return super().convert(input_data, output_data, format, is_dir=is_dir, **kwargs)
