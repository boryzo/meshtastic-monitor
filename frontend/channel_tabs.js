(() => {
  const root = typeof window !== "undefined" ? window : globalThis;

  function computeMessageChannelTabItems(
    observedIds,
    channelsByIndex,
    formatIndexLabel,
    formatHashLabel
  ) {
    const items = [{ id: "all", label: "All" }];
    if (!Array.isArray(observedIds)) return items;
    const seen = new Set();
    for (const rawId of observedIds) {
      const id = String(rawId);
      if (seen.has(id)) continue;
      seen.add(id);
      const num = Number(id);
      if (Number.isInteger(num) && channelsByIndex && typeof channelsByIndex.get === "function") {
        const info = channelsByIndex.get(num);
        if (info && typeof formatIndexLabel === "function") {
          items.push({ id, label: formatIndexLabel(num, info) });
          continue;
        }
      }
      if (typeof formatHashLabel === "function") {
        items.push({ id, label: formatHashLabel(id) });
      } else {
        items.push({ id, label: `#${id} (hash)` });
      }
    }
    return items;
  }

  root.computeMessageChannelTabItems = computeMessageChannelTabItems;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { computeMessageChannelTabItems };
  }
})();
