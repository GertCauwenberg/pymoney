import type { ReconcileState } from "@pymoney/core";
import { useT } from "@pymoney/core/hooks";

const MARKS: Record<ReconcileState, { mark: string; label: string }> = {
  NOT_RECONCILED: { mark: "", label: "Not reconciled" },
  CLEARED: { mark: "C", label: "Cleared" },
  RECONCILED: { mark: "R", label: "Reconciled" },
  FROZEN: { mark: "F", label: "Frozen" },
};

/** The one-letter column every ledger has had since paper. */
export function ReconcileMark({ state }: { state: ReconcileState }) {
  const t = useT();
  const { mark, label: key } = MARKS[state];
  const label = t(key);
  return (
    <span className={`reconcile reconcile--${state.toLowerCase()}`} title={label} aria-label={label}>
      {mark}
    </span>
  );
}
