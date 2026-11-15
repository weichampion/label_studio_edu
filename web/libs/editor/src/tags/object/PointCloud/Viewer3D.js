import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { pointIndicesInBox } from "./geometry";
import { indicesInCircle, indicesInPolygon, projectToScreen } from "./segmentation";

/**
 * Imperative three.js viewer used by <PointCloud>. React/MobX only feed it data; it never touches the store.
 *
 * Views:
 *  - "perspective": free orbit camera (z up)
 *  - "bev": orthographic bird's-eye view, +x (forward) pointing up the screen. Boxes are drawn here.
 *
 * Tools: "select", "draw" (boxes, BEV only), "brush" and "lasso" (point labels, both views; the left button
 * paints, the right button pans, the wheel zooms, the middle button orbits the 3D view).
 */

// Canvas colors cannot use CSS variables; point clouds are conventionally shown on a dark background.
const BACKGROUND = 0x14171c;
const GRID_COLOR = 0x3a4049;
const HIGHLIGHT = new THREE.Color(1, 1, 1);
const DRAG_THRESHOLD_PX = 4;
const SVG_NS = "http://www.w3.org/2000/svg";
const PAINT_TOOLS = ["brush", "lasso"];

/** Point colors with the segmentation on top: labeled points take their class color, the rest is dimmed. */
export function segmentedColors(raw, labels, palette) {
  const out = raw.slice();

  if (!labels || labels.length * 3 !== raw.length) return out;
  let any = false;

  for (let i = 0; i < labels.length; i++) {
    if (labels[i]) {
      any = true;
      break;
    }
  }
  if (!any) return out;
  for (let i = 0; i < labels.length; i++) {
    const rgb = labels[i] ? palette[labels[i]] : null;

    if (rgb) {
      out[i * 3] = rgb[0];
      out[i * 3 + 1] = rgb[1];
      out[i * 3 + 2] = rgb[2];
    } else {
      out[i * 3] *= 0.55;
      out[i * 3 + 1] *= 0.55;
      out[i * 3 + 2] *= 0.55;
    }
  }
  return out;
}

/** Turbo colormap (Google, Apache-2.0) polynomial approximation. t in [0, 1]. */
export function turbo(t) {
  const x = Math.min(1, Math.max(0, t));
  const r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))));
  const g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))));
  const b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))));

  return [Math.min(1, Math.max(0, r)), Math.min(1, Math.max(0, g)), Math.min(1, Math.max(0, b))];
}

export function computeColors(cloud, mode, groundZ = 0) {
  const { count, positions, intensity, colors } = cloud;
  const out = new Float32Array(count * 3);

  if (mode === "rgb" && colors) {
    out.set(colors.subarray(0, count * 3));
    return out;
  }
  let maxIntensity = 1;

  if (mode === "intensity" && intensity) {
    // robust max: 99th percentile of a sample (KITTI is 0..1, many drivers report 0..255)
    const sample = [];
    const step = Math.max(1, Math.floor(count / 5000));

    for (let i = 0; i < count; i += step) sample.push(intensity[i]);
    sample.sort((a, b) => a - b);
    maxIntensity = sample[Math.floor(sample.length * 0.99)] || 1;
  }
  for (let i = 0; i < count; i++) {
    let t;

    if (mode === "intensity" && intensity) {
      t = 0.15 + 0.85 * Math.min(1, intensity[i] / maxIntensity);
    } else if (mode === "distance") {
      const x = positions[i * 3];
      const y = positions[i * 3 + 1];

      t = Math.sqrt(x * x + y * y) / 60;
    } else {
      t = (positions[i * 3 + 2] - (groundZ - 0.3)) / 3.5;
    }
    const [r, g, b] = turbo(t);

    out[i * 3] = r;
    out[i * 3 + 1] = g;
    out[i * 3 + 2] = b;
  }
  return out;
}

