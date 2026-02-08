const assert = require("node:assert/strict");
const { test } = require("node:test");

const { computeMessageChannelTabItems } = require("../channel_tabs.js");

test("returns only All when there are no observed channels", () => {
  const items = computeMessageChannelTabItems([], new Map(), () => "", () => "");
  assert.deepEqual(items, [{ id: "all", label: "All" }]);
});

test("uses channel info when available and hash label when missing", () => {
  const map = new Map();
  map.set(1, { name: "Primary" });
  const items = computeMessageChannelTabItems(
    ["1", "3"],
    map,
    (idx, info) => `#${idx} (${info.name})`,
    (id) => `#${id} (hash)`
  );
  assert.deepEqual(items, [
    { id: "all", label: "All" },
    { id: "1", label: "#1 (Primary)" },
    { id: "3", label: "#3 (hash)" },
  ]);
});

test("dedupes observed ids and handles non-numeric ids", () => {
  const items = computeMessageChannelTabItems(
    ["2", "2", "abc"],
    new Map(),
    (idx) => `#${idx}`,
    (id) => `#${id} (hash)`
  );
  assert.deepEqual(items, [
    { id: "all", label: "All" },
    { id: "2", label: "#2 (hash)" },
    { id: "abc", label: "#abc (hash)" },
  ]);
});
