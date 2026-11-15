import { useEffect, useRef, useState } from "react";

import { cn } from "../../../utils/bem";
import { projectBoxEdges, projectPoint } from "./geometry";
import { turbo } from "./Viewer3D";

/**
 * One camera image with the 3D boxes (and optionally the LiDAR points) projected onto it.
 * Projection: pixel = intrinsic * (extrinsic * [x y z 1]) — see geometry.normalizeCalibration().
 */
export const CameraView = ({ camera, boxes, cloud, showPoints }) => {
  const imgRef = useRef(null);
  const canvasRef = useRef(null);
  const [size, setSize] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setSize(null);
    setFailed(false);
  }, [camera.url]);

  useEffect(() => {
    const canvas = canvasRef.current;

    if (!canvas || !size) return;
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext("2d");

    ctx.clearRect(0, 0, size.width, size.height);
    const calib = camera.calibration;

    if (!calib) return;
    const lineScale = Math.max(1, size.width / 800);

    if (showPoints && cloud) {
      const step = Math.max(1, Math.floor(cloud.count / 40000));

      for (let i = 0; i < cloud.count; i += step) {
        const x = cloud.positions[i * 3];
        const y = cloud.positions[i * 3 + 1];
        const z = cloud.positions[i * 3 + 2];
        const px = projectPoint(calib, x, y, z);

        if (!px || px[0] < 0 || px[1] < 0 || px[0] >= size.width || px[1] >= size.height) continue;
        const [r, g, b] = turbo(Math.hypot(x, y, z) / 50);

        ctx.fillStyle = `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`;
        ctx.fillRect(px[0] - lineScale, px[1] - lineScale, 2 * lineScale, 2 * lineScale);
      }
    }

    for (const item of boxes) {
      if (item.hidden) continue;
      const segments = projectBoxEdges(calib, item.box);

      ctx.strokeStyle = item.selected ? "#ffffff" : item.color || "#ff9a3c";
      for (const { from, to, front } of segments) {
        ctx.lineWidth = (item.selected ? 3 : 2) * lineScale * (front ? 1.5 : 1);
        ctx.beginPath();
        ctx.moveTo(from[0], from[1]);
        ctx.lineTo(to[0], to[1]);
        ctx.stroke();
      }
    }
  }, [boxes, size, camera, cloud, showPoints]);

  return (
    <figure className={cn("pointcloud").elem("camera").toClassName()}>
      <div className={cn("pointcloud").elem("camera-frame").toClassName()}>
        <img
          ref={imgRef}
          src={camera.url}
          alt={`Camera image: ${camera.name}`}
          onLoad={(e) => setSize({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })}
          onError={() => setFailed(true)}
        />
        <canvas ref={canvasRef} aria-hidden="true" />
      </div>
      <figcaption>
        {camera.name}
        {!camera.calibration && " — no calibration, boxes can't be projected"}
        {failed && " — image failed to load"}
      </figcaption>
    </figure>
  );
};
