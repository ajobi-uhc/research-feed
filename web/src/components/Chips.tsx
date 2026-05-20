import { useState } from "react";

// Editable list of short strings rendered as removable chips + an add input.
export default function Chips({
  items,
  onChange,
  placeholder = "add…",
}: {
  items: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");

  function add() {
    const v = draft.trim();
    if (v && !items.includes(v)) onChange([...items, v]);
    setDraft("");
  }

  return (
    <div className="chips">
      {items.map((it, i) => (
        <span className="chip" key={i}>
          {it}
          <button
            type="button"
            className="chip-x"
            title="remove"
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          >
            ×
          </button>
        </span>
      ))}
      <input
        className="chip-input"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}
