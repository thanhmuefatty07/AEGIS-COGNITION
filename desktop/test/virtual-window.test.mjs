import assert from "node:assert/strict";
import test from "node:test";
import { virtualWindow } from "../src/virtual_window.js";

test("virtual window keeps a 10,000-event timeline bounded around its scroll position", () => {
  const result = virtualWindow(10_000, 64 * 1_000, 420, 64, 6);

  assert.equal(result.start, 994);
  assert.equal(result.end, 1_013);
  assert.equal(result.offset, 994 * 64);
  assert.equal(result.totalHeight, 10_000 * 64);
  assert.ok(result.end - result.start <= 20);
});

test("virtual window clamps empty lists and invalid negative scroll values", () => {
  assert.deepEqual(virtualWindow(0, -20, 100, 40), {
    start: 0,
    end: 0,
    offset: 0,
    totalHeight: 0,
  });
  assert.deepEqual(virtualWindow(3, -20, 100, 40), {
    start: 0,
    end: 3,
    offset: 0,
    totalHeight: 120,
  });
});
