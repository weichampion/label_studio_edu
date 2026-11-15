# 3D 点云检测与跟踪 ML 后端（Label Studio）

给 `<PointCloud>` + `<Cuboid>` 项目使用的模型服务，实现 Label Studio 标准 ML 后端协议
（`/health`、`/setup`、`/predict`、`/versions`、`/webhook`），只依赖 Python 标准库；接入深度学习检测器时再装 OpenPCDet。

| 用法 | 效果 |
|---|---|
| Data Manager → 选任务 → **Retrieve Predictions**（或开启自动预标注） | 单帧：检测出的 3D 框；连续帧：逐帧检测 + 多目标跟踪，输出带轨迹 ID 的关键帧轨迹 |
| 编辑器中选中一条轨迹 → **Track With Model**（快捷键 M） | 从人工关键帧出发，用模型把这条轨迹延伸到整段序列；人工关键帧保持不变，一次 Ctrl+Z 可撤销 |
| 项目 Settings → Model → **Start Training** | 用本项目已完成的标注训练/微调检测模型，之后本项目的预测和跟踪都用新模型 |

## 组成

| 模块 | 内容 |
|---|---|
| `tracker3d/detector.py` | `ClusterDetector`：去地面 → 按距离自适应的 BEV 聚类 → 最小面积矩形 → 按尺寸分类，并用类别先验补全只看到一面的目标（只见车尾时向远离传感器方向补全车长）。`OpenPCDetDetector`：任意 OpenPCDet 模型（PointPillars、SECOND、PV-RCNN、CenterPoint…） |
| `tracker3d/tracker.py` | AB3DMOT 风格多目标跟踪：世界坐标（用帧位姿扣除自车运动）恒速卡尔曼滤波、按类别门限的贪心关联、出生/消亡管理；轨迹级处理：类别投票 + 速度纠正（跑得快的“行人”是骑行者）、稳健尺寸、运动方向作为朝向、静止目标取中位数位置、重复碎片轨迹去除；单轨迹延伸；关键帧压缩（插值误差 ≤ 0.25 m / 6° 的最少关键帧） |
| `tracker3d/training.py` | 从标注生成训练样本、可学习的聚类分类器、OpenPCDet 微调、按项目保存模型、后台训练任务 |
| `openpcdet/` | OpenPCDet 微调用的数据集 / 模型配置模板和数据准备脚本 |
| `tracker3d/model.py` | 解析标注配置、下载点云、生成预测、训练入口 |
| `tracker3d/server.py` | HTTP 服务（标准库） |

关键帧插值规则与编辑器（`PointCloud/sequence.ts`）和导出（`pointcloud3d/formats.py`）完全一致，所以“模型压缩后的少量关键帧”在编辑器中插值出来就是模型逐帧的结果。

## 用标注训练 / 微调

在项目 Settings → Model 中点 **Start Training**（教师或学院管理员；学生需要“修改项目配置”授权）。
Label Studio 发出 `START_TRAINING` webhook，服务用该项目的令牌从 `/api/projects/<id>/export` 下载已标注任务，在后台训练；
旧版 Label Studio 的 `/train` 请求（标注随请求发送）同样支持，任务状态可用 `/job_status` 查询。
设置 `TRAIN_ON_ANNOTATIONS=1` 并把项目 webhook 指向本服务后，每次提交标注都会触发重新训练（训练中再次触发会排队一次）。

- **训练数据**：每个已标注的点云一份样本（ground truth 优先，否则最近更新的未跳过标注）；连续帧任务按编辑器同样的插值规则得到每一帧的框，所以只标关键帧也能产生逐帧训练数据。
- **默认（无依赖）**：学习聚类检测器的分类器。每个候选聚类按它落入的标注框取类别，落在框外的记为“背景”，在形状特征（长宽高、点数、距离、点的高度分布、是否正对传感器）上训练 k 近邻分类器；同时从标注框学习每个类别的尺寸先验，用于补全只看到一面的目标。
  效果：项目可以使用自己的标签（任意名称、任意语言，例如“小汽车/卡车/行人/骑行者”，或“公交车”“锥桶”），并学会把墙面、杆子等当作背景。
  训练会留出一部分任务（任务较少时留出部分帧）评估，`/job_status` 和 `<MODEL_DIR>/<项目>/history.jsonl` 中有精确率、召回率和各类别样本数。
