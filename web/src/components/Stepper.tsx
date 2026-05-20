import { Stages, StageStatus } from "../api";

const ORDER = ["onboarding", "papers", "forum", "sources", "curating", "done"];
const LABEL: Record<string, string> = {
  onboarding: "Building profile",
  papers: "Papers",
  forum: "Forum",
  sources: "Sources",
  curating: "Curating",
  done: "Done",
};
const ICON: Record<StageStatus, string> = { pending: "○", running: "◍", done: "●", error: "✕" };

// Progress stepper: shows which pipeline stages are pending/running/done.
// The three discovery lanes run in parallel, so they light up independently.
export default function Stepper({ stages }: { stages: Stages }) {
  const present = ORDER.filter((k) => k in stages);
  if (present.length === 0) return null;
  return (
    <div className="stepper">
      {present.map((k) => {
        const st = stages[k].status;
        return (
          <div className={`step step-${st}`} key={k}>
            <span className="step-icon">{ICON[st]}</span>
            <span className="step-label">{LABEL[k] || k}</span>
            {stages[k].detail && <span className="step-detail">{stages[k].detail}</span>}
          </div>
        );
      })}
    </div>
  );
}
