# 3D 点云标注模块（户外驾驶 / LiDAR）

在标注平台中新增 **3D 点云 + 3D 框（Cuboid）** 标注，用于自动驾驶等户外场景的 3D 目标检测，以及**点云语义分割**（逐点标类别）和 **3D 车道线**（3D 折线），见下文。
模块以原生标签的方式接入 Label Studio 编辑器（与 `<Video>` + `<VideoRectangle>` 的接入方式相同），
标注结果、草稿、预标注、导出、学院多租户权限都走现有流程，不需要单独的服务。

## 参考的开源项目

| 项目 | 借鉴的部分 |
|---|---|
| [SUSTechPOINTS](https://github.com/naurril/SUSTechPOINTS) | 俯视图拖框 + 自动贴合点云（去地面、贴地、取最高点、可选自动估计朝向），俯视/侧视/后视三视图精修，键盘微调 |
| [Xtreme1](https://github.com/xtreme1-io/xtreme1) | 点云 + 多相机融合视图：用内外参把 3D 框投影到相机图像上校验；框内点高亮 |
| [CVAT 3D](https://github.com/cvat-ai/cvat) | 透视视图 + 三个正交视图的界面布局；PCD / KITTI bin 数据支持 |
| [three.js PCDLoader](https://github.com/mrdoob/three.js)（MIT） | PCD `binary_compressed`（LZF、按列存储）的解析方式 |
| [OpenPCDet](https://github.com/open-mmlab/OpenPCDet)、[KITTI](https://www.cvlibs.net/datasets/kitti/) | 坐标系约定和两种导出格式 |
| [OpenLane](https://github.com/OpenDriveLab/OpenLane)、[Xtreme1](https://github.com/xtreme1-io/xtreme1)、[CVAT 3D](https://github.com/cvat-ai/cvat) | 3D 车道线：在点云上逐点点出折线、顶点贴地、按强度显示路面标线；按车道线逐帧导出的 JSON 结构 |
| [SemanticKITTI](https://semantic-kitti.org/)、[Xtreme1](https://github.com/xtreme1-io/xtreme1)、[labelCloud](https://github.com/ch-sa/labelCloud) | 语义分割：`.label` 格式和类别配色；画刷 / 套索刷点、按高度过滤、用 3D 框和地面自动填充 |

## 使用方法

### 1. 建项目

新建项目 → Labeling Setup → 左侧分类 **3D Point Cloud**，选择模板：

- **LiDAR 3D Object Detection**：纯点云 3D 框。
- **LiDAR + Camera Fusion**：点云 + 相机图像投影校验，带逐框“遮挡程度”属性。
- **LiDAR Sequence Tracking**：连续帧标注与跨帧跟踪（见下文“连续帧标注与跨帧跟踪”）。

也可以手写配置：

```xml
<View>
  <Labels name="label" toName="pcd">
    <Label value="Car" background="#FFA39E"/>
    <Label value="Pedestrian" background="#D4380D"/>
  </Labels>
  <PointCloud name="pcd" value="$pointcloud" images="$cameras" colorMode="height"/>
  <Cuboid name="box" toName="pcd"/>
</View>
```

`<PointCloud>` 属性：

| 属性 | 说明 |
|---|---|
| `value` | 点云文件 URL（`.pcd` 或 `.bin`）；或帧列表（连续帧模式） |
| `images` | 可选，任务数据中的相机列表（见下文），用于融合视图 |
| `format` | 可选，`pcd` / `bin`，URL 无法判断扩展名时使用 |
| `binDims` | `.bin` 每个点的 float32 个数，KITTI 为 4（默认），nuScenes 为 5 |
| `colorMode` | 初始着色：`height`（默认）/ `intensity` / `distance` / `rgb` |
| `pointSize` | 初始点大小（像素），默认 2 |
| `height` | 3D 视图高度（像素），默认 600 |
| `defaultHeight` | 框内没有点时新框的高度（米），默认 1.6 |
| `frameRate` | 连续帧：播放速度（帧/秒），默认 10 |
| `fixedSize` | 连续帧：修改轨迹尺寸时同步到所有关键帧（目标是刚体），默认 `true` |
| `cacheFrames` | 连续帧：内存中保留的已解码帧数，默认 30 |
| `overlayFrames` | 连续帧：初始叠加前面多少帧（0–10），默认 0 |
| `overlayFuture` | 连续帧：同时叠加后面相同数量的帧，默认 `false` |

### 2. 导入数据

- 直接上传 `.pcd` / `.bin` 文件，每个文件生成一个任务（仅点云模板）。
- 或导入 JSON（融合模板需要这样提供相机）：

```json
[{
  "data": {
    "pointcloud": "s3://bucket/lidar/000123.pcd",
    "cameras": [{
      "name": "front",
      "url": "s3://bucket/camera/000123.jpg",
      "intrinsic": [fx, 0, cx, 0, fy, cy, 0, 0, 1],
      "extrinsic": [16 个数，LiDAR→相机 4x4 行优先],
      "width": 1920, "height": 1080
    }]
  }
}]
```

`intrinsic` 可以是 3x3 K 或 3x4 P；也可以直接给 KITTI 标定字段 `P2`、`R0_rect`、`Tr_velo_to_cam`
（此时外参 = `R0_rect × Tr_velo_to_cam`）。`cameras` 也可以是 `{"front": {...}, "left": {...}}` 形式。

示例数据：`/static/samples/pointcloud/scene_000001_tasks.json`（合成场景，含 7 个预标注框，可直接导入体验）。
示例数据由 `scripts/generate_pointcloud_samples.py` 生成（纯 Python、结果固定）。

### 3. 标注操作

1. 在上方选择类别（如 Car），按 **N**（或点“Draw Box”）进入画框模式，自动切到俯视图（BEV）。
2. 在地面上拖出矩形 → 生成 3D 框，并自动贴合：底面落到地面、顶面到最高点。
3. 点击框选中，右侧出现 **俯视 / 侧视 / 后视** 三个投影视图：拖框内部移动，拖边缘改尺寸，拖俯视图的圆点旋转。
   框内点在 3D 视图中显示为白色，右上角显示框内点数。
4. 精确数值可以在右侧输入：位置、长宽高、偏航角（度）。
5. “Fit to Points”（G）把框收紧到框内点；“Fit With Rotation”（Shift+G）同时估计朝向。
6. 融合模板下，下方相机图像实时显示所有框的投影，可勾选“把点投影到相机图像”检查标定。

快捷键（焦点在 3D 区域时生效，点“Shortcuts”可查看）：

| 键 | 作用 |
|---|---|
| N / Esc | 开始 / 结束画框 |
| B | 俯视图 ↔ 3D 视图 |
| ↑ ↓ ← → | 沿框的朝向前后左右移动 0.1 m（Shift ×5） |
| R / F | 升高 / 降低 0.05 m |
| Q / E | 左转 / 右转 1°（Shift 5°） |
| T | 朝向翻转 180° |
| G / Shift+G | 贴合点云 / 贴合并估计朝向 |
| Delete、Backspace | 删除选中的框 |
| Ctrl+Z / Ctrl+Shift+Z | 撤销 / 重做（拖动过程合并为一步） |

鼠标：俯视图左键平移（画框模式下左键画框、右键平移），滚轮缩放；3D 视图左键旋转、右键平移。

## 连续帧标注与跨帧跟踪

### 数据

`<PointCloud value="$frames">` 指向一个列表时进入连续帧模式，每一项是点云 URL，或：

```json
{"frames": [
  {"pointcloud": "s3://bucket/seq01/000000.pcd", "pose": [16 个数，LiDAR→世界 4x4 行优先], "cameras": [...]},
  {"pointcloud": "s3://bucket/seq01/000001.pcd", "pose": [...]}
], "sequence": "0001"}
```

- `pose`（可选，也接受 `ego_pose`、`lidar_to_world`、3x4）：自车位姿。提供后，插值和“保持”都在世界坐标中进行——
  停着的车在自车行驶时保持不动，运动目标沿真实轨迹插值；不提供时所有帧视为同一坐标系。
- `cameras`（可选）：该帧的相机列表，格式同单帧；没有时使用 `images` 属性指向的任务级相机。
- `sequence`（可选）：导出 KITTI Tracking 时的序列名。

示例：`/static/samples/pointcloud/sequence_0001_tasks.json`（8 帧，自车沿弯道行驶，6 条轨迹只在首尾帧有关键帧，
行人在第 6 帧离开）。

### 标注方式（参考 SUSTechPOINTS、Xtreme1、CVAT 的序列标注）

- **一个框 = 一条轨迹**，区域 ID 即轨迹 ID。在某一帧画框就创建轨迹，并在该帧生成关键帧。
- 在任意帧移动/缩放/旋转框，会在该帧写入关键帧；两个关键帧之间自动插值（位置线性、朝向走最短角度）。
- 最后一个关键帧之后，目标在世界坐标中保持不动，直到“结束轨迹”。
- **结束 / 恢复轨迹（X）**：目标离开视野时在该帧结束；之后再出现时在新的帧恢复。
- **跟踪到下一帧（P）/ 自动跟踪 10 帧（Shift+P）**：按轨迹自身速度预测（有位姿时扣除自车运动），
  再在新一帧的点云中以“框中心与可见点质心的偏移不变”重新对准；尺寸、高度、朝向保持不变。
  跟丢（搜索范围内没有点）时停止，最后一步仍保留预测位置供人工修正。
- 底部时间轴：每条轨迹一行，色条为存在区间，菱形为关键帧（红色为结束），竖线为当前帧；点击跳帧。
- 尺寸默认按轨迹统一（`fixedSize`），“Fit to Points”在多关键帧轨迹上只调整位置和高度。

### 多帧点云叠加

工具栏“Overlay”选择叠加前面 1/2/3/5/10 帧（勾选“Later frames too”时前后都叠加），快捷键 **O** 开关。

- 相邻帧的点用位姿变换到当前帧坐标（`inverse(pose_当前) × pose_相邻`），静止物体（路面、建筑、停放车辆）对齐、点更密；
  运动目标留下拖影，可以直观看出运动方向和速度。没有位姿时原样叠加，状态栏会提示未做运动补偿。
- 叠加点单独一层、调暗显示（越早的帧越暗），画在当前帧点的下面；俯视/侧视/后视投影中也以深灰色显示，
  方便给远处稀疏的静止目标确定尺寸。
- “框内点数”、“Fit to Points”和跨帧跟踪只使用当前帧的点，不受拖影影响。
- 叠加帧总点数超过 150 万时按帧均匀抽稀；被叠加的帧会保留在缓存中（缓存上限自动不小于 2×叠加帧数+3）。

示例序列中，第 0 帧停放卡车上的 41 个点变换到第 7 帧后全部落在第 7 帧的卡车框内；行驶车辆的点则全部落在框外（形成拖影）。

新增快捷键：

| 键 | 作用 |
|---|---|
| , / . | 上一帧 / 下一帧（Shift 跳 10 帧） |
| Space | 播放 / 暂停 |
| P / Shift+P | 跟踪到下一帧 / 自动跟踪 10 帧 |
| X | 在本帧结束轨迹 / 恢复轨迹 |
| K | 删除本帧的关键帧 |
| O | 多帧叠加开 / 关 |
| M | 用连接的模型跟踪选中轨迹（需要交互式 ML 后端） |
| Delete | 删除整条轨迹 |

### 基于模型的跟踪

参考 ML 后端在仓库的 `ml_backends/pointcloud_tracker/`（标准 Label Studio ML 后端协议，详见其 README）：

- **整段自动预标注**：项目 Settings → Model 连接该服务后，在 Data Manager 中对任务执行 “Retrieve Predictions”。
  模型逐帧检测（内置聚类检测器，或接入 OpenPCDet 模型），在世界坐标中做多目标跟踪（卡尔曼滤波 + 关联），
  再把每条轨迹压缩成少量关键帧，作为预标注返回；标注员在编辑器中只需修正。
- **交互式跟踪（M / “Track With Model”）**：连接时勾选 *Interactive preannotations* 后，轨迹面板出现该按钮。
  对选中的轨迹，模型从人工关键帧出发向前、向后跟踪到整段序列，并在人工关键帧之间补上插值明显不准的帧；
  人工关键帧原样保留，人工设置的轨迹结束帧不会被越过，静止目标保持人工框不动。结果一次写回，Ctrl+Z 可整体撤销。
- 编辑器通过平台接口 `GET/POST /api/pointcloud3d/track/` 调用模型（`pointcloud3d/api.py`），平台按任务找到项目的
  交互式 ML 后端并复用 Label Studio 的交互预标注转发；返回的关键帧会被校验（越界帧丢弃、格式错误返回 502）。
  学院模式下：GET 需要能查看任务，POST 需要“标注”授权，且学生必须有“3D 点云标注”模块授权。
- 本地的 P / Shift+P（恒速预测 + 点云对准）仍可在没有模型服务时使用。
- **用标注训练**：项目 Settings → Model → Start Training 后，模型服务用本项目的标注训练（默认学习聚类检测器的分类器和尺寸先验，
  可使用项目自己的任意标签；配置 OpenPCDet 时改为微调深度模型），之后本项目的预测和跟踪自动使用新模型。
  模型按项目保存、互不共享，详见 `ml_backends/pointcloud_tracker/README.md`。

### 逐帧属性与按轨迹批量修改

遮挡、截断、是否在动这类会随帧变化的属性，在 `<Cuboid>` 里用 `<FrameAttribute>` 声明（“LiDAR Sequence Tracking”模板已带）：

```xml
<Cuboid name="box" toName="pcd">
  <FrameAttribute value="occluded" title="遮挡" choices="0,1,2,3" default="0"/>
  <FrameAttribute value="truncated" title="截断" choices="0,0.5,1" default="0"/>
  <FrameAttribute value="moving" title="运动中"/>   <!-- 不写 choices 即为“是/否” -->
</Cuboid>
```

- **单帧修改**：选中轨迹后，右侧面板的“Attributes in frame N”显示本帧的值；修改后从本帧一直生效到该属性的下一个变化点（同 CVAT）。
- **批量修改**：点时间轴上的 **Batch Attributes**：
  1. 选范围：起止帧可直接输入，也可用 Start Here / End Here（当前帧）、Track Lifespan（所选轨迹的出现范围）、All Frames；范围会在时间轴上高亮。
  2. 选轨迹：当前选中的轨迹（在时间轴上 Ctrl/⌘ 点击轨迹名可多选）、与选中轨迹同类别的所有轨迹，或全部轨迹。
  3. 选属性和值，点 Apply。范围内的帧被设为该值，范围之后的帧保持原值；一次操作是一步撤销（Ctrl+Z）。
- 选中的轨迹在时间轴上多出每个属性一条色带，按值着色，悬停显示值和帧范围。
- 单帧任务也可以用 `<FrameAttribute>`，相当于逐框属性。
- 不随帧变化的属性（车辆颜色等）仍然用 `perRegion` 的 `<Choices>`；类别直接改轨迹的标签。

### 结果格式

```json
{
  "id": "track0", "from_name": "box", "to_name": "pcd", "type": "cuboid",
  "value": {
    "sequence": [
      {"frame": 0, "enabled": true,  "position": {...}, "dimensions": {...}, "rotation": {...}},
      {"frame": 7, "enabled": true,  "position": {...}, "dimensions": {...}, "rotation": {...}},
      {"frame": 9, "enabled": false, "position": {...}, "dimensions": {...}, "rotation": {...}}
    ],
    "framesCount": 12,
    "frameAttributes": {
      "occluded": [{"frame": 0, "value": "0"}, {"frame": 4, "value": "2"}, {"frame": 8, "value": "0"}]
    },
    "labels": ["Car"]
  }
}
```

`frameAttributes` 只记录变化点：每个值一直生效到下一个变化点，第一个变化点之前取配置中的 `default`；从未设置过的属性不写入。

`frame` 是帧列表的下标（从 0 开始）；每个关键帧的坐标都在**该帧自己的** LiDAR 坐标系中；
`enabled: false` 表示从该帧起目标不存在（省略 `enabled` 视为 true，方便 ML 后端只返回关键帧）。
前端（`sequence.ts`）和导出（`formats.py`）使用同一套插值规则，并用示例序列的逐帧真值做了对照测试。

### 4. 结果格式（单帧）

坐标系与 KITTI velodyne / OpenPCDet / nuScenes LiDAR 一致：x 向前、y 向左、z 向上，单位米；
`rotation.z` 为绕 z 轴的偏航角（弧度，从 +x 逆时针），`length` 沿车头方向。

```json
{
  "id": "c1", "from_name": "box", "to_name": "pcd", "type": "cuboid",
  "value": {
    "position":   {"x": 12.0, "y": -2.2, "z": -0.95},
    "dimensions": {"length": 4.4, "width": 1.85, "height": 1.55},
    "rotation":   {"x": 0, "y": 0, "z": 0.0},
    "labels": ["Car"]
  }
}
```

ML 后端返回同样结构的 prediction 即可作为预标注。

### 5. 导出

在 Export 对话框中（或 `GET /api/projects/<id>/export?exportType=...`）新增以下格式。3D 框格式仅对含 `<Cuboid>` 的项目可选，
`SEMANTIC_KITTI` 仅对含 `<PointLabels>` 的项目可选，`LANE3D_JSON` 仅对含 `<Polyline3D>` 的项目可选：

| 格式 | 内容 |
|---|---|
| `OPENPCDET_3D` | `labels/<帧名>.txt`，每行 `x y z dx dy dz heading 类别`（LiDAR 坐标），可直接用于 OpenPCDet 自定义数据集 |
| `KITTI_3D` | `label_2/<帧名>.txt`（相机坐标 15 列，含 2D 框、alpha、遮挡）+ `calib/<帧名>.txt`；使用任务中第一个有标定的相机，没有标定的任务被跳过 |
| `SEMANTIC_KITTI` | 语义分割：每个已标注的帧一个 `labels/<帧名>.label`，每个点一个小端 uint32（低 16 位为类别 ID，0 为未标注，点序与点云文件相同），另附 `classes.json`（ID → 类别，ID 按配置中的顺序）；未标注的帧记录在 `export_report.json` 中 |
| `LANE3D_JSON` | 3D 车道线：每个点云一个 `lanes/<帧名>.json`，内容为 `{"lanes": [{"id", "track_id", "category", "attributes", "points": [[x, y, z], ...]}]}`，坐标是该帧的 LiDAR 坐标；连续帧中一条线会经位姿换算写到每一帧，`track_id` 跨帧不变 |
| `KITTI_TRACKING` | 每个任务一个序列：`label_02/<序列>.txt`（`frame track_id` + KITTI 15 列，插值后逐帧输出）、`calib/<序列>.txt`、有位姿时 `poses/<序列>.txt`；单帧任务视为 1 帧序列 |

连续帧任务导出 `OPENPCDET_3D` / `KITTI_3D` 时，每一帧（按插值结果）输出一个标签文件，帧名取各帧点云文件名；
`OPENPCDET_3D` 另附 `tracks.json`，给出每个标签行对应的轨迹 ID。
有 `<FrameAttribute>` 或逐框 Choices 时，`OPENPCDET_3D` 另附 `attributes.json`（帧名 → 与标签行同序的每个框在该帧的属性值）。

两种格式都会附带 `ImageSets/train.txt`、`mapping.json`（帧名 → 任务、标注、点云 URL）和 `export_report.json`（跳过的任务及原因）。
帧名取点云文件名（重名时加任务 ID）。每个任务只导出一份标注：优先 ground truth，否则最近更新且未跳过的那份。
KITTI 的遮挡列来自名字包含 `occlu` 的属性（取值开头的数字，或 visible / partly / largely / unknown），截断列来自名字包含 `truncat` 的属性（0–1 的数）；
属性可以是逐帧的 `<FrameAttribute>`（按该帧的值输出），也可以是逐框的 Choices。

## 点云语义分割

给点云中的**每个点**标一个类别（道路、人行道、建筑、植被、车辆……），用于训练 RandLA-Net、Cylinder3D 等分割模型。
模板：“LiDAR Semantic Segmentation”。配置：

```xml
<PointCloud name="pcd" value="$pointcloud"/>
<PointLabels name="seg" toName="pcd">
  <Label value="Road" background="#FF00FF"/>
  <Label value="Car" background="#6496F5"/>
</PointLabels>
```

可以和 `<Cuboid>` 放在同一个项目里（同时标框和分割）；单帧和连续帧都支持。

### 标注方式

1. 在类别列表里点选一个类别（或按它的数字快捷键），再选工具：
   - **画刷（U）**：按住左键刷过的点标成该类别；`[` / `]` 调整画刷大小。
   - **套索（L）**：圈出一块区域，松开后区域内的点全部标成该类别。
   - 两种工具都在俯视图和 3D 视图中可用：右键拖动平移、滚轮缩放、3D 视图中按住中键旋转。按住 **Shift** 拖动是擦除，也可以勾选“Eraser”。
2. 辅助选项：
   - **Only unlabeled points**：只改还没标过的点，不会覆盖已有标注。
   - **Height filter**：只改离地高度在指定范围内的点，例如俯视图中圈一片区域时只标车身、不标地面。
3. 自动填充（结果都可以再用画刷修改）：
   - **Label Ground**：先选中地面类别（如 Road），再按局部最低点识别本帧的地面点。示例场景上精确率 0.98、召回率 1.0。
   - **Fill From Boxes**：项目里有 3D 框时，框内的点（不含框底 10 cm）标成与框标签同名的类别。
   - **Copy From Previous / Next Frame**（连续帧）：用自车位姿把相邻帧的标注搬到本帧。示例序列上相隔 3 帧时，静止物体约 97% 正确；移动目标需要人工检查，或者先修正 3D 框再点“Fill From Boxes”。
   - **Clear Frame**：清空本帧。
4. 每一笔、每一次自动填充都是一步撤销（Ctrl+Z）。右侧面板显示本帧已标点数和各类别点数；**V** 键显示 / 隐藏分割颜色。

已标注的点显示类别颜色，未标注的点变暗。

### 结果格式

每个点云只有一条结果，包含所有帧：

```json
{
  "id": "seg1", "from_name": "seg", "to_name": "pcd", "type": "pointlabels",
  "value": {
    "classes": ["Road", "Car"],
    "frames": [{"frame": 0, "count": 27430, "rle": "AQMCAgAB..."}],
    "pointlabels": ["Road", "Car"]
  }
}
```

- `frames` 中每帧按点在文件中的顺序存一串类别码：0 表示未标注，k 表示 `classes` 中的第 k 个类别。
- 编码方式：先做游程编码（类别码，连续个数），再用 LEB128 变长整数写成字节，最后转 base64。示例单帧 2.7 万个点编码后只有约 0.7 KB。
- 编辑器（`segmentation.ts`）和导出（`formats.py` 的 `decode_point_labels`）用同一个测试向量验证编码一致。
- `count` 必须等于该帧的点数。点云文件被替换导致点数不同时，编辑器不显示这帧的旧标注并给出提示；导出时跳过这帧，并记录在报告中。
- `pointlabels` 是用到的类别，用于标签统计和“删除仍在使用的标签”校验。
- ML 后端返回同样结构的 prediction 即可作为预标注，示例见 `core/static/samples/pointcloud/scene_000001_segmentation_tasks.json`。

## 3D 车道线（3D 折线）

在点云上画车道线、路沿、停止线等 3D 折线，用于 3D 车道线检测和高精地图。模板：“LiDAR Lane Annotation”。配置：

```xml
<Labels name="lane_type" toName="pcd">
  <Label value="Dashed"/><Label value="Solid"/><Label value="Road Edge"/>
</Labels>
<PointCloud name="pcd" value="$pointcloud" colorMode="intensity"/>
<Polyline3D name="lane" toName="pcd"/>
<Choices name="color" toName="pcd" perRegion="true"><Choice value="White"/><Choice value="Yellow"/></Choices>
```

可以和 `<Cuboid>`、`<PointLabels>` 放在同一个项目里。路面标线的反射强度高，用 `colorMode="intensity"` 显示最清楚。

### 标注方式

1. 选好类型（如 Dashed），按 **J**（或工具栏 “Lane Line”），在俯视图或 3D 视图中沿标线逐点单击。
   每个点的高度自动取该位置的路面高度（附近点云的低分位数，会排除车辆等高出路面的点）。
2. 双击或按 Enter 结束这条线；Backspace 删除最后一个点，Esc 放弃这条线。结束后仍在画线工具中，可以接着画下一条。
3. 修改选中的线（选择工具下）：
   - 拖动顶点移动，高度跟着路面走。
   - 拖动线段中点的小圆点即在该处插入一个顶点。
   - Alt+单击顶点，或选中顶点后按 Delete，删除该顶点；没有选中顶点时 Delete 删除整条线。
   - 右侧面板可以直接修改选中顶点的 x / y / z。
4. 右侧面板的辅助按钮：
   - **Snap to Markings**：利用点云强度（标线比沥青亮得多），把粗略画出的线横向移到标线上。虚线间隙处的点跟随相邻点平移，附近没有标线时不移动。
     在示例场景上，从偏离 0.2–0.55 m 的位置都能落到标线上 5 cm 以内（标线宽 16 cm）。
   - **Snap to Ground**：所有顶点重新贴到路面上。
   - **Reverse Direction**：反转线的方向，约定为沿行驶方向。
5. 车道线颜色等属性用逐线的 `<Choices perRegion>`，选中线后在区域面板中选择。

### 连续帧

车道线在世界中是静止的，所以一条线只需在某一帧画一次：结果记录画线时的帧（`frame`），坐标存在该帧的 LiDAR 坐标系中，
其他帧借助自车位姿换算后显示，编辑时也会换算回来。没有位姿时所有帧共用同一坐标系。

### 结果格式

```json
{
  "id": "lane0", "from_name": "lane", "to_name": "pcd", "type": "polyline3d",
  "value": {"points": [[3.0, 1.75, -1.73], [13.0, 1.75, -1.73]], "frame": 0, "labels": ["Dashed"]}
}
```

单帧任务没有 `frame` 字段。预标注示例见 `core/static/samples/pointcloud/scene_000001_lanes_tasks.json`。

## 与学院多租户的集成

- **标注模块权限**：新增模块“3D 点云标注”（`pointcloud`）。教师在“学生功能授权”中可以单独关闭某个学生的 3D 标注；
  关闭后学生看不到点云和 3D 框控件，服务器拒绝提交 `cuboid` 结果（同其他模块）。融合模板中的相机图像属于点云模块。
- **学生建项目授权**：模板分类中新增“3D Point Cloud（3D 点云/自动驾驶）”，可单独授权。
- **车道线**：`<Polyline3D>` 同样属于“3D 点云标注”模块；线的类型参与标签统计和“删除仍在使用的标签”校验。
- **语义分割**：`<PointLabels>` 也属于“3D 点云标注”模块，关闭该模块后服务器拒绝提交 `pointlabels` 结果。
- **导出**：3D 格式走原有导出接口，“导出数据”（export_data）授权照常生效，跨学院访问照常拒绝。

## 代码位置

| 位置 | 内容 |
|---|---|
| `web/libs/editor/src/tags/object/PointCloud/` | `<PointCloud>` 标签：模型、three.js 视图、三视图、相机融合、PCD/BIN 解析、几何计算；`sequence.ts`（关键帧插值、位姿、跟踪器）、`TrackTimeline.jsx`（帧控制与轨迹时间轴）、`attributes.ts` / `AttributePanels.jsx`（逐帧属性与批量修改） |
| `web/libs/editor/src/tags/control/Cuboid.js`、`FrameAttribute.js` | `<Cuboid>` 控件和 `<FrameAttribute>` 属性声明 |
| `web/libs/editor/src/tags/control/Polyline3D.js`、`regions/Polyline3DRegion.js`、`tags/object/PointCloud/lanes.ts` | `<Polyline3D>` 控件、3D 折线区域；贴地、拾取、按强度吸附到标线 |
| `web/libs/editor/src/tags/control/PointLabels.js`、`regions/PointSegmentationRegion.js` | `<PointLabels>` 控件和分割结果（每个点云一个区域） |
| `web/libs/editor/src/tags/object/PointCloud/segmentation.ts`、`SegmentationPanel.jsx` | 逐点标签的编码、画刷 / 套索选点、地面识别、跨帧传递；分割工具面板 |
| `web/libs/editor/src/regions/CuboidRegion.js` | 3D 框区域（序列化、移动/旋转/贴合） |
| `label_studio/pointcloud3d/formats.py` | 导出格式（纯标准库，可单独测试） |
| `label_studio/pointcloud3d/converter.py` | 在 SDK Converter 上增加 3D 格式 |
| `label_studio/pointcloud3d/api.py`、`urls.py` | 模型跟踪转发接口 |
| `ml_backends/pointcloud_tracker/` | 参考 ML 后端：检测（聚类 / OpenPCDet）、多目标跟踪、单轨迹延伸、关键帧压缩 |
| `label_studio/annotation_templates/3d-point-cloud/` | 五个模板（检测、相机融合、连续帧跟踪、语义分割、车道线） |
| `scripts/generate_pointcloud_samples.py` | 生成示例点云（单帧和 8 帧序列及逐帧真值）、分割与车道线预标注、相机图和模板缩略图 |

对上游代码的改动（均标注 `POINTCLOUD-3D` 或为注册表中的一行）：

| 文件 | 改动 |
|---|---|
| `web/package.json`、`web/libs/editor/package.json`、`web/yarn.lock` | 新增依赖 `three@0.170.0` |
| `web/libs/editor/jest.config.js` | 允许 Jest 转换 three 的 ES 模块 |
| `editor/src/tags/object/index.js`、`tags/control/index.js`（含 `FrameAttribute`）、`regions/index.js`、`regions/Area.js`、`regions/Result.js` | 注册标签、区域和结果类型 `cuboid`、`pointlabels`、`polyline3d` |
| `editor/src/tags/visual/View.jsx`、`Collapse.jsx`、`tags/object/PagedView.jsx` | 允许新标签作为子元素 |
| `editor/src/components/Node/Node.tsx` | 大纲中显示 3D 框（尺寸、点数）、分割结果（帧数、类别）和 3D 折线（点数、长度） |
| `editor/src/tags/control/Label.jsx` | `<Label>` 可以放在 `<PointLabels>` 中 |
| `web/apps/labelstudio/.../Import/Import.jsx`、`core/settings/base.py` | 允许上传 `.pcd`、`.bin` |
| `projects/models.py` | `cuboid`、`polyline3d` 结果中合并的 labels 参与标签统计和“删除仍在使用的标签”校验 |
| `tasks/validation.py` | `PointCloud` 的任务数据可以是字符串或帧列表 |
| `core/urls.py` | 挂载 `pointcloud3d.urls`（模型跟踪转发接口） |
| `data_export/models.py`、`data_export/mixins.py` | 使用 `PointCloudConverter` |
| `school_tenancy/models.py`、`template_groups.py` | 新增标注模块和模板分类 |

## 在 Linux 机器上启动（局域网测试）

```bash
git clone -b dev_3d https://github.com/weichampion/label_studio_edu.git && cd label_studio_edu
SEED=1 bash deploy/school/local/run-lan.sh      # 第一次：装依赖、构建前端、建库、创建演示账号
bash deploy/school/local/run-lan.sh             # 之后再次启动；拉取新代码后加 REBUILD=1 重新构建前端
```

- 使用 SQLite 和本地文件，只用于测试，不能用于生产环境。
- 需要 Python 3.10–3.12、Node.js 18 以上，构建前端约需 8 GB 内存。
- 局域网内用 `http://<本机 IP>:8080/` 访问；演示账号的密码是 `Demo@2026`。
- 3D 模型服务会同时在 `:9090` 启动（加 `TRACKER=0` 可不启动），在项目 Settings → Model 中连接 `http://127.0.0.1:9090`。

## 测试

```powershell
# 导出格式与连续帧插值（不需要数据库）
..\..\.venv\Scripts\python -m pytest pointcloud3d/tests -q
# 参考 ML 后端（检测、跟踪、协议）
cd ..\ml_backends\pointcloud_tracker; python -m pytest -q; cd ..\..\label_studio
# 学校版集成：模块权限、标签统计、导出授权、模板分类、模型跟踪接口
..\..\.venv\Scripts\python -m pytest school_tenancy/tests/test_pointcloud.py --ds=core.settings.school_local --reuse-db -q
# 前端几何与文件解析
cd web
node node_modules\jest\bin\jest.js --config libs\editor\jest.config.js --runInBand libs/editor/src/tags/object/PointCloud
```

新增依赖后需要先 `yarn install`（或 README 中的 npm 安装命令），再重新构建前端。

## 尚未实现（后续）

- OpenPCDet 微调尚未在真实 GPU 环境中验证。
- 实例分割 / 全景分割（`.label` 的高 16 位实例 ID）；分割模型的预标注与训练；车道线的自动检测（目前只有吸附到标线）。
- 在相机图像上反向调整 3D 框；相机畸变参数。
- 超大点云（> 数百万点）的分块 / LOD 加载。
