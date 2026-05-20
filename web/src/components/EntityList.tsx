type Field = { key: string; label: string; flex?: number };
type Row = Record<string, string>;

// Editable list of structured rows (authors, sources) rendered as inline cards.
export default function EntityList({
  items,
  fields,
  onChange,
  addLabel,
}: {
  items: Row[];
  fields: Field[];
  onChange: (next: Row[]) => void;
  addLabel: string;
}) {
  function update(i: number, key: string, val: string) {
    onChange(items.map((it, j) => (j === i ? { ...it, [key]: val } : it)));
  }
  function remove(i: number) {
    onChange(items.filter((_, j) => j !== i));
  }
  function add() {
    onChange([...items, Object.fromEntries(fields.map((f) => [f.key, ""]))]);
  }

  return (
    <div className="entity-list">
      {items.map((it, i) => (
        <div className="entity-card" key={i}>
          {fields.map((f) => (
            <input
              key={f.key}
              className="entity-input"
              style={{ flex: f.flex ?? 1 }}
              placeholder={f.label}
              value={it[f.key] || ""}
              onChange={(e) => update(i, f.key, e.target.value)}
            />
          ))}
          <button type="button" className="entity-del" title="remove" onClick={() => remove(i)}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="btn-secondary" onClick={add}>
        {addLabel}
      </button>
    </div>
  );
}
