import { Link, useParams } from "react-router-dom";
import { api, DiscoveryMeta, LaneReport } from "../api";
import { useFetch } from "../useFetch";

// What each lane checked + found, plus the full agent trace — the "why wasn't X
// surfaced?" view. Reads the run's stored meta + log.
const LANE_LABEL: Record<string, string> = {
  arxiv: "Papers · arXiv / OpenAlex",
  sources: "Sources · lab & org blogs",
  forum: "Forum · AF / LW",
};
const LANE_ORDER = ["arxiv", "sources", "forum"];
const usd = (c?: number) => (c ?? 0).toFixed(2);

export default function RunDetail() {
  const { id } = useParams();
  const { data: r, loading, error } = useFetch(() => api.getRun(id!), [id]);

  if (loading) return <p className="dim">Loading…</p>;
  if (error || !r) return <p className="empty">{error || "Not found"}</p>;

  const meta = r.meta || {};
  const disc: DiscoveryMeta = meta.discovery || meta; // onboarding runs nest it
  const onb = meta.onboarding;
  const reports = disc.subagent_reports || {};
  const subs = disc.subagents || {};
  const lanes = LANE_ORDER.filter((l) => reports[l] || subs[l]);

  const totalCost =
    Object.values(subs).reduce((s, a) => s + (a.cost_usd ?? 0), 0) +
    (disc.curator?.cost_usd ?? 0) +
    (onb?.cost_usd ?? 0);

  return (
    <>
      <div className="view-header">
        <h2>{r.kind} run</h2>
        <p className="dim">
          {r.status}
          {r.window_start && <> · {r.window_start} → {r.window_end}</>} · started {r.started_at}
          {r.finished_at && <> · finished {r.finished_at}</>}
        </p>
      </div>

      {r.error && <p className="empty">Error: {r.error}</p>}

      <p className="coverage-note">
        {disc.n_candidates != null && (
          <><strong>{disc.n_candidates}</strong> candidates → <strong>{disc.n_kept}</strong> kept · </>
        )}
        cost ≈ <strong>${usd(totalCost)}</strong>
        {onb && <> (onboarding ${usd(onb.cost_usd)})</>}
        {r.digest_id && (
          <> · <Link to={`/briefings/${r.digest_id}`}>view the feed it produced →</Link></>
        )}
      </p>

      {lanes.map((lane) => {
        const rep: LaneReport = reports[lane] || {};
        const sub = subs[lane] || {};
        return (
          <div className="lane-card" key={lane}>
            <h3>
              {LANE_LABEL[lane] || lane}
              <span className="dim small">
                {" · "}{rep.n_kept ?? 0} kept
                {sub.cost_usd != null && <> · ${usd(sub.cost_usd)}</>}
                {sub.turns != null && <> · {sub.turns} turns</>}
              </span>
            </h3>
            {sub.error && <p className="empty small">crashed: {sub.error}</p>}
            {rep.profile_interpretation && <p className="body-text">{rep.profile_interpretation}</p>}

            {!!rep.searches_performed?.length && (
              <div className="lane-block">
                <span className="lane-label">Searches</span>
                {rep.searches_performed.map((s, i) => (
                  <div className="dim small" key={i}>
                    {s.query}
                    {s.results_count != null && <> — {s.results_count} hits</>}
                    {s.error && <> — error</>}
                  </div>
                ))}
              </div>
            )}

            {!!rep.sources_checked?.length && (
              <div className="lane-block">
                <span className="lane-label">Sources checked</span>
                {rep.sources_checked.map((s, i) => (
                  <div className="dim small" key={i}>
                    {s.name} — {s.status}
                    {s.items_found != null && <> ({s.items_found})</>}
                    {s.note && <> · {s.note}</>}
                  </div>
                ))}
              </div>
            )}

            {rep.coverage_notes && <p className="dim small">{rep.coverage_notes}</p>}
          </div>
        );
      })}

      {!!disc.discovered_sources?.length && (
        <div className="lane-card">
          <h3>New sources found while roaming</h3>
          {disc.discovered_sources.map((s, i) => (
            <div className="dim small" key={i}>
              <strong>{s.name}</strong> — {s.url}
              {s.why && <> · {s.why}</>}
            </div>
          ))}
        </div>
      )}

      {r.log && (
        <details className="mt-20">
          <summary className="dim small">Full agent log ({(r.log.length / 1000).toFixed(0)} KB)</summary>
          <pre className="log-box tall">{r.log}</pre>
        </details>
      )}
    </>
  );
}
