import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Profile as P } from "../api";
import Chips from "../components/Chips";
import EntityList from "../components/EntityList";

export default function Profile() {
  const [p, setP] = useState<P | null>(null);
  const [base, setBase] = useState("");
  const [proposals, setProposals] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    const prof = await api.getProfile();
    setP(prof);
    setBase(JSON.stringify(prof));
    setProposals(await api.proposals());
  }
  useEffect(() => {
    load().catch((e) => setErr(String(e)));
  }, []);

  if (err) return <p className="empty">{err}</p>;
  if (!p) return <p className="dim">Loading…</p>;

  const dirty = JSON.stringify(p) !== base;
  function set<K extends keyof P>(k: K, v: P[K]) {
    setP((prev) => (prev ? { ...prev, [k]: v } : prev));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    try {
      const out = await api.putProfile(p!);
      setP(out);
      setBase(JSON.stringify(out));
      setSaved(true);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function accept(id: string) {
    await api.acceptProposal(id);
    await load();
  }
  async function reject(id: string) {
    await api.rejectProposal(id);
    setProposals((ps) => ps.filter((x) => x.id !== id));
  }

  return (
    <>
      <div className="view-header">
        <h2>Profile</h2>
        <p className="dim">The single thing that drives your feed. Edit, save, and the next catch-up reflects it.</p>
      </div>

      <p className="profile-restart">
        <Link className="btn-secondary" to="/onboarding">↻ Re-run onboarding</Link>
        <span className="dim small"> rebuild your profile and feed from scratch</span>
      </p>

      {proposals.length > 0 && (
        <div className="suggestions-panel">
          <h3>Proposed updates ({proposals.length})</h3>
          {proposals.map((pr) => (
            <div className="suggest-row" key={pr.id}>
              <span>
                <strong>{pr.kind}</strong>:{" "}
                {pr.kind === "source"
                  ? `${pr.payload.name} — ${pr.payload.url}`
                  : pr.kind === "author"
                    ? `${pr.payload.name}${pr.payload.affiliation ? ` (${pr.payload.affiliation})` : ""}`
                    : `${pr.payload.field} → ${pr.payload.proposed}`}
                <span className="dim small"> — {pr.rationale}</span>
              </span>
              <button className="chip" onClick={() => accept(pr.id)}>accept</button>
              <button className="link-btn" onClick={() => reject(pr.id)}>reject</button>
            </div>
          ))}
        </div>
      )}

      <p className="group-label">What we look for</p>
      <div className="edit-block">
        <h3>Interests</h3>
        <p className="small dim">Each becomes a paper search and a relevance signal for every lane. Up to 15.</p>
        <Chips items={p.interests} onChange={(v) => set("interests", v)} placeholder="add an interest…" />
      </div>
      <div className="edit-block">
        <h3>About you</h3>
        <p className="small dim">Every agent reads this to judge whether a result is genuinely for you.</p>
        <textarea rows={5} value={p.user_summary} onChange={(e) => set("user_summary", e.target.value)} />
      </div>
      <div className="edit-block">
        <h3>Current focus</h3>
        <p className="small dim">A soft lean — items touching it lead the feed, but it never excludes good on-topic work.</p>
        <textarea rows={2} value={p.current_question} onChange={(e) => set("current_question", e.target.value)} />
      </div>

      <p className="group-label">Where we look</p>
      <div className="edit-block">
        <h3>Followed authors</h3>
        <p className="small dim">Their work is boosted as high-signal wherever it appears.</p>
        <EntityList
          items={p.authors as any}
          fields={[
            { key: "name", label: "Name", flex: 2 },
            { key: "affiliation", label: "Affiliation", flex: 2 },
            { key: "why", label: "why", flex: 3 },
          ]}
          onChange={(v) => set("authors", v as any)}
          addLabel="+ add author"
        />
      </div>
      <div className="edit-block">
        <h3>Sources</h3>
        <p className="small dim">Lab/org pages the sources agent visits each run (it also roams beyond them).</p>
        <EntityList
          items={p.sources as any}
          fields={[
            { key: "url", label: "URL", flex: 3 },
            { key: "name", label: "Name", flex: 2 },
            { key: "why", label: "why", flex: 3 },
          ]}
          onChange={(v) => set("sources", v as any)}
          addLabel="+ add source"
        />
      </div>

      <p className="group-label">What to skip</p>
      <div className="edit-block">
        <h3>Filter out</h3>
        <p className="small dim">Hard excludes — matching items are always dropped.</p>
        <Chips items={p.filter_outs} onChange={(v) => set("filter_outs", v)} placeholder="add a filter…" />
      </div>
      <div className="edit-block">
        <h3>Notes</h3>
        <textarea rows={3} value={p.notes} onChange={(e) => set("notes", e.target.value)} />
      </div>

      {dirty && (
        <div className="save-bar">
          <span>Unsaved changes</span>
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="btn-secondary" onClick={() => setP(JSON.parse(base))}>Discard</button>
        </div>
      )}

      {saved && !dirty && (
        <div className="save-bar saved">
          <span>Saved ✓ — your next catch-up will use it.</span>
        </div>
      )}
    </>
  );
}
