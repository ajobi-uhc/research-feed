import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, BriefingSummary, Digest } from "../api";
import { useFetch } from "../useFetch";
import Catchup from "../components/Catchup";
import SampleLoader from "../components/SampleLoader";

export default function Feed() {
  const { data: list, loading, error } = useFetch(api.listBriefings);
  const nav = useNavigate();
  const [adv, setAdv] = useState(false);
  const [ws, setWs] = useState("");
  const [we, setWe] = useState("");

  async function run(body: { range?: string; window_start?: string; window_end?: string }) {
    try {
      await api.startBriefing(body);
    } catch (e) {
      if (!String(e).includes("409")) {
        alert(String(e));
        return;
      }
    }
    nav("/running");
  }

  return (
    <>
      <div className="view-header">
        <h2>Feed</h2>
        <p className="dim">Catch up on safety research since you last looked.</p>
      </div>

      <div className="sample-panel">
        <SampleLoader />
      </div>

      <div className="catchup-bar">
        <button className="btn-primary" onClick={() => run({ range: "month" })}>
          Catch me up
        </button>
        <span className="dim small">last month · ~10–15 min · ~$2–3 per run</span>
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
      {list && list.length === 0 && <p className="empty">No catch-ups yet. Click “Catch me up”.</p>}

      {list?.map((s, i) => <FeedSection key={s.id} summary={s} defaultOpen={i === 0} />)}
    </>
  );
}

// One catch-up in the timeline. Newest is open by default; others lazy-load on expand.
function FeedSection({ summary, defaultOpen }: { summary: BriefingSummary; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open && !digest) {
      setLoading(true);
      api.getBriefing(summary.id).then(setDigest).catch(() => {}).finally(() => setLoading(false));
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
