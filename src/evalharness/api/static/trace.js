/* Trace replay. The stored events are the source of truth; timing is scaled
   so a recorded run can be watched without waiting for the original wall
   clock. No model is called and no token is spent. */
(() => {
  "use strict";

  const panel = document.getElementById("replay-panel");
  const payloadNode = document.getElementById("replay-events");
  if (!panel || !payloadNode) return;

  const log = document.getElementById("replay-log");
  const fill = document.getElementById("replay-fill");
  const track = document.getElementById("replay-track");
  const status = document.getElementById("replay-status");
  if (!log || !fill || !track || !status) return;

  let events = [];
  try {
    events = JSON.parse(payloadNode.textContent || "[]");
  } catch (error) {
    status.textContent = "Replay data could not be read; the trace below is still complete.";
    panel.hidden = false;
    return;
  }
  if (!Array.isArray(events) || events.length === 0) {
    status.textContent = "This run has no stored events to replay.";
    panel.hidden = false;
    return;
  }

  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function clock(value) {
    const parsed = Date.parse(value || "");
    if (!Number.isFinite(parsed)) return "--:--:--";
    return new Date(parsed).toISOString().slice(11, 19);
  }

  function compact(value) {
    if (typeof value === "number") {
      return Number.isInteger(value) ? String(value) : value.toFixed(6);
    }
    if (value === null || value === undefined) return "";
    if (typeof value === "object") {
      try {
        return JSON.stringify(value);
      } catch (error) {
        return "[unreadable]";
      }
    }
    return String(value);
  }

  function describe(event) {
    const body = event.payload || {};
    switch (event.kind) {
      case "run_started":
        return "Run started: " + compact(body.n_cells) + " cells planned";
      case "run_resumed":
        return "Run resumed: " + compact(body.n_remaining) + " cells remaining";
      case "cell_started":
        return (
          compact(body.case_id) +
          " · " +
          compact(body.candidate) +
          " started (replica " +
          compact(body.replica) +
          ")"
        );
      case "cell_completed":
        return (
          compact(body.cell_id) +
          " completed: " +
          compact(body.passed) +
          " checks passed, " +
          compact(body.wall_ms) +
          " ms"
        );
      case "cell_failed":
        return (
          compact(body.cell_id) +
          " failed: " +
          compact(body.error_type) +
          " · " +
          compact(body.error)
        );
      case "cell_cancelled":
        return compact(body.cell_id) + " cancelled: " + compact(body.reason);
      case "run_finished":
        return (
          "Run finished: " +
          compact(body.status) +
          " · " +
          compact(body.n_ok) +
          "/" +
          compact(body.n_cells) +
          " cells ok"
        );
      case "run_cancelled":
        return "Run cancelled: " + compact(body.reason);
      case "budget_exceeded":
        return "Budget exceeded: spent " + compact(body.spend_usd);
      default: {
        const pairs = Object.keys(body)
          .slice(0, 4)
          .map((key) => key + "=" + compact(body[key]));
        return event.kind + (pairs.length ? " · " + pairs.join(" · ") : "");
      }
    }
  }

  function append(event) {
    const row = document.createElement("li");
    row.className = "replay-event";
    const time = document.createElement("time");
    time.className = "replay-time";
    time.textContent = clock(event.ts);
    const kind = document.createElement("span");
    kind.className = "replay-kind";
    kind.textContent = event.kind || "event";
    const text = document.createElement("span");
    text.className = "replay-text";
    text.textContent = describe(event);
    row.append(time, kind, text);
    log.append(row);
  }

  function setProgress(done) {
    const ratio = done / events.length;
    fill.style.transform = "scaleX(" + ratio + ")";
    track.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
  }

  function finish() {
    setProgress(events.length);
    status.textContent =
      "Replay complete · " + events.length + " recorded events · timings simulated.";
  }

  panel.hidden = false;
  if (reducedMotion) {
    events.forEach(append);
    finish();
    return;
  }

  const stamps = events.map((event) => {
    const parsed = Date.parse(event.ts || "");
    return Number.isFinite(parsed) ? parsed : null;
  });
  const first = stamps.find((stamp) => stamp !== null) || 0;
  const offsets = stamps.map((stamp) => (stamp === null ? 0 : Math.max(0, stamp - first)));
  const span = Math.max(1, Math.max(...offsets));
  const windowMs = Math.min(6000, Math.max(1200, events.length * 180));

  let index = 0;
  function step() {
    append(events[index]);
    index += 1;
    setProgress(index);
    if (index >= events.length) {
      finish();
      return;
    }
    const previous = offsets[index - 1];
    const next = offsets[index];
    const delay = Math.max(35, ((next - previous) / span) * windowMs);
    window.setTimeout(step, delay);
  }
  status.textContent = "Replaying " + events.length + " recorded events…";
  step();
})();
