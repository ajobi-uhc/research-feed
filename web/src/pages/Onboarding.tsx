import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import SampleLoader from "../components/SampleLoader";

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const commas = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function Onboarding() {
  const [seed, setSeed] = useState("");
  const [scholar, setScholar] = useState("");
  const [authors, setAuthors] = useState("");
  const [question, setQuestion] = useState("");
  const [filters, setFilters] = useState("");
  const [free, setFree] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nav = useNavigate();

  async function submit() {
    setSubmitting(true);
    try {
      const res = await api.startOnboarding({
        seed_papers: lines(seed),
        scholar_url: scholar.trim(),
        followed_authors: lines(authors),
        current_question: question.trim(),
        filter_outs: commas(filters),
        freeform: free.trim(),
      });
      nav(`/runs/${res.run_id}`);
    } catch (e) {
      alert(String(e));
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="view-header">
        <h2>Set up your feed</h2>
        <p className="dim">
          Tell us a few things you value. We'll build your profile, find the labs/authors worth
          tracking, and create your first digest — all in one go.
        </p>
      </div>

      <div className="sample-panel">
        <p className="small dim" style={{ margin: "0 0 8px" }}>
          Just exploring? Skip the ~15-min onboard and load a ready-made researcher's profile + feed.
        </p>
        <SampleLoader />
      </div>

      <div className="edit-block">
        <h3>Seed papers / posts</h3>
        <p className="small dim">3–5 URLs you recently found valuable. One per line.</p>
        <textarea rows={5} value={seed} onChange={(e) => setSeed(e.target.value)}
          placeholder="https://arxiv.org/abs/...&#10;https://transformer-circuits.pub/..." />
      </div>

      <div className="edit-block">
        <h3>Google Scholar profile (optional)</h3>
        <input type="text" value={scholar} onChange={(e) => setScholar(e.target.value)}
          placeholder="https://scholar.google.com/citations?user=..." />
      </div>

      <div className="edit-block">
        <h3>Authors you follow</h3>
        <p className="small dim">People whose work you read whenever it appears. One per line.</p>
        <textarea rows={4} value={authors} onChange={(e) => setAuthors(e.target.value)}
          placeholder="Chris Olah&#10;Neel Nanda" />
      </div>

      <div className="edit-block">
        <h3>What are you focused on right now?</h3>
        <p className="small dim">Optional — one sentence. We'll gently prioritize it, but won't hide your other interests.</p>
        <textarea rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} />
      </div>

      <div className="edit-block">
        <h3>Anything to filter out?</h3>
        <p className="small dim">Topics to hard-exclude. Comma-separated.</p>
        <input type="text" value={filters} onChange={(e) => setFilters(e.target.value)}
          placeholder="AI governance, RLHF methodology" />
      </div>

      <div className="edit-block">
        <h3>Anything else? (optional)</h3>
        <p className="small dim">Freeform context — taste, what you care about, what you don't.</p>
        <textarea rows={3} value={free} onChange={(e) => setFree(e.target.value)} />
      </div>

      <div className="profile-actions">
        <button className="btn-primary" onClick={submit} disabled={submitting || !seed.trim()}>
          {submitting ? "Starting…" : "Build my profile & first digest"}
        </button>
        <span className="dim small">~15 min · ~$3–4 — we'll stream progress as it runs.</span>
      </div>
    </>
  );
}
