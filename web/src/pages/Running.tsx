import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Stages } from "../api";
import Stepper from "../components/Stepper";

// Live run view: stage stepper on top, streaming agent log below.
export default function Running() {
  const [lines, setLines] = useState<string[]>([]);
  const [stages, setStages] = useState<Stages>({});
  const nav = useNavigate();
  const boxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const es = new EventSource("/api/run/stream");
    es.onmessage = (e) => setLines((l) => [...l, e.data]);
    es.addEventListener("stage", (e) => {
      try {
        setStages(JSON.parse((e as MessageEvent).data));
      } catch {
        /* ignore */
      }
    });
    es.addEventListener("done", (e) => {
      es.close();
      nav((e as MessageEvent).data || "/");
    });
    es.addEventListener("failed", (e) => {
      es.close();
      setLines((l) => [...l, "FAILED: " + (e as MessageEvent).data]);
    });
    return () => es.close();
  }, [nav]);

  useEffect(() => {
    boxRef.current?.scrollTo(0, boxRef.current.scrollHeight);
  }, [lines]);

  return (
    <>
      <div className="view-header">
        <h2>Running…</h2>
        <p className="dim">Live agent activity. Onboarding ~1–3 min · a catch-up ~10–15 min.</p>
      </div>

      <Stepper stages={stages} />

      <details className="mt-12">
        <summary className="dim small">Agent log</summary>
        <pre className="log-box tall" ref={boxRef}>{lines.join("\n")}</pre>
      </details>
    </>
  );
}
