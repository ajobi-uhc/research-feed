import { Link } from "react-router-dom";
import { api } from "../api";
import { useFetch } from "../useFetch";

export default function Runs() {
  const { data, loading, error } = useFetch(api.listRuns);

  if (loading) return <p className="dim">Loading…</p>;
  if (error) return <p className="empty">{error}</p>;

  return (
    <>
      <div className="view-header">
        <h2>Runs</h2>
        <p className="dim">Saved agent runs — click one to see what each lane searched and the full trace.</p>
      </div>
      <div className="cards">
        {data?.map((r) => (
          <Link className="card run-link" to={`/runs/${r.id}`} key={r.id}>
            <div className="meta">
              <span className={`venue venue-${r.status === "done" ? "metr" : "arxiv"}`}>{r.kind}</span>
              <span>{r.status}</span>
              <span className="date">{r.started_at}</span>
              {r.window_start && <span className="dim">{r.window_start} → {r.window_end}</span>}
            </div>
          </Link>
        ))}
        {data && data.length === 0 && <p className="empty">No runs yet.</p>}
      </div>
    </>
  );
}
