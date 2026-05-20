import { useEffect, useState } from "react";
import { api } from "../api";

// Load an eval-generated profile + feed so people can explore without onboarding.
export default function SampleLoader() {
  const [samples, setSamples] = useState<{ persona: string; label: string }[]>([]);
  const [loading, setLoading] = useState("");

  useEffect(() => {
    api.listSamples().then(setSamples).catch(() => {});
  }, []);

  if (samples.length === 0) return null;

  async function load(persona: string) {
    // If the user has their own profile (onboarded or hand-edited — anything but
    // an already-loaded sample), loading a sample would replace it. Confirm first.
    const prof = await api.getProfile().catch(() => null);
    if (prof && prof.origin !== "sample") {
      const ok = window.confirm(
        "Loading a sample replaces your current profile and feed — your onboarding will be lost. Continue?",
      );
      if (!ok) return;
    }
    setLoading(persona);
    try {
      await api.loadSample(persona);
      window.location.href = "/"; // full reload → fresh profile + feed
    } catch (e) {
      alert(String(e));
      setLoading("");
    }
  }

  return (
    <div className="sample-loader">
      <span className="dim small">Explore a sample researcher:</span>
      {samples.map((s) => (
        <button key={s.persona} className="btn-secondary" disabled={!!loading} onClick={() => load(s.persona)}>
          {loading === s.persona ? "Loading…" : <><strong>{s.persona}</strong> · {s.label}</>}
        </button>
      ))}
    </div>
  );
}
