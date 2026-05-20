import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, DigestSummary, Digest } from "../api";
import { useFetch } from "../useFetch";
import Catchup from "../components/Catchup";
import SampleLoader from "../components/SampleLoader";

export default function Feed() {
  const { data: list, loading, error } = useFetch(api.listDigests);
  const { data: profile } = useFetch(api.getProfile);
  const nav = useNavigate();
  const [adv, setAdv] = useState(false);
  const [ws, setWs] = useState("");
  const [we, setWe] = useState("");

  async function run(body: { range?: string; window_start?: string; window_end?: string }) {
    try {
      const res = await api.startDigest(body);
      nav(`/runs/${res.run_id}`);
    } catch (e) {
      if (String(e).includes("409")) {
        // A run is already going — jump to it rather than erroring.
        const st = await api.runState().catch(() => null);
        if (st?.run_id) nav(`/runs/${st.run_id}`);
        return;
      }
      alert(String(e));
    }
  }

  // Up to date = a digest already covers through today (so a new day means a new
  // digest can be created). A loaded sample is a ready-made snapshot to explore,
  // not a live feed — it always counts as up to date, no prompt to regenerate.
  const today = new Date().toISOString().slice(0, 10);
  const isSample = profile?.origin === "sample";
  const upToDate = isSample || (!!list && list.some((b) => b.window_end >= today));

  return (
    <>
      <div className="view-header">
        <h2>Feed</h2>
        <p className="dim">Your research digests — generate one to see what's new in your field.</p>
      </div>

      <div className="sample-panel">
        <SampleLoader />
      </div>

      <div className="catchup-bar">
        {upToDate ? (
          <>
            <span className="caught-up">✓ {isSample ? "Sample feed loaded" : "Today's digest is ready"}</span>
            <span className="dim small">
              {isSample
                ? "exploring a ready-made researcher — re-onboard to build your own"
                : "up to date through today · check back tomorrow"}
            </span>
          </>
        ) : (
          <>
            <button className="btn-primary" onClick={() => run({ range: "since_last" })}>
              Create digest
            </button>
            <span className="dim small">
              {list && list.length ? "new since your last digest" : "last 30 days"} · ~10–15 min · ~$2–3 to build
            </span>
          </>
        )}
        <button className="link-btn" onClick={() => setAdv((a) => !a)}>
          {adv ? "hide" : "custom range"}
        </button>
      </div>
      {adv && (
        <div className="adv-range">
          <input type="date" value={ws} onChange={(e) => setWs(e.target.value)} />
          <span className="dim">→</span>
          <input type="date" value={we} onChange={(e) => setWe(e.target.value)} />
          <button
            className="btn-secondary"
            disabled={!ws || !we}
            onClick={() => run({ window_start: ws, window_end: we })}
          >
            Run range
          </button>
        </div>
      )}

      {loading && <p className="dim">Loading…</p>}
      {error && <p className="empty">{error}</p>}
      {list && list.length === 0 && <p className="empty">No digests yet. Click “Create digest”.</p>}

      {list?.map((s, i) => <FeedSection key={s.id} summary={s} defaultOpen={i === 0} />)}
    </>
  );
}

// One digest in the timeline. Newest is open by default; others lazy-load on expand.
function FeedSection({ summary, defaultOpen }: { summary: DigestSummary; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open && !digest) {
      setLoading(true);
      api.getDigest(summary.id).then(setDigest).catch(() => {}).finally(() => setLoading(false));
    }
  }, [open]);

  return (
    <section className="catchup">
      <h2 className="catchup-head" onClick={() => setOpen((o) => !o)}>
        <span className="caret">{open ? "▾" : "▸"}</span> {summary.window_start} → {summary.window_end}
        <span className="dim small"> · {summary.generated_at}</span>
      </h2>
      {open && (loading || !digest ? <p className="dim">Loading…</p> : <Catchup digest={digest} />)}
    </section>
  );
}
