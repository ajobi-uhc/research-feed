export function lastName(name: string): string {
  const toks = (name || "").trim().split(/\s+/);
  return toks.length ? toks[toks.length - 1].toLowerCase() : "";
}

// Set of followed authors' last names, for the "followed author" badge.
export function followedLastNames(authors: { name: string }[] = []): Set<string> {
  return new Set(authors.map((a) => lastName(a.name)).filter((n) => n.length > 2));
}

const VENUE_LABEL: Record<string, string> = {
  arxiv: "arXiv",
  alignment_forum: "AF",
  lesswrong: "LW",
  transformer_circuits: "Transformer Circuits",
  anthropic_alignment_science_blog: "Anthropic",
  openai_alignment_research_blog: "OpenAI",
  metr_blog: "METR",
  apollo_research_blog: "Apollo",
  mats_research: "MATS",
};

export function venueLabel(v: string): string {
  return VENUE_LABEL[v] || (v || "").replace(/_/g, " ");
}

// Which discovery lane an item came from — for the "drawn from" coverage note.
// Reads discovered_via (e.g. "openalex:…", "forum:af", "sources:web:…",
// "rescued:arxiv"), falling back to venue.
function laneOf(via: string, venue: string): string {
  const primary = (via || "").toLowerCase().split("+")[0].trim();
  const sub = primary.startsWith("rescued:") ? primary.slice(8) : primary;
  if (sub.startsWith("openalex") || sub.includes("arxiv")) return "papers";
  if (sub.startsWith("forum")) return "forum";
  if (sub.startsWith("sources")) return "lab/org";
  const v = (venue || "").toLowerCase();
  if (v === "arxiv") return "papers";
  if (v === "alignment_forum" || v === "lesswrong") return "forum";
  if (v) return "lab/org";
  return "other";
}

// Count kept items per lane, biggest first — "lab/org ×9 · papers ×4 · forum ×4".
const LANE_ORDER = ["papers", "lab/org", "forum", "other"];
export function sourceMix(items: { discovered_via?: string; venue?: string }[]): { label: string; n: number }[] {
  const counts: Record<string, number> = {};
  for (const it of items) {
    const lane = laneOf(it.discovered_via || "", it.venue || "");
    counts[lane] = (counts[lane] || 0) + 1;
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1] || LANE_ORDER.indexOf(a[0]) - LANE_ORDER.indexOf(b[0]))
    .map(([label, n]) => ({ label, n }));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function shortDate(d: string): string {
  const [, m, day] = d.split("-").map(Number);
  return `${MONTHS[m - 1]} ${day}`;
}

// Split a window into 7-day buckets, for the per-week feed tabs. Returns [] when
// the window is too short to be worth slicing (e.g. a single-week catch-up).
export function weekBuckets(start: string, end: string): { label: string; from: string; to: string }[] {
  const toDate = (s: string) => new Date(s + "T00:00:00Z");
  const s = toDate(start), e = toDate(end);
  if (isNaN(+s) || isNaN(+e)) return [];
  const days = Math.round((+e - +s) / 86400000) + 1;
  if (days <= 10) return [];
  const out: { label: string; from: string; to: string }[] = [];
  const cur = new Date(s);
  while (cur <= e) {
    const from = cur.toISOString().slice(0, 10);
    const toD = new Date(cur);
    toD.setUTCDate(toD.getUTCDate() + 6);
    const to = (toD > e ? e : toD).toISOString().slice(0, 10);
    out.push({ label: shortDate(from), from, to });
    cur.setUTCDate(cur.getUTCDate() + 7);
  }
  return out;
}
