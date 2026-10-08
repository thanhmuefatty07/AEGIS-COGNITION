import { useState, type UIEvent } from "react";
import { CapabilityIcon } from "./capability-icons";
import type { ConversationInspection } from "./protocol";
import { virtualWindow } from "./virtual_window.js";

const ROW_HEIGHT = 64;

type TimelineItem = ConversationInspection["timeline"][number];

export default function VirtualizedTimeline({ items }: { items: TimelineItem[] }) {
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(360);
  const window = virtualWindow(items.length, scrollTop, viewportHeight, ROW_HEIGHT, 6);

  function updateScroll(event: UIEvent<HTMLDivElement>) {
    setScrollTop(event.currentTarget.scrollTop);
    setViewportHeight(event.currentTarget.clientHeight);
  }

  return (
    <div className="activity-feed" role="list" aria-label="Task timeline" tabIndex={0} onScroll={updateScroll}>
      <div className="activity-feed-spacer" style={{ height: window.totalHeight }}>
        {items.slice(window.start, window.end).map((item, offset) => {
          const index = window.start + offset;
          return (
            <article
              className="activity-feed-row"
              role="listitem"
              aria-posinset={index + 1}
              aria-setsize={items.length}
              key={item.event_id}
              style={{ top: index * ROW_HEIGHT, height: ROW_HEIGHT }}
            >
              <span className="activity-icon"><CapabilityIcon name={item.status === "COMPLETED" ? "check" : "activity"} size={13} /></span>
              <div>
                <strong>{item.title} · {item.status}</strong>
                <small>{item.detail}</small>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
