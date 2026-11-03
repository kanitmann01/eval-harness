/* Console conveniences only: copy affordances, confirmations, filter submit,
   estimate refresh and the SSE progress treatment. Every page still works
   without this file; no form depends on it. */
(() => {
  "use strict";

  const copyStatus = document.getElementById("copy-status");

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("copy failed");
  }

  const copyButtons = document.querySelectorAll("[data-copy]");
  for (const button of copyButtons) {
    button.hidden = false;
    button.addEventListener("click", async () => {
      const value = button.dataset.copy || "";
      try {
        await copyText(value);
        const object =
          button.dataset.copyLabel ||
          button.textContent.trim().replace(/^Copy\s+/i, "") ||
          "value";
        if (copyStatus) copyStatus.textContent = "Copied " + object + " " + value;
        button.dataset.copied = "true";
        if (!button.classList.contains("copy-button")) {
          const previous = button.textContent;
          button.textContent = "Copied";
          window.setTimeout(() => { button.textContent = previous; }, 1400);
        }
      } catch (error) {
        if (copyStatus) copyStatus.textContent = "Copy failed; select the text manually.";
      }
    });
  }

  for (const form of document.querySelectorAll("[data-confirm]")) {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }

  const filterForm = document.querySelector("[data-filter-form]");
  if (filterForm) {
    for (const select of filterForm.querySelectorAll("select")) {
      select.addEventListener("change", () => filterForm.requestSubmit());
    }
  }

  const wizardAdd = document.querySelector('[data-wizard-add="case"]');
  const caseRows = document.querySelector("[data-case-rows]");
  if (wizardAdd && caseRows) {
    wizardAdd.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey) return;
      const rows = [...caseRows.querySelectorAll("tr")];
      if (!rows.length) return;
      const source = rows[rows.length - 1];
      const index =
        rows.reduce((max, row) => Math.max(max, Number(row.dataset.rowIndex || "0")), 0) + 1;
      const clone = source.cloneNode(true);
      clone.dataset.rowIndex = String(index);
      clone.classList.remove("is-invalid");
      for (const field of clone.querySelectorAll("input, textarea, select")) {
        const name = field.getAttribute("name") || "";
        field.setAttribute("name", name.replace(/_\d+_/, "_" + index + "_"));
        if (field.id) field.id = field.id.replace(/_\d+_/, "_" + index + "_");
        field.value = "";
        field.removeAttribute("aria-invalid");
        field.removeAttribute("aria-describedby");
        field.removeAttribute("aria-errormessage");
      }
      for (const node of clone.querySelectorAll("[id$='-error']")) node.remove();
      let remove = clone.querySelector("[data-wizard-remove]");
      if (!remove) {
        remove = document.createElement("button");
        remove.type = "submit";
        remove.className = "icon-btn btn btn-secondary";
        remove.name = "action";
        remove.setAttribute("data-wizard-remove", "");
        remove.textContent = "✕";
        const cell = clone.querySelector("td:last-child");
        if (cell) cell.append(remove);
      }
      remove.value = "remove:case:" + index;
      remove.setAttribute("aria-label", "Remove case " + (index + 1));
      caseRows.append(clone);
      event.preventDefault();
      const first = clone.querySelector("input, textarea, select");
      if (first) first.focus();
    });
  }

  const estimateSelect = document.querySelector("[data-estimate-select]");
  if (estimateSelect) {
    estimateSelect.addEventListener("change", () => {
      const url = new URL(window.location.href);
      url.searchParams.set("spec", estimateSelect.value);
      window.location.assign(url.toString());
    });
  }

  const helpButtons = [...document.querySelectorAll("[data-help-tip]")];
  function closeHelp(button, refocus) {
    button.setAttribute("aria-expanded", "false");
    button.parentElement.classList.add("is-dismissed");
    if (refocus) button.focus();
  }
  for (const button of helpButtons) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = button.getAttribute("aria-expanded") === "true";
      for (const other of helpButtons) {
        other.setAttribute("aria-expanded", "false");
        other.parentElement.classList.remove("is-dismissed");
      }
      button.setAttribute("aria-expanded", open ? "false" : "true");
      if (open) button.parentElement.classList.add("is-dismissed");
    });
    button.addEventListener("blur", () => {
      button.parentElement.classList.remove("is-dismissed");
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const button of helpButtons) {
      if (button.getAttribute("aria-expanded") === "true") closeHelp(button, true);
    }
  });
  document.addEventListener("pointerdown", (event) => {
    for (const button of helpButtons) {
      if (button.parentElement.contains(event.target)) continue;
      const pinned =
        button.getAttribute("aria-expanded") === "true" ||
        button === document.activeElement;
      button.setAttribute("aria-expanded", "false");
      if (pinned) button.parentElement.classList.add("is-dismissed");
    }
  });

  for (const form of document.querySelectorAll("[data-loading-form]")) {
    form.addEventListener("submit", () => {
      const selector = form.dataset.loadingTarget;
      const target = selector ? document.querySelector(selector) : null;
      if (!target) return;
      target.setAttribute("aria-busy", "true");
      const loaded = target.querySelector("[data-loaded-rows]");
      const skeleton = target.querySelector("[data-skeleton-rows]");
      if (loaded) loaded.hidden = true;
      if (skeleton) skeleton.hidden = false;
      const status = target.querySelector("[data-loading-status]");
      if (status) status.textContent = form.dataset.loadingLabel || "Loading";
    });
  }
  window.addEventListener("pageshow", () => {
    for (const target of document.querySelectorAll("[data-loading-region]")) {
      target.setAttribute("aria-busy", "false");
      const loaded = target.querySelector("[data-loaded-rows]");
      const skeleton = target.querySelector("[data-skeleton-rows]");
      if (loaded) loaded.hidden = false;
      if (skeleton) skeleton.hidden = true;
      const status = target.querySelector("[data-loading-status]");
      if (status) status.textContent = "";
    }
  });

  const progress = document.querySelector("[data-sse-url]");
  if (!progress || !window.EventSource) {
    return;
  }
  const log = document.getElementById("event-log");
  const state = document.getElementById("stream-state");
  const label = document.getElementById("stream-label");
  const count = document.getElementById("progress-count");
  const fill = document.getElementById("progress-fill");
  const track = document.getElementById("progress-track");
  const placeholder = document.getElementById("event-placeholder");
  if (!log) {
    return;
  }

  const total = Number(progress.dataset.totalCells || "0");
  const initial = Number(progress.dataset.doneCells || "0");
  const seen = new Set();
  let done = initial;

  const setStream = (text, live) => {
    if (label) label.textContent = text;
    if (state) state.classList.toggle("is-live", Boolean(live));
  };

  const updateProgress = () => {
    done = Math.max(initial, seen.size);
    if (count) {
      count.replaceChildren(
        document.createTextNode(String(done) + " "),
        Object.assign(document.createElement("small"), {
          textContent: "of " + total + " cells",
        })
      );
    }
    if (fill && total > 0) {
      fill.style.transform = "scaleX(" + Math.min(1, done / total) + ")";
    }
    if (track) {
      track.setAttribute("aria-valuemax", String(total > 0 ? total : 1));
      track.setAttribute("aria-valuenow", String(done));
    }
  };

  const parse = (event) => {
    try {
      return JSON.parse(event.data);
    } catch (error) {
      return { kind: event.type, payload: {} };
    }
  };

  const noun = (count, singular) =>
    count + " " + singular + (Number(count) === 1 ? "" : "s");

  const describe = (data) => {
    const payload = data.payload || {};
    const cell = payload.cell_id ? payload.cell_id.slice(0, 8) : "";
    const passed = typeof payload.passed === "number" ? " · " + payload.passed + " passed" : "";
    switch (data.kind) {
      case "run_started":
        return "run started · " + noun(payload.n_cells || total, "cell");
      case "run_resumed":
        return "run resumed · " + (payload.n_remaining || 0) + " remaining";
      case "cell_completed":
        return "cell " + cell + " completed" + passed;
      case "cell_failed":
        return "cell " + cell + " errored";
      case "cell_cancelled":
        return "cell " + cell + " cancelled";
      case "budget_exceeded":
        return "budget exceeded · the run stopped at its cap";
      case "run_cancelled":
        return "run cancelled";
      case "run_finished":
        return (
          "run finished · " +
          (payload.n_ok || 0) +
          " of " +
          noun(payload.n_cells || total, "cell") +
          " passed"
        );
      default:
        return data.kind || "event";
    }
  };

  // One chip per row. The text already says "cancelled" or names the error,
  // so only these three kinds carry a state chip beside it.
  const chipFor = (kind, payload) => {
    if (kind === "cell_completed") return ["status-ok", "ok"];
    if (kind === "cell_failed") return ["status-error", "error"];
    if (kind === "run_finished") return ["status-" + (payload.status || "ok"), payload.status || "ok"];
    return null;
  };

  const append = (event) => {
    const data = parse(event);
    if (placeholder) placeholder.remove();
    if (data.payload && data.payload.cell_id && data.kind.startsWith("cell_")) {
      seen.add(data.payload.cell_id);
    }
    const item = document.createElement("li");
    item.className = "event-row";
    const seq = document.createElement("span");
    seq.className = "event-seq";
    seq.textContent = "#" + (event.lastEventId || data.seq || "");
    const text = document.createElement("span");
    text.className = "event-kind";
    text.textContent = describe(data);
    if (data.payload && data.payload.error) {
      text.title = String(data.payload.error);
    }
    item.append(seq, text);
    const chip = chipFor(data.kind, data.payload || {});
    if (chip) {
      const badge = document.createElement("span");
      badge.className = "status " + chip[0];
      badge.textContent = chip[1];
      item.append(badge);
    }
    if (data.payload && data.payload.error_type) {
      const note = document.createElement("span");
      note.className = "event-note";
      note.textContent = data.payload.error_type;
      item.append(note);
    }
    log.append(item);
    log.scrollTop = log.scrollHeight;
    updateProgress();
    if (data.kind === "run_started") setStream("streaming", true);
    if (data.kind === "budget_exceeded") setStream("budget exceeded", false);
  };

  const source = new EventSource(progress.dataset.sseUrl);
  const kinds = [
    "run_started",
    "run_resumed",
    "cell_completed",
    "cell_failed",
    "cell_cancelled",
    "budget_exceeded",
    "run_cancelled",
    "run_finished",
  ];
  for (const kind of kinds) {
    source.addEventListener(kind, (event) => {
      append(event);
      if (kind === "run_finished") {
        source.close();
        if (track) track.classList.remove("is-running");
        setStream("complete", false);
      }
    });
  }
  source.addEventListener("error", () => {
    if (progress.dataset.runStatus === "running") {
      setStream("reconnecting", true);
    } else {
      setStream("replay complete", false);
      source.close();
    }
  });
})();
