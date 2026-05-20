import { useState } from "react";
import { Digest } from "../api";
import { followedLastNames, sourceMix, weekBuckets } from "../util";
import ItemCard from "./ItemCard";

// One catch-up: a ranked list, sliceable by week. "Most important" shows the
// full month best-first; each week tab filters to items dated that week.
export default function Catchup({ digest }: { digest: Digest }) {
  const followed = followedLastNames((digest as any).profile_snapshot?.authors || []);
  const weeks = weekBuckets(digest.window_start, digest.window_end);
  const [tab, setTab] = useState<string>("top");

  const cov = digest.coverage;
  const mix = sourceMix(digest.kept);

  const ranked = digest.kept.map((it, i) => ({ it, rank: i + 1 }));
  const shown =
    tab === "top"
      ? ranked
      : ranked.filter(({ it }) => {
          const w = weeks.find((x) => x.from === tab);
          return w ? it.date >= w.from && it.date <= w.to : true;
        });

  return (
    <>
      {digest.kept.length > 0 && (
        <p className="coverage-note">
          {cov?.items_considered != null && (
            <>
              <strong>{cov.items_considered}</strong> considered →{" "}
              <strong>{cov.items_kept ?? digest.kept.length}</strong> kept
              {cov.items_dropped != null && <> · {cov.items_dropped} dropped</>}
              {" · "}
            </>
          )}
          drawn from: {mix.map((m) => `${m.label} ×${m.n}`).join(" · ")}
        </p>
      )}

      {weeks.length > 1 && (
        <div className="week-tabs">
          <button className={`week-tab${tab === "top" ? " active" : ""}`} onClick={() => setTab("top")}>
            Most important
          </button>
          {weeks.map((w) => (
            <button
              key={w.from}
              className={`week-tab${tab === w.from ? " active" : ""}`}
              onClick={() => setTab(w.from)}
            >
              {w.label}
            </button>
          ))}
        </div>
      )}

      {digest.kept.length === 0 && <p className="empty">Nothing surfaced this catch-up.</p>}

      <div className="cards">
        {shown.map(({ it, rank }) => (
          <ItemCard key={it.id} item={it} followed={followed} rank={rank} />
        ))}
      </div>
      {shown.length === 0 && digest.kept.length > 0 && <p className="dim small">Nothing dated in this week.</p>}

      {digest.dropped?.length > 0 && (
        <details className="mt-20">
          <summary className="dim small">Considered &amp; dropped ({digest.dropped.length})</summary>
          {digest.dropped.map((x, i) => (
            <div className="ledger-row" key={i}>
              {x.url ? (
                <a href={x.url} target="_blank" rel="noreferrer">{x.title}</a>
              ) : (
                x.title
              )}
              <span className="dim"> — {x.reason}</span>
            </div>
          ))}
        </details>
      )}
    </>
  );
}
