import { useState } from "react";
import { createRoot } from "react-dom/client";

import AmbiguousToolCallReconciliation from "../../src/AmbiguousToolCallReconciliation";
import "../../src/styles.css";
import "../../src/desktop-theme.css";
import "../../src/desktop-workbench-v2.css";

const query = new URLSearchParams(window.location.search);
document.documentElement.dataset.theme = query.get("theme") === "light" ? "light" : "dark";
const canReconcile = query.get("active") !== "true";

function ReviewHarness() {
  const [recorded, setRecorded] = useState(false);

  return (
    <main style={{ minHeight: "100vh", padding: 24 }}>
      <div className="chat-column" style={{ maxWidth: 760 }}>
        {recorded ? (
          <p className="approval-reconciliation-feedback" role="status">
            Your confirmation was recorded. AEGIS did not independently verify or undo the external action.
          </p>
        ) : (
          <article className="approval-card" aria-label="External action outcome unclear">
            <div>
              <span className="eyebrow">REVIEW REQUIRED</span>
              <strong>fixture.external_write</strong>
            </div>
            <span className="status-chip warning"><i />AMBIGUOUS</span>
            <p>The previous session ended before the result was confirmed. The action may or may not have happened.</p>
            <AmbiguousToolCallReconciliation
              canReconcile={canReconcile}
              busy={false}
              onReconcile={() => setRecorded(true)}
            />
          </article>
        )}
      </div>
    </main>
  );
}

createRoot(document.getElementById("review-root")!).render(<ReviewHarness />);