- **OpenPCDet**：同时设置 `PCDET_ROOT`（OpenPCDet 源码目录）和 `PCDET_CFG` 时改为微调深度模型：
  1. 把标注导出为 OpenPCDet 自定义数据集（`points/*.npy`、`labels/*.txt`、`ImageSets/`）；
  2. 用 `openpcdet/*.tmpl` 生成数据集和模型配置——类别列表取项目标签，锚框尺寸取标注框的中位数尺寸；
  3. 运行 `openpcdet/create_infos.py` 生成 info 文件，再在 `PCDET_ROOT/tools` 中运行 `train.py`，从本项目上一次的权重（首次为 `PCDET_CKPT`）继续训练；类别不同的检测头会重新初始化，骨干网络权重保留；
  4. 训练完成后自动切换到新权重，日志在 `<MODEL_DIR>/<项目>/<版本>/train.log`。
  命令和模板可以用 `PCDET_PREPARE_CMD`、`PCDET_TRAIN_CMD`、`PCDET_DATASET_CFG_TEMPLATE`、`PCDET_MODEL_CFG_TEMPLATE` 替换（例如改用 CenterPoint），
  `FINETUNE_EPOCHS`、`FINETUNE_BATCH_SIZE`、`PCDET_ANCHOR_BOTTOM`（地面在 LiDAR 坐标中的高度，KITTI 为 -1.78）可调。
  模板基于 OpenPCDet 的 custom_dataset / PointPillars 配置编写，请对照你使用的 OpenPCDet 版本检查。
- **按项目隔离**：模型保存在 `<MODEL_DIR>/<项目 ID>/`，只用于该项目；其他项目（包括其他学院的项目）继续使用基础模型或各自训练的模型。
  下载数据时也按项目使用各自的令牌（Label Studio 在 `/setup` 中按项目下发），点云缓存按项目区分。
- 训练失败（例如还没有任何 3D 框标注）时任务状态为 failed，并保留原来的模型。

## 运行

```bash
docker build -t pointcloud-tracker ml_backends/pointcloud_tracker
docker run -p 9090:9090 -v tracker-models:/data/models \
  -e LABEL_STUDIO_URL=http://label-studio:8080 \
  -e LABEL_STUDIO_API_KEY=<有项目访问权限的账号令牌> \
  pointcloud-tracker
# 或不用 Docker：cd ml_backends/pointcloud_tracker && python _wsgi.py
```

在项目 Settings → Model → Connect Model 填 `http://<主机>:9090`，并打开 **Interactive preannotations**
（编辑器的“Track With Model”按钮只在连接了交互式 ML 后端时出现）。

| 环境变量 | 说明 |
|---|---|
| `LABEL_STUDIO_URL`、`LABEL_STUDIO_API_KEY` | 下载 `/data/upload/...` 等点云和解析 `s3://` 等存储地址；未设置时使用 Label Studio 在 `/setup` 中下发的地址和项目创建者令牌。令牌只会发给 Label Studio 本身，不会发给对象存储的预签名地址 |
| `LOCAL_ROOTS` | 直接读本地磁盘，如 `/static/=/label-studio/label_studio/core/static,/data/=/label-studio/data/media`（禁止越出根目录） |
| `PCDET_CFG`、`PCDET_CKPT`、`DEVICE`、`SCORE_THRESHOLD` | 使用 OpenPCDet 模型（同时设置 CFG 和 CKPT 才启用） |
| `LABEL_MAP` | 模型类别改名，如 `Van:Car,Bus:Truck`；模型类别与项目标签按名称（忽略大小写）对应，项目里没有的类别会被丢弃 |
| `FRAME_RATE` | 帧率（用于速度判断），默认 10 |
| `MAX_AGE`、`MIN_HITS` | 轨迹最多连续丢失帧数（默认 3）、确认所需命中次数（默认 2） |
| `KEYFRAME_POSITION_TOL` | 关键帧压缩的位置误差上限（米），默认 0.25 |
| `MODEL_DIR` | 训练得到的模型目录（Docker 镜像中为 `/data/models`，请挂载数据卷） |
| `TRAIN_ON_ANNOTATIONS` | `1` 时收到 ANNOTATION_* webhook 就重新训练 |
| `KNN_K`、`MIN_CONFIDENCE` | 学习分类器的近邻数（默认 7）和最低置信度（默认 0.5） |
| `BASIC_AUTH_USER`、`BASIC_AUTH_PASS` | 可选 Basic Auth（在 Connect Model 中填写同样的账号） |

