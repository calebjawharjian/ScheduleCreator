import {
  DndContext, PointerSensor, useDraggable, useDroppable,
  useSensor, useSensors,
} from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import type { ReactNode } from "react";
import { capitalize } from "../format";
import { moveIntoGap, moveIntoTier } from "../weights";
import type { FactorMeta } from "../types";

interface Props {
  factors: FactorMeta[];
  tiers: string[][];
  onChange: (tiers: string[][]) => void;
}

/**
 * Weights are relative, not absolute (see Preferences.weights in scoring.py),
 * so asking for a *rank* is a more honest control than a slider: a slider
 * implies "how much," but all that ever reaches the backend is "more than
 * what." Ties are first-class -- dropping one factor onto another's row
 * declares them equally important -- because forcing a strict order onto
 * things the user genuinely doesn't want to distinguish would be lying about
 * a preference they don't hold.
 */
export function WeightRanking({ factors, tiers, onChange }: Props) {
  const sensors = useSensors(useSensor(PointerSensor, {
    activationConstraint: { distance: 4 },
  }));
  const labels = Object.fromEntries(factors.map((f) => [f.name, f.label]));

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const name = String(active.id);
    const overId = String(over.id);
    if (overId.startsWith("tier:")) {
      onChange(moveIntoTier(tiers, name, Number(overId.slice(5))));
    } else if (overId.startsWith("gap:")) {
      onChange(moveIntoGap(tiers, name, Number(overId.slice(4))));
    }
  };

  return (
    <DndContext sensors={sensors} onDragEnd={onDragEnd}>
      <div className="tier-list">
        <Gap index={0} />
        {tiers.map((tier, index) => (
          <div key={tier.join("+")}>
            <Tier index={index} rank={index + 1}>
              {tier.map((name) => (
                <Chip key={name} name={name} label={capitalize(labels[name] ?? name)} />
              ))}
            </Tier>
            <Gap index={index + 1} />
          </div>
        ))}
      </div>
    </DndContext>
  );
}

function Tier({ index, rank, children }:
  { index: number; rank: number; children: ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: `tier:${index}` });
  return (
    <div ref={setNodeRef} className={isOver ? "tier over" : "tier"}>
      <span className="tier-rank">{rank}</span>
      <div className="tier-chips">{children}</div>
    </div>
  );
}

function Gap({ index }: { index: number }) {
  const { setNodeRef, isOver } = useDroppable({ id: `gap:${index}` });
  return <div ref={setNodeRef} className={isOver ? "tier-gap over" : "tier-gap"} />;
}

function Chip({ name, label }: { name: string; label: string }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: name });
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined;
  return (
    <button
      type="button"
      ref={setNodeRef}
      className={isDragging ? "weight-chip dragging" : "weight-chip"}
      style={style}
      {...listeners}
      {...attributes}
    >
      {label}
    </button>
  );
}
