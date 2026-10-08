export function virtualWindow(total, scrollTop, viewportHeight, rowHeight, overscan = 6) {
  const count = Math.max(0, Math.floor(total));
  const height = Math.max(1, rowHeight);
  const visible = Math.max(1, Math.ceil(Math.max(0, viewportHeight) / height));
  const firstVisible = Math.min(count, Math.floor(Math.max(0, scrollTop) / height));
  const start = Math.max(0, firstVisible - Math.max(0, Math.floor(overscan)));
  const end = Math.min(count, firstVisible + visible + Math.max(0, Math.floor(overscan)));
  return { start, end, offset: start * height, totalHeight: count * height };
}
