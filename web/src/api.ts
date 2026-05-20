const JSON_HEADERS = { "Content-Type": "application/json" };

async function req(path: string, opts?: RequestInit) {
  const r = await fetch(`/api${path}`, opts);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.status === 204 ? null : r.json();
}

export const api = {
  getProfile: () => req("/profile"),
  putProfile: (p: Profile) =>
    req("/profile", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(p) }),

  listDigests: (): Promise<DigestSummary[]> => req("/digests"),
  getDigest: (id: string): Promise<Digest> => req(`/digests/${id}`),

  listRuns: (): Promise<RunSummary[]> => req("/runs"),
  getRun: (id: string): Promise<Run> => req(`/runs/${id}`),

  runState: (): Promise<RunState> => req("/run"),
  startDigest: (body: { range?: string; window_start?: string; window_end?: string }) =>
    req("/digests", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) }),
  startOnboarding: (body: OnboardingInput) =>
    req("/onboarding", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) }),

  proposals: () => req("/proposals"),
  acceptProposal: (id: string) => req(`/proposals/${id}/accept`, { method: "POST" }),
  rejectProposal: (id: string) => req(`/proposals/${id}/reject`, { method: "POST" }),

  propose: (kind: string, payload: Record<string, unknown>, rationale = "") =>
    req("/propose", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ kind, payload, rationale }) }),
  addNote: (text: string) =>
    req("/note", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ text }) }),

  listSamples: (): Promise<{ persona: string; label: string; has_feed: boolean }[]> => req("/samples"),
  loadSample: (persona: string) => req(`/samples/${persona}`, { method: "POST" }),
};

export type Author = { name: string; affiliation?: string; why?: string };
export type Source = { name?: string; url: string; why?: string };
export type Profile = {
  user_summary: string;
  current_question: string;
  interests: string[];
  authors: Author[];
  sources: Source[];
  filter_outs: string[];
  notes: string;
  seed_papers?: string[];
  origin?: "onboarding" | "user" | "sample";
};

export type Item = {
  id: string;
  title: string;
  url: string;
  venue: string;
  date: string;
  authors: string[];
  summary: string;
  discovered_via: string;
  why_kept: string;
  venue_detail?: string;
  karma?: number | null;
  comments?: number | null;
  arxiv_id?: string | null;
};
export type Digest = {
  id: string;
  generated_at: string;
  window_start: string;
  window_end: string;
  kept: Item[];
  dropped: { title: string; url: string; source?: string; reason: string }[];
  coverage?: { items_considered?: number; items_kept?: number; items_dropped?: number };
  profile_snapshot?: { authors?: Author[] };
};
export type DigestSummary = {
  id: string;
  generated_at: string;
  window_start: string;
  window_end: string;
};
export type RunSummary = {
  id: string;
  kind: string;
  status: string;
  started_at: string;
  finished_at?: string;
  window_start?: string;
  window_end?: string;
  digest_id?: string;
};
export type LaneReport = {
  profile_interpretation?: string;
  searches_performed?: { query: string; results_count?: number; error?: string }[];
  sources_checked?: { name: string; status?: string; items_found?: number; note?: string }[];
  excluded_aggregate?: string;
  coverage_notes?: string;
  n_kept?: number;
  n_close_call_excludes?: number;
};
export type DiscoveryMeta = {
  subagents?: Record<string, { turns?: number; cost_usd?: number; error?: string | null }>;
  subagent_reports?: Record<string, LaneReport>;
  curator?: { turns?: number; cost_usd?: number };
  discovered_sources?: { name: string; url: string; why?: string }[];
  n_candidates?: number;
  n_kept?: number;
};
// A discovery run's meta is a DiscoveryMeta; an onboarding run nests one under `discovery`.
export type Run = RunSummary & {
  error?: string;
  log?: string;
  meta?: DiscoveryMeta & {
    onboarding?: { turns?: number; cost_usd?: number };
    discovery?: DiscoveryMeta;
  };
};
export type StageStatus = "pending" | "running" | "done" | "error";
export type Stages = Record<string, { status: StageStatus; detail?: string }>;
export type RunState = {
  kind: string | null;
  status: "idle" | "running" | "done" | "error";
  result: string | null;
  error: string | null;
  run_id: string | null;
  progress: string[];
  stages: Stages;
};
export type OnboardingInput = {
  seed_papers: string[];
  scholar_url: string;
  followed_authors: string[];
  current_question: string;
  filter_outs: string[];
  freeform: string;
};
