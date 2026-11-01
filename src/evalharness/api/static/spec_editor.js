/* Spec editor progressive enhancement: debounced validation against the same
   JSON route the CLI's validator backs, a dirty marker and Ctrl/Cmd+S.
   Without this file the Check and Save buttons still post the form, so the
   editor stays fully usable. */
(function () {
  "use strict";

  const form = document.querySelector("[data-spec-editor]");
  if (!form) return;

  const textarea = form.querySelector("#yaml");
  const panel = form.querySelector("[data-error-panel]");
  const save = form.querySelector("[data-save-button]");
  const checkButton = form.querySelector('button[value="check"]');
  const dirty = document.querySelector("[data-dirty]");
  const url = form.getAttribute("data-validate-url");

  const cells = document.querySelector("[data-cells]");
  const cases = document.querySelector("[data-cases]");
  const cost = document.querySelector("[data-cost]");
  const perCandidate = document.querySelector("[data-per-candidate]");
  const warnings = document.querySelector("[data-warnings]");
  const status = document.querySelector("[data-editor-status]");

  let timer = null;
  let sequence = 0;

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function setStatus(valid) {
    if (!status) return;
    status.className = "status " + (valid ? "status-ok" : "status-error");
    const glyph = status.querySelector(".status-glyph");
    const labels = status.querySelectorAll("span:not(.status-glyph)");
    if (glyph) glyph.textContent = valid ? "✓" : "✕";
    if (labels.length) labels[0].textContent = valid ? "valid" : "invalid";
  }

  function renderErrors(errors) {
    if (!panel) return;
    panel.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = errors.length ? "Validation errors" : "Validation";
    panel.append(heading);
    if (!errors.length) {
      const clear = document.createElement("p");
      clear.className = "subtle";
      clear.textContent = "No validation errors.";
      panel.append(clear);
      return;
    }
    const list = document.createElement("ul");
    list.className = "error-list";
    for (const error of errors) {
      const item = document.createElement("li");
      item.className = "field-error";
      item.dataset.field = error.field;
      const field = document.createElement("code");
      field.className = "field-name";
      field.textContent = error.field;
      item.append(field, document.createTextNode(" = " + error.value + ": " + error.reason + " "));
      const fix = document.createElement("span");
      fix.className = "fix";
      fix.textContent = "Fix: " + error.fix;
      item.append(fix);
      list.append(item);
    }
    panel.append(list);
  }

  function renderEstimate(payload) {
    const estimate = payload.estimate || payload.cost_estimate;
    const counts = payload.cells_per_candidate || {};
    setText(cells, estimate ? String(payload.cells) : "-");
    setText(cases, payload.cases ? String(payload.cases) : "-");
    setText(cost, estimate ? estimate.cost_text : "-");
    if (cost) cost.title = estimate ? estimate.cost_exact : "";
    if (perCandidate) {
      perCandidate.replaceChildren();
      for (const name of Object.keys(counts)) {
        const item = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = name;
        const value = document.createElement("span");
        value.className = "num";
        value.textContent = String(counts[name]);
        item.append(label, value);
        perCandidate.append(item);
      }
    }
    if (warnings) {
      warnings.replaceChildren();
      for (const warning of payload.warnings || []) {
        const item = document.createElement("li");
        item.textContent = warning;
        warnings.append(item);
      }
      warnings.hidden = warnings.children.length === 0;
    }
  }

  function render(payload) {
    const valid = payload.valid === true;
    renderErrors(payload.errors || []);
    renderEstimate(payload);
    if (save) save.disabled = !valid;
    if (textarea) textarea.setAttribute("aria-invalid", valid ? "false" : "true");
    setStatus(valid);
  }

  function check() {
    const current = ++sequence;
    form.setAttribute("aria-busy", "true");
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: textarea.value,
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (current === sequence) render(payload);
      })
      .catch(function () {
        /* The form still posts normally when the check cannot run. */
      })
      .finally(function () {
        if (current === sequence) form.removeAttribute("aria-busy");
      });
  }

  textarea.addEventListener("input", function () {
    if (dirty) dirty.hidden = false;
    window.clearTimeout(timer);
    timer = window.setTimeout(check, 400);
  });

  document.addEventListener("keydown", function (event) {
    if (!(event.ctrlKey || event.metaKey)) return;
    if (event.key.toLowerCase() !== "s") return;
    event.preventDefault();
    if (save && !save.disabled) {
      save.click();
    } else if (checkButton) {
      checkButton.click();
    }
  });
})();
