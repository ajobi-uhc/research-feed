import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, DiscoveryMeta, LaneReport, Stages } from "../api";
import { useFetch } from "../useFetch";
import Stepper from "../components/Stepper";

// One run view. While the run is in flight it streams live progress (stepper +
// agent log) over SSE; once finished it shows the saved trace (per-lane
// breakdown + full log). Reached live from "Create digest" / onboarding, or
// later from the Runs tab.
const LANE_LABEL: Record<string, string> = {
  arxiv: "Papers · arXiv / OpenAlex",
  sources: "Sources · lab & org blogs",
  forum: "Forum · AF / LW",
};
const LANE_ORDER = ["arxiv", "sources", "forum"];
const usd = (c?: number) => (c ?? 0).toFixed(2);

export default function RunDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: r, loading, error, refetch } = useFetch(() => api.getRun(id!), [id]);
  const [lines, setLines] = useState<string[]>([]);
  const [stages, setStages] = useState<Stages>({});
  const boxRef = useRef<HTMLPreElement>(null);

  const running = r?.status === "running";

  // Live stream while the run is in flight.
  useEffect(() => {
    if (!running) return;
    const es = new EventSource("/api/run/stream");
    es.onmessage = (e) => setLines((l) => [...l, e.data]);
    es.addEventListener("stage", (e) => {
      try { setStages(JSON.parse((e as MessageEvent).data)); } catch { /* ignore */ }
    });
    es.addEventListener("done", (e) => {
      es.close();
      nav((e as MessageEvent).data || "/"); // land on the digest it produced
    });
    es.addEventListener("failed", (e) => {
      es.close();
      setLines((l) => [...l, "FAILED: " + (e as MessageEvent).data]);
      refetch(); // reload as a finished (error) trace
    });
    return () => es.close();
  }, [running]);

  // Autoscroll the live log.
  useEffect(() => { boxRef.current?.scrollTo(0, boxRef.current.scrollHeight); }, [lines]);

  if (loading || !r) return <p className="dim">Loading…</p>;
  if (error) return <p className="empty">{error}</p>;

  // ── Live view (run in flight) ──────────────────────────────────────
  if (running) {
    return (
      <>
        <div className="view-header">
          <h2>{r.kind === "onboarding" ? "Building your profile & first digest…" : "Creating your digest…"}</h2>
          <p className="dim">Live agent activity. Onboarding ~15 min · a digest ~10–15 min.</p>
        </div>
        <Stepper stages={stages} />
        <details className="mt-12" open>
          <summary className="dim small">Agent log</summary>
          <pre className="log-box tall" ref={boxRef}>{lines.join("\n")}</pre>
        </details>
      </>
    );
  }

  // ── Saved trace (run finished) ─────────────────────────────────────
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
          <> · <Link to={`/digests/${r.digest_id}`}>view the digest it produced →</Link></>
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