/** Overlay colours: height colormap, dimmed, older (or later) scans dimmer than adjacent ones. */
export function overlayColors(overlay, groundZ = 0) {
  const { positions, ages, count } = overlay;
  const out = new Float32Array(count * 3);
  let maxAge = 1;

  for (let i = 0; i < count; i++) maxAge = Math.max(maxAge, Math.abs(ages[i]));
  for (let i = 0; i < count; i++) {
    const [r, g, b] = turbo((positions[i * 3 + 2] - (groundZ - 0.3)) / 3.5);
    const fade = 0.25 + 0.3 * (1 - (Math.abs(ages[i]) - 1) / Math.max(1, maxAge - 1));

    out[i * 3] = r * fade;
    out[i * 3 + 1] = g * fade;
    out[i * 3 + 2] = b * fade;
  }
  return out;
}

function unitBoxEdges() {
  // unit cube centred at origin + a heading line from the centre to the front face
  const c = [
    [0.5, 0.5, -0.5],
    [0.5, -0.5, -0.5],
    [-0.5, -0.5, -0.5],
    [-0.5, 0.5, -0.5],
    [0.5, 0.5, 0.5],
    [0.5, -0.5, 0.5],
    [-0.5, -0.5, 0.5],
    [-0.5, 0.5, 0.5],
  ];
  const edges = [
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 0],
    [4, 5],
    [5, 6],
    [6, 7],
    [7, 4],
    [0, 4],
    [1, 5],
    [2, 6],
    [3, 7],
    // front face cross, marks the heading
    [0, 5],
    [1, 4],
  ];
  const vertices = [];

  for (const [a, b] of edges) vertices.push(...c[a], ...c[b]);
  // heading arrow on the top face
  vertices.push(0, 0, 0.5, 0.5, 0, 0.5);
  const geometry = new THREE.BufferGeometry();

  geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  return geometry;
}

export class Viewer3D {
  constructor(container, callbacks = {}) {
    this.container = container;
    this.callbacks = callbacks;
    this.viewMode = "bev";
    this.tool = "select";
    this.groundZ = 0;
    this.boxes = new Map();
    this.cloud = null;
    // colors from the color mode; baseColors adds the segmentation on top
    this.rawColors = null;
    this.baseColors = null;
    this.highlighted = [];
    this.segLabels = null;
    this.segPalette = [];
    this.segVisible = true;
    this.brushSize = 24;
    this.renderRequested = false;
    this.disposed = false;

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 600;

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(width, height);
    this.renderer.domElement.setAttribute("aria-label", "3D point cloud view");
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(BACKGROUND);

    this.perspective = new THREE.PerspectiveCamera(60, width / height, 0.1, 2000);
    this.perspective.up.set(0, 0, 1);
    this.perspective.position.set(-18, 0, 14);

    this.bevHalfHeight = 30;
    this.bev = new THREE.OrthographicCamera(-1, 1, 1, -1, -1000, 1000);
    // +x (forward) up the screen, +y (left) to the left
    this.bev.up.set(1, 0, 0);
    this.bev.position.set(0, 0, 200);

    this.perspectiveControls = new OrbitControls(this.perspective, this.renderer.domElement);
    this.perspectiveControls.target.set(10, 0, 0);
    this.perspectiveControls.screenSpacePanning = false;
    this.perspectiveControls.maxPolarAngle = Math.PI * 0.495;

    this.bevControls = new OrbitControls(this.bev, this.renderer.domElement);
    this.bevControls.enableRotate = false;
    this.bevControls.screenSpacePanning = true;
    this.bevControls.target.set(10, 0, 0);
    this.bev.position.set(10, 0, 200);
    this.bevControls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };

    for (const controls of [this.perspectiveControls, this.bevControls]) {
      controls.addEventListener("change", () => this.requestRender());
      controls.update();
    }

    this.grid = new THREE.GridHelper(200, 20, GRID_COLOR, GRID_COLOR);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.35;
    this.scene.add(this.grid);
    this.scene.add(new THREE.AxesHelper(2));

    this.pointsMaterial = new THREE.PointsMaterial({ size: 2, vertexColors: true, sizeAttenuation: false });
    this.points = null;
    this.overlayMaterial = new THREE.PointsMaterial({
      size: 1.5,
      vertexColors: true,
      sizeAttenuation: false,
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
    });
    this.overlayPoints = null;

