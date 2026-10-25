/* Console conveniences only: filter auto-submit and the SSE progress log. */
(() => {
  "use strict";

  const filterForm = document.querySelector("[data-filter-form]");
  if (filterForm) {
    for (const select of filterForm.querySelectorAll("select")) {
      select.addEventListener("change", () => filterForm.requestSubmit());
    }
  }

  const progress = document.querySelector("[data-sse-url]");
  if (!progress || !window.EventSource) {
    return;
  }
  const log = document.getElementById("event-log");
  const state = document.getElementById("stream-state");
  const placeholder = document.getElementById("event-placeholder");
  if (!log) {
    return;
  }

  const describe = (event) => {
    try {
      const data = JSON.parse(event.data);
      const payload = data.payload || {};
      const cell = payload.cell_id ? " " + payload.cell_id : "";
      const status = payload.status ? " (" + payload.status + ")" : "";
      return (data.kind || event.type) + cell + status;
    } catch (error) {
      return event.type;
    }
  };

  const append = (event) => {
    if (placeholder) {
      placeholder.remove();
    }
    const item = document.createElement("li");
    item.className = "event";
    const seq = document.createElement("span");
    seq.className = "mono subtle";
    seq.textContent = "#" + event.lastEventId;
    const text = document.createElement("span");
    text.textContent = " " + describe(event);
    item.append(seq, text);
    log.append(item);
    log.scrollTop = log.scrollHeight;
  };

  const source = new EventSource(progress.dataset.sseUrl);
  const kinds = [
    "run_started",
    "cell_started",
    "cell_completed",
    "cell_failed",
    "cell_cancelled",
    "budget_exceeded",
    "run_finished",
  ];
  for (const kind of kinds) {
    source.addEventListener(kind, (event) => {
      append(event);
      if (kind === "run_finished") {
        source.close();
        if (state) {
          state.textContent = "complete";
        }
      }
    });
  }
  source.addEventListener("error", () => {
    if (state) {
      state.textContent = "reconnecting";
    }
  });
})();
