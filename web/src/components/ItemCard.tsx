import { useState } from "react";
import { api, Item } from "../api";
import { lastName, venueLabel } from "../util";

// One ranked result: rank · title · meta · expandable why + abstract + feedback.
// Feedback becomes a *proposed profile edit* (no hidden state) — it lands on the
// Profile page as an accept/reject proposal.
export default function ItemCard({
  item,
  followed,
  rank,
}: {
  item: Item;
  followed: Set<string>;
  rank?: number;
}) {
  const [show, setShow] = useState<"" | "why" | "abstract">("");
  const [mode, setMode] = useState<"" | "reason">("");
  const [term, setTerm] = useState("");
  const [msg, setMsg] = useState("");
  const isFollowed = (item.authors || []).some((a) => followed.has(lastName(a)));
  const firstAuthor = item.authors?.[0];

  async function submitReason() {
    const t = term.trim();
    if (!t) return;
    // The reason only makes sense tied to the specific post — record both.
    await api.addNote(`Found “${item.title}” less relevant — ${t}`);
    setMode("");
    setTerm("");
    setMsg("✓ noted on your profile");
  }
  async function followAuthor() {
    if (!firstAuthor) return;
    await api.propose("author", { name: firstAuthor, affiliation: "", why: "from feedback" }, `feedback on “${item.title}”`);
    setMsg(`✓ proposed following ${firstAuthor} — review on Profile`);
  }

  return (
    <div className="card">
      <div className="card-head">
        {rank != null && <span className="rank">{rank}</span>}
        <a className="title" href={item.url} target="_blank" rel="noreferrer">
          {item.title}
        </a>
      </div>
      <div className="meta">
        {item.date && <span className="date">{item.date}</span>}
        {item.venue && <span className={`venue venue-${item.venue}`}>{venueLabel(item.venue)}</span>}
        {item.venue_detail && item.venue_detail !== venueLabel(item.venue) && (
          <span className="pub-venue">{item.venue_detail}</span>
        )}
        {item.karma != null && <span className="karma">▲{item.karma}</span>}
        {isFollowed && <span className="badge-followed">followed author</span>}
        {item.authors?.length > 0 && (
          <span className="dim">
            {item.authors.slice(0, 3).join(", ")}
            {item.authors.length > 3 ? " et al." : ""}
          </span>
        )}
      </div>

      <div className="card-foot">
        {item.why_kept && (
          <button className="link-btn" onClick={() => setShow(show === "why" ? "" : "why")}>
            why
          </button>
        )}
        {item.summary && (
          <button className="link-btn" onClick={() => setShow(show === "abstract" ? "" : "abstract")}>
            abstract
          </button>
        )}
        <button className="link-btn" onClick={() => setMode(mode === "reason" ? "" : "reason")}>
          less like this
        </button>
        {firstAuthor && !isFollowed && (
          <button className="link-btn" onClick={followAuthor}>follow {lastName(firstAuthor)}</button>
        )}
        {msg && <span className="dim small fb-msg">{msg}</span>}
      </div>

      {show === "why" && item.why_kept && <p className="reason">{item.why_kept}</p>}
      {show === "abstract" && item.summary && <p className="description">{item.summary}</p>}

      {mode === "reason" && (
        <div className="fb-filter">
          <input
            type="text"
            placeholder="why less relevant? e.g. too theoretical, off-topic, already knew it"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitReason()}
          />
          <button className="btn-secondary" onClick={submitReason}>add to notes</button>
        </div>
      )}
    </div>
  );
}