    this.unitEdges = unitBoxEdges();
    this.unitBox = new THREE.BoxGeometry(1, 1, 1);

    // footprint preview while drawing
    const previewGeometry = new THREE.BufferGeometry();

    previewGeometry.setAttribute("position", new THREE.Float32BufferAttribute(new Array(15).fill(0), 3));
    this.preview = new THREE.Line(previewGeometry, new THREE.LineBasicMaterial({ color: 0xffffff }));
    this.preview.visible = false;
    this.preview.renderOrder = 10;
    this.scene.add(this.preview);

    this.raycaster = new THREE.Raycaster();
    this.pointer = null;

    // brush cursor and lasso path, drawn in screen space over the canvas
    this.svg = document.createElementNS(SVG_NS, "svg");
    Object.assign(this.svg.style, { position: "absolute", inset: "0", width: "100%", height: "100%", pointerEvents: "none" });
    this.svg.setAttribute("aria-hidden", "true");
    this.brushCircle = document.createElementNS(SVG_NS, "circle");
    this.brushCircle.setAttribute("fill", "rgba(255,255,255,0.12)");
    this.brushCircle.setAttribute("stroke", "#ffffff");
    this.brushCircle.setAttribute("stroke-width", "1.5");
    this.brushCircle.style.display = "none";
    this.lassoPath = document.createElementNS(SVG_NS, "polyline");
    this.lassoPath.setAttribute("fill", "rgba(255,255,255,0.12)");
    this.lassoPath.setAttribute("stroke", "#ffffff");
    this.lassoPath.setAttribute("stroke-width", "1.5");
    this.lassoPath.setAttribute("stroke-dasharray", "4 3");
    this.svg.append(this.brushCircle, this.lassoPath);
    container.appendChild(this.svg);

