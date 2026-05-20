import { useParams } from "react-router-dom";
import { api } from "../api";
import { useFetch } from "../useFetch";
import Catchup from "../components/Catchup";

export default function DigestView() {
  const { id } = useParams();
  const { data: d, loading, error } = useFetch(() => api.getDigest(id!), [id]);

  if (loading) return <p className="dim">Loading…</p>;
  if (error || !d) return <p className="empty">{error || "Not found"}</p>;

  return (
    <>
      <div className="view-header">
        <h2>{d.window_start} → {d.window_end}</h2>
        <p className="dim">generated {d.generated_at}</p>
      </div>
      <Catchup digest={d} />
    </>
  );
}
