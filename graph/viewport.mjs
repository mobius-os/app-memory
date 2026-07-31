export const MIN_ZOOM = 0.22;
export const MAX_ZOOM = 4.6;

export function scaleRendererTransformAt(transform, point, nextScale) {
  const scale = Math.max(0.001, Number(transform?.k) || 1);
  const graphX = (point.x - (Number(transform?.x) || 0)) / scale;
  const graphY = (point.y - (Number(transform?.y) || 0)) / scale;
  return {
    x: point.x - graphX * nextScale,
    y: point.y - graphY * nextScale,
    k: nextScale,
  };
}

export function pinchRendererTransform(startTransform, startCenter, currentCenter, spread) {
  const numericSpread = Number(spread);
  const safeSpread = Number.isFinite(numericSpread)
    ? Math.max(0.001, numericSpread)
    : 1;
  const nextScale = clamp(
    (Number(startTransform?.k) || 1) * safeSpread,
    MIN_ZOOM,
    MAX_ZOOM,
  );
  const anchored = scaleRendererTransformAt(startTransform, startCenter, nextScale);
  return {
    x: anchored.x + currentCenter.x - startCenter.x,
    y: anchored.y + currentCenter.y - startCenter.y,
    k: anchored.k,
  };
}

export function midpoint(first, second) {
  return {
    x: (first.x + second.x) / 2,
    y: (first.y + second.y) / 2,
  };
}

export function pointDistance(first, second) {
  return Math.hypot(second.x - first.x, second.y - first.y);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