    this.onPointerDown = this.onPointerDown.bind(this);
    this.onPointerMove = this.onPointerMove.bind(this);
    this.onPointerUp = this.onPointerUp.bind(this);
    // capture phase so drawing / box dragging can disable OrbitControls before it reacts
    this.renderer.domElement.addEventListener("pointerdown", this.onPointerDown, true);
    window.addEventListener("pointermove", this.onPointerMove);
    window.addEventListener("pointerup", this.onPointerUp);
    this.renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
    this.onHover = this.onHover.bind(this);
    this.onLeave = this.onLeave.bind(this);
    this.renderer.domElement.addEventListener("pointermove", this.onHover);
    this.renderer.domElement.addEventListener("pointerleave", this.onLeave);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.setViewMode("bev");
  }

  get camera() {
    return this.viewMode === "bev" ? this.bev : this.perspective;
  }

  get controls() {
    return this.viewMode === "bev" ? this.bevControls : this.perspectiveControls;
  }

  resize() {
    if (this.disposed) return;
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    const aspect = width / height;

    this.renderer.setSize(width, height);
    this.perspective.aspect = aspect;
    this.perspective.updateProjectionMatrix();
    this.bev.left = -this.bevHalfHeight * aspect;
    this.bev.right = this.bevHalfHeight * aspect;
    this.bev.top = this.bevHalfHeight;
    this.bev.bottom = -this.bevHalfHeight;
    this.bev.updateProjectionMatrix();
    this.requestRender();
  }

  requestRender() {
    if (this.renderRequested || this.disposed) return;
    this.renderRequested = true;
    requestAnimationFrame(() => {
      this.renderRequested = false;
      if (this.disposed) return;
      this.renderer.render(this.scene, this.camera);
    });
  }

  setViewMode(mode) {
    this.viewMode = mode;
    this.perspectiveControls.enabled = mode !== "bev";
    this.bevControls.enabled = mode === "bev";
    this.applyToolButtons();
    this.requestRender();
  }

  setTool(tool) {
    this.tool = tool;
    this.applyToolButtons();
    this.renderer.domElement.style.cursor =
      tool === "draw" || tool === "lasso" ? "crosshair" : tool === "brush" ? "none" : "default";
    if (tool !== "brush") this.brushCircle.style.display = "none";
  }

  applyToolButtons() {
    // in draw / paint modes the left button draws; panning stays on the right button and zoom on the wheel
    const painting = PAINT_TOOLS.includes(this.tool);

    this.bevControls.mouseButtons = {
      LEFT: this.tool === "draw" || painting ? -1 : THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    this.perspectiveControls.mouseButtons = painting
      ? { LEFT: -1, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: THREE.MOUSE.PAN }
      : { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
  }

  setBrushSize(px) {
    this.brushSize = px;
    this.brushCircle.setAttribute("r", String(px));
  }

  /** Per-point class codes of the scan on screen (null for none) and the color of each code. */
  setSegmentation(labels, palette, visible = true) {
    this.segLabels = labels;
    this.segVisible = visible;
    this.segPalette = (palette ?? []).map((style) => {
      if (!style) return null;
      const color = new THREE.Color();

      try {
        color.setStyle(style);
      } catch {
        color.set(0x9e9e9e);
      }
      return [color.r, color.g, color.b];
    });
    this.refreshColors();
  }

  composeColors() {
    if (!this.rawColors) return null;
    return segmentedColors(this.rawColors, this.segVisible ? this.segLabels : null, this.segPalette);
  }

  /** Re-apply segmentation and the box highlight to the point colors. */
  refreshColors() {
    if (!this.points || !this.rawColors) return;
    this.baseColors = this.composeColors();
    const attr = this.points.geometry.getAttribute("color");

    attr.array.set(this.baseColors);
    const highlighted = this.highlighted;

    for (const i of highlighted) {
      attr.array[i * 3] = HIGHLIGHT.r;
      attr.array[i * 3 + 1] = HIGHLIGHT.g;
      attr.array[i * 3 + 2] = HIGHLIGHT.b;
    }
    attr.needsUpdate = true;
    this.requestRender();
  }

  setGroundZ(z) {
    this.groundZ = Number.isFinite(z) ? z : 0;
    this.grid.position.z = this.groundZ;
    this.requestRender();
  }

  setCloud(cloud, colorMode) {
    if (this.points) {
      this.scene.remove(this.points);
      this.points.geometry.dispose();
      this.points = null;
    }
    this.cloud = cloud;
    this.highlighted = [];
    if (!cloud) {
      this.requestRender();
      return;
    }
    const geometry = new THREE.BufferGeometry();

    geometry.setAttribute("position", new THREE.BufferAttribute(cloud.positions, 3));
    this.rawColors = computeColors(cloud, colorMode, this.groundZ);
    // labels of the previous scan don't apply to this one until the store sends this scan's labels
    if (this.segLabels && this.segLabels.length !== cloud.count) this.segLabels = null;
    this.baseColors = this.composeColors();
    geometry.setAttribute("color", new THREE.BufferAttribute(this.baseColors.slice(), 3));
    geometry.computeBoundingSphere();
    this.points = new THREE.Points(geometry, this.pointsMaterial);
    this.scene.add(this.points);
    this.requestRender();
  }

  /** Neighbouring scans (already in this frame's coordinates), drawn dimmed underneath the current scan. */
  setOverlay(overlay) {
    if (this.overlayPoints) {
      this.scene.remove(this.overlayPoints);
      this.overlayPoints.geometry.dispose();
      this.overlayPoints = null;
    }
    if (overlay?.count) {
      const geometry = new THREE.BufferGeometry();

      geometry.setAttribute("position", new THREE.BufferAttribute(overlay.positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(overlayColors(overlay, this.groundZ), 3));
      geometry.computeBoundingSphere();
      this.overlayPoints = new THREE.Points(geometry, this.overlayMaterial);
      this.overlayPoints.renderOrder = -1;
      this.scene.add(this.overlayPoints);
    }
    this.requestRender();
  }

  setColorMode(mode) {
    if (!this.cloud || !this.points) return;
    this.rawColors = computeColors(this.cloud, mode, this.groundZ);
    this.highlighted = [];
    this.refreshColors();
  }

  setPointSize(size) {
    this.pointsMaterial.size = size;
    this.overlayMaterial.size = Math.max(1, size - 0.5);
    this.requestRender();
  }

  /** Paint the points inside `box` white so the annotator sees what the box contains. */
  highlightBox(box) {
    if (!this.points || !this.cloud) return;
    const attr = this.points.geometry.getAttribute("color");
    const colors = attr.array;

    for (const i of this.highlighted) {
      colors[i * 3] = this.baseColors[i * 3];
      colors[i * 3 + 1] = this.baseColors[i * 3 + 1];
      colors[i * 3 + 2] = this.baseColors[i * 3 + 2];
    }
    this.highlighted = box ? pointIndicesInBox(box, this.cloud.positions, this.cloud.count) : [];
    for (const i of this.highlighted) {
      colors[i * 3] = HIGHLIGHT.r;
      colors[i * 3 + 1] = HIGHLIGHT.g;
      colors[i * 3 + 2] = HIGHLIGHT.b;
    }
    attr.needsUpdate = true;
    this.requestRender();
  }

  /**
   * @param {{id:string, box:import('./geometry').Box3D, color:string, selected:boolean, hidden:boolean}[]} items
   */
  syncBoxes(items) {
    const seen = new Set();

    for (const item of items) {
      seen.add(item.id);
      let entry = this.boxes.get(item.id);

      if (!entry) {
        const group = new THREE.Group();
        const lineMaterial = new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true });
        const edges = new THREE.LineSegments(this.unitEdges, lineMaterial);

        edges.renderOrder = 5;
        const fillMaterial = new THREE.MeshBasicMaterial({
          color: 0xffffff,
          transparent: true,
          opacity: 0.08,
          depthWrite: false,
        });
        const mesh = new THREE.Mesh(this.unitBox, fillMaterial);

        mesh.userData.regionId = item.id;
        group.add(edges);
        group.add(mesh);
        this.scene.add(group);
        entry = { group, edges, mesh };
        this.boxes.set(item.id, entry);
      }
      const { group, edges, mesh } = entry;
      const { box } = item;

      group.position.set(box.x, box.y, box.z);
      group.rotation.set(0, 0, box.yaw);
      group.scale.set(box.length, box.width, box.height);
      group.visible = !item.hidden;
      const color = new THREE.Color();

      try {
        color.setStyle(item.color || "#ff9a3c");
      } catch {
        color.set(0xff9a3c);
      }
      edges.material.color.copy(item.selected ? HIGHLIGHT : color);
      edges.material.opacity = item.selected ? 1 : 0.9;
      mesh.material.color.copy(color);
      mesh.material.opacity = item.selected ? 0.25 : 0.08;
    }
    for (const [id, entry] of this.boxes) {
      if (!seen.has(id)) {
        this.scene.remove(entry.group);
        entry.edges.material.dispose();
        entry.mesh.material.dispose();
        this.boxes.delete(id);
      }
    }
    this.requestRender();
  }

  focus(box) {
    if (!box) return;
    const target = new THREE.Vector3(box.x, box.y, box.z);
    const offsetP = this.perspective.position.clone().sub(this.perspectiveControls.target);
    const distance = Math.max(8, Math.max(box.length, box.width, box.height) * 3);

    offsetP.setLength(distance);
    this.perspectiveControls.target.copy(target);
    this.perspective.position.copy(target).add(offsetP);
    this.perspectiveControls.update();

    this.bevControls.target.set(box.x, box.y, 0);
    this.bev.position.set(box.x, box.y, 200);
    this.bevControls.update();
    this.requestRender();
  }

  /* ---------------------------------------------------------------------------------------------- */
  /* pointer interaction                                                                             */
  /* ---------------------------------------------------------------------------------------------- */

  ndc(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();

    return new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
  }

  groundPoint(event, z = this.groundZ) {
    this.raycaster.setFromCamera(this.ndc(event), this.camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -z);
    const hit = new THREE.Vector3();

    return this.raycaster.ray.intersectPlane(plane, hit) ? hit : null;
  }

  pickBox(event) {
    this.raycaster.setFromCamera(this.ndc(event), this.camera);
    const meshes = [...this.boxes.values()].filter((b) => b.group.visible).map((b) => b.mesh);
    const hits = this.raycaster.intersectObjects(meshes, false);

    return hits.length ? hits[0].object.userData.regionId : null;
  }

  /** Canvas-relative CSS pixel position of a pointer event. */
  local(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();

    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  /** Screen positions of all points for the current camera (fixed for the duration of a stroke). */
  projectCloud() {
    const camera = this.camera;
    const canvas = this.renderer.domElement;

    camera.updateMatrixWorld();
    const m = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);

    return projectToScreen(this.cloud.positions, this.cloud.count, m.elements, canvas.clientWidth, canvas.clientHeight);
  }

  onHover(event) {
    if (this.tool !== "brush") return;
    const [x, y] = this.local(event);

    this.brushCircle.setAttribute("cx", String(x));
    this.brushCircle.setAttribute("cy", String(y));
    this.brushCircle.setAttribute("r", String(this.brushSize));
    this.brushCircle.style.display = "";
  }

  onLeave() {
    if (!this.pointer) this.brushCircle.style.display = "none";
  }

  /** Paint along the segment from the last brush position, so fast strokes leave no gaps. */
  brushTo(x, y) {
    const pointer = this.pointer;
    const [px, py] = pointer.last ?? [x, y];
    const steps = Math.max(1, Math.ceil(Math.hypot(x - px, y - py) / Math.max(1, this.brushSize / 2)));
    const indices = new Set();

    for (let k = 1; k <= steps; k++) {
      const t = k / steps;

      for (const i of indicesInCircle(pointer.screen, px + (x - px) * t, py + (y - py) * t, this.brushSize)) {
        indices.add(i);
      }
    }
    pointer.last = [x, y];
    if (indices.size) this.callbacks.onPaint?.([...indices]);
  }

  onPointerDown(event) {
    if (event.button !== 0) return;
    const start = { x: event.clientX, y: event.clientY };

    if (PAINT_TOOLS.includes(this.tool)) {
      if (!this.cloud) return;
      // OrbitControls listens on the same canvas
      event.stopImmediatePropagation();
      const erase = event.shiftKey || event.altKey;
      const [x, y] = this.local(event);

      if (this.tool === "lasso") {
        this.pointer = { mode: "lasso", start, erase, path: [[x, y]] };
        this.updateLasso();
        return;
      }
      if (!this.callbacks.onPaintStart?.(erase)) return;
      this.pointer = { mode: "brush", start, screen: this.projectCloud(), last: null };
      this.brushTo(x, y);
      return;
    }

    if (this.tool === "draw" && this.viewMode === "bev") {
      const p = this.groundPoint(event);

      if (!p) return;
      this.pointer = { mode: "draw", start, from: p, to: p.clone() };
      this.updatePreview();
      return;
    }

    const regionId = this.pickBox(event);
    const selectedId = this.callbacks.getSelectedId?.();

    if (this.viewMode === "bev" && regionId && regionId === selectedId && this.callbacks.canEdit?.()) {
      // drag the selected box on the ground plane
      const entry = this.boxes.get(regionId);
      const p = this.groundPoint(event, entry.group.position.z);

      if (p) {
        // OrbitControls listens on the same canvas, so stop it from starting a pan
        event.stopImmediatePropagation();
        this.bevControls.enabled = false;
        this.pointer = { mode: "move", start, from: p, regionId, moved: false, z: entry.group.position.z };
        return;
      }
    }
    this.pointer = { mode: "click", start, regionId };
  }

  onPointerMove(event) {
    const pointer = this.pointer;

    if (!pointer) return;
    if (pointer.mode === "brush") {
      const [x, y] = this.local(event);

      this.onHover(event);
      this.brushTo(x, y);
      return;
    }
    if (pointer.mode === "lasso") {
      const [x, y] = this.local(event);
      const [lx, ly] = pointer.path[pointer.path.length - 1];

      if (Math.hypot(x - lx, y - ly) >= 3) {
        pointer.path.push([x, y]);
        this.updateLasso();
      }
      return;
    }
    if (pointer.mode === "draw") {
      const p = this.groundPoint(event);

      if (p) pointer.to = p;
      this.updatePreview();
    } else if (pointer.mode === "move") {
      const p = this.groundPoint(event, pointer.z);

      if (!p) return;
      const dist = Math.hypot(event.clientX - pointer.start.x, event.clientY - pointer.start.y);

      if (!pointer.moved && dist < DRAG_THRESHOLD_PX) return;
      if (!pointer.moved) this.callbacks.onMoveStart?.(pointer.regionId);
      pointer.moved = true;
      this.callbacks.onMove?.(pointer.regionId, p.x - pointer.from.x, p.y - pointer.from.y);
    }
  }

  onPointerUp(event) {
    const pointer = this.pointer;

    if (!pointer) return;
    this.pointer = null;
    const dist = Math.hypot(event.clientX - pointer.start.x, event.clientY - pointer.start.y);

    if (pointer.mode === "brush") {
      this.callbacks.onPaintEnd?.();
      return;
    }
    if (pointer.mode === "lasso") {
      this.lassoPath.setAttribute("points", "");
      if (pointer.path.length >= 3 && this.cloud && this.callbacks.onPaintStart?.(pointer.erase)) {
        const indices = indicesInPolygon(this.projectCloud(), pointer.path);

        if (indices.length) this.callbacks.onPaint?.(indices);
        this.callbacks.onPaintEnd?.();
      }
      return;
    }
    if (pointer.mode === "draw") {
      this.preview.visible = false;
      this.requestRender();
      const { from, to } = pointer;
      const length = Math.abs(to.x - from.x);
      const width = Math.abs(to.y - from.y);

      if (length > 0.2 && width > 0.2) {
        this.callbacks.onDraw?.({ x: (from.x + to.x) / 2, y: (from.y + to.y) / 2, length, width, yaw: 0 });
      }
      return;
    }
    if (pointer.mode === "move") {
      this.bevControls.enabled = this.viewMode === "bev";
      if (pointer.moved) this.callbacks.onMoveEnd?.(pointer.regionId);
      else this.callbacks.onSelect?.(pointer.regionId);
      return;
    }
    // plain click (not a camera drag) selects / unselects
    if (dist < DRAG_THRESHOLD_PX) this.callbacks.onSelect?.(pointer.regionId);
  }

  updateLasso() {
    const path = this.pointer?.path ?? [];

    this.lassoPath.setAttribute("points", path.map(([x, y]) => `${x},${y}`).join(" "));
  }

  updatePreview() {
    const pointer = this.pointer;

    if (!pointer || pointer.mode !== "draw") return;
    const { from, to } = pointer;
    const z = this.groundZ + 0.05;
    const attr = this.preview.geometry.getAttribute("position");
    const pts = [
      [from.x, from.y],
      [to.x, from.y],
      [to.x, to.y],
      [from.x, to.y],
      [from.x, from.y],
    ];

    pts.forEach(([x, y], i) => attr.setXYZ(i, x, y, z));
    attr.needsUpdate = true;
    this.preview.geometry.computeBoundingSphere();
    this.preview.visible = true;
    this.requestRender();
  }

  dispose() {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.renderer.domElement.removeEventListener("pointerdown", this.onPointerDown, true);
    this.renderer.domElement.removeEventListener("pointermove", this.onHover);
    this.renderer.domElement.removeEventListener("pointerleave", this.onLeave);
    this.svg.remove();
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("pointerup", this.onPointerUp);
    this.perspectiveControls.dispose();
    this.bevControls.dispose();
    for (const entry of this.boxes.values()) {
      entry.edges.material.dispose();
      entry.mesh.material.dispose();
    }
    this.boxes.clear();
    this.points?.geometry.dispose();
    this.pointsMaterial.dispose();
    this.overlayPoints?.geometry.dispose();
    this.overlayMaterial.dispose();
    this.unitEdges.dispose();
    this.unitBox.dispose();
    this.preview.geometry.dispose();
    this.preview.material.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
