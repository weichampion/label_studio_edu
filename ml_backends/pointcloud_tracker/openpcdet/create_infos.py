"""Create OpenPCDet info files for the exported dataset (run inside OpenPCDet's tools/ directory).

usage: python create_infos.py <dataset.yaml> <Class1,Class2,...>

OpenPCDet's own ``custom_dataset.py __main__`` uses fixed paths and class names; this calls the same
function with the dataset exported for one Label Studio project.
"""

import sys
from pathlib import Path

import yaml
from easydict import EasyDict
from pcdet.datasets.custom.custom_dataset import create_custom_infos

if __name__ == '__main__':
    cfg = EasyDict(yaml.safe_load(open(sys.argv[1])))
    data_path = Path(cfg.DATA_PATH)
    create_custom_infos(
        dataset_cfg=cfg,
        class_names=sys.argv[2].split(','),
        data_path=data_path,
        save_path=data_path,
    )
