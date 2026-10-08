export function virtualWindow(
  total: number,
  scrollTop: number,
  viewportHeight: number,
  rowHeight: number,
  overscan?: number,
): { start: number; end: number; offset: number; totalHeight: number };
