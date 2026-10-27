export function memorySummary(memory) {
  return `Revision ${memory.revision ?? "?"} · ~${memory.estimated_tokens ?? "?"} token · compact ${memory.compactions ?? 0} lần`;
}

export function memoryEntryTitle(entry) {
  const labels = {summary:"Tóm tắt các hành động cũ",tool:"Tool",assistant:"Phản hồi model",
    working_notes:"Kết luận, giả thuyết đã loại và bước tiếp theo",handoff:"Bàn giao",round_started:"Bắt đầu vòng",failure:"Lỗi"};
  return `#${entry.sequence ?? "?"} · vòng ${entry.round ?? "?"} · ${entry.tool || labels[entry.kind] || entry.kind || "Bản ghi"}`;
}

export function mergeMemoryPage(previous, page) {
  const same = previous?.id === page.id && !page.reset;
  const entries = same ? [...previous.entries] : [];
  const sequences = new Set(entries.map(entry => entry.sequence).filter(Number.isInteger));
  for (const entry of page.entries || []) {
    if (Number.isInteger(entry.sequence) && sequences.has(entry.sequence)) continue;
    entries.push(entry);
    if (Number.isInteger(entry.sequence)) sequences.add(entry.sequence);
  }
  return {...page, entries};
}

export function memoryRevisionLabel(row) {
  return `Revision ${row.revision} · vòng ${row.round ?? "?"} · ${row.tool || row.kind || "lưu memory"}${row.compacted ? " · COMPACT" : ""}`;
}

export function memoryChangeSummary(change) {
  if (!change.available) return "Không đủ dữ liệu để so sánh chính xác revision này.";
  return `Revision ${change.previous_revision} → ${change.revision} · +${change.added.length} thêm · −${change.removed.length} bỏ · ${change.updated.length} rút gọn/thay đổi${change.metadata?.compacted ? " · Đã compact" : ""}`;
}

export function mergeMemoryHistory(previous, page) {
  const rows = previous?.id === page.id ? [...previous.revisions] : [];
  const byRevision = new Map(rows.map(row => [row.revision, row]));
  for (const row of page.revisions || []) byRevision.set(row.revision, row);
  return {...page, revisions:[...byRevision.values()].sort((a,b)=>b.revision-a.revision)};
}