### 使用 OpenPCDet

在带 CUDA 的 PyTorch 镜像中按 OpenPCDet 文档安装 `pcdet`，把本目录复制进去，设置
`PCDET_CFG=/models/pointpillar.yaml PCDET_CKPT=/models/pointpillar.pth`。检测器输出
`(x, y, z, dx, dy, dz, heading)` 与本平台坐标约定一致（x 前、y 左、z 上）。模型类别名来自 `cfg.CLASS_NAMES`，
必要时用 `LABEL_MAP` 对应到项目标签。

## 学院多租户

- 编辑器通过平台接口 `/api/pointcloud3d/track/` 调用模型，平台按任务找到该项目的交互式 ML 后端再转发：
  需要能看到该任务；学生还需要“标注”授权和“3D 点云标注”模块授权。
- ML 后端按项目连接，各学院可以连接各自的模型服务；服务本身不保存任何标注数据。

## 测试

```bash
cd ml_backends/pointcloud_tracker && python -m pytest -q
```

测试使用仓库自带的 8 帧合成序列（逐帧真值在 `label_studio/core/static/samples/pointcloud/sequence_0001_truth.json`）：

| 场景 | 结果 |
|---|---|
| 整段自动跟踪 | 6 个目标各一条轨迹、类别全部正确（骑行者由速度纠正）；行驶车辆、行人、骑行者、卡车位置误差 < 0.5 m；匀速目标只需 2 个关键帧；行人离开后轨迹结束 |
| 从 1 个人工关键帧延伸 | 运动目标逐帧误差 < 0.3 m；静止卡车保持人工框不动；人工设置的轨迹结束帧不会被模型越过 |
| 协议 | `/health`、`/setup`、`/predict`（批量与交互）、Basic Auth、令牌不外发、路径穿越被拒绝 |
| 训练（中文标签项目） | 训练前基础模型对中文标签没有任何输出；用 5 帧标注训练后，在另外 3 帧上召回率 ≥ 0.8、精确率 ≥ 0.6；其他项目不受影响；重启后模型仍在 |
| 训练流程 | `/train` + `/job_status`、`START_TRAINING` webhook 从（模拟的）Label Studio 导出接口下载标注、按项目令牌下载、无标注时训练失败且不替换模型 |
| OpenPCDet 微调编排 | 用替身命令验证：数据集导出（.npy 头、标签行）、配置模板填充（中文类别、锚框）、从上一次权重继续训练、切换到新权重 |

内置聚类检测器的局限：侧向停放、只看到侧面的车辆位置偏差约 0.8 m；远处稀疏目标会晚 1–2 帧出现；
目标贴得很近时可能合并。真实数据建议换成 OpenPCDet 等学习模型。
可学习的聚类分类器也受训练数据覆盖的视角限制：例如示例中侧向停放车辆在自车经过时只露出局部，
这种视角没出现在训练帧里，会被误判为骑行者——标注越多、视角越全越好。
OpenPCDet 的实际训练（GPU、模板与具体版本的兼容性）没有在本仓库的测试环境中运行过，首次使用请先用少量数据试跑。
