import { useEffect, useRef, useState } from "react";

type AmbiguousToolCallReconciliationProps = {
  canReconcile: boolean;
  busy: boolean;
  onReconcile: () => void;
};

export default function AmbiguousToolCallReconciliation({
  canReconcile,
  busy,
  onReconcile,
}: AmbiguousToolCallReconciliationProps) {
  const [confirming, setConfirming] = useState(false);
  const explanationRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!canReconcile) setConfirming(false);
  }, [canReconcile]);

  useEffect(() => {
    if (confirming) explanationRef.current?.focus();
  }, [confirming]);

  if (!canReconcile) {
    return <small>A task is still active. Wait for it to finish, then refresh before continuing.</small>;
  }

  if (!confirming) {
    return (
      <div className="approval-reconciliation">
        <button
          type="button"
          className="settings-secondary"
          disabled={busy}
          onClick={() => setConfirming(true)}
        >
          I checked the target
        </button>
      </div>
    );
  }

  return (
    <div className="approval-reconciliation" role="group" aria-label="Confirm external action outcome">
      <p ref={explanationRef} tabIndex={-1}>
        Continue only if you checked the external target and confirmed the action did not happen. This records your
        confirmation; AEGIS cannot verify, stop, or undo the external action.
      </p>
      <div className="approval-actions">
        <button type="button" className="approval-decline" disabled={busy} onClick={() => setConfirming(false)}>
          Keep unresolved
        </button>
        <button type="button" className="approval-approve" disabled={busy} onClick={onReconcile}>
          {busy ? "Recording…" : "Record as not performed"}
        </button>
      </div>
    </div>
  );
}
