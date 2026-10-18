// One index over the navigation and the server's own command/chat names.
// Entries describe navigation or a draft, never a request to send. Ranking
// preserves source order within a tier so equal matches stay predictable.
export function paletteEntries({pages = [], commands = [], chats = [], forms = []}) {
  return [
    ...pages.map(({page, label, detail = ''}) => ({
      id: `page:${page}`, kind: 'page', label, detail, run: {page},
    })),
    ...chats.map(({id, title}) => ({
      id: `chat:${id}`, kind: 'chat', label: title || id, detail: 'Chat', run: {page: 'chat', arg: id},
    })),
    ...forms.map(({id, title, summary}) => ({
      id: `form:${id}`, kind: 'form', label: title, detail: summary, run: {page: 'workflows', arg: id},
    })),
    ...commands.map(({name, summary}) => ({
      id: `command:${name}`, kind: 'command', label: `/${name}`, detail: summary, run: {compose: `/${name} `},
    })),
  ];
}

function rank(query, entry) {
  const label = entry.label.toLowerCase();
  if (label === query) return 0;
  if (label.startsWith(query)) return 1;
  if (label.split(/[^\p{L}\p{N}]+/u).some((word) => word.startsWith(query))) return 2;
  if (label.includes(query)) return 3;
  if ((entry.detail || '').toLowerCase().includes(query)) return 4;
  return Infinity;
}

export function rankEntries(query, entries) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return [...entries].sort((a, b) => Number(a.kind !== 'page') - Number(b.kind !== 'page'));
  return entries.map((entry) => ({entry, rank: rank(normalized, entry)}))
    .filter(({rank}) => Number.isFinite(rank))
    .sort((a, b) => a.rank - b.rank)
    .map(({entry}) => entry);
}
