/* First-visit coach marks. Enhancement only: without this file the console
   works and the tour stays hidden. Seen state is a local preference, not a
   server record. */
(() => {
  "use strict";

  const STORAGE_KEY = "evalh:console-tour:v1";
  const root = document.getElementById("tour");
  if (!root) return;

  let alreadySeen = false;
  try {
    alreadySeen = window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch (error) {
    alreadySeen = false;
  }
  if (alreadySeen) {
    root.remove();
    return;
  }

  const bubble = root.querySelector(".tour-bubble");
  const steps = Array.from(root.querySelectorAll(".tour-step"));
  if (!bubble || steps.length === 0) {
    root.remove();
    return;
  }

  const progress = document.getElementById("tour-progress");
  const next = document.getElementById("tour-next");
  const back = document.getElementById("tour-back");
  const skip = document.getElementById("tour-skip");
  if (!progress || !next || !back || !skip) {
    root.remove();
    return;
  }

  let index = 0;
  let highlighted = null;
  let previousFocus = document.activeElement;

  function rememberSeen() {
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch (error) {
      /* Private mode: the tour can show again, but the console still works. */
    }
  }

  function clearHighlight() {
    if (highlighted) {
      highlighted.classList.remove("tour-highlight");
      highlighted = null;
    }
  }

  function targetFor(step) {
    const selector = step.dataset.tourTarget;
    if (!selector) return null;
    try {
      return document.querySelector(selector);
    } catch (error) {
      return null;
    }
  }

  function place(target) {
    if (!target) {
      const width = bubble.offsetWidth;
      const height = bubble.offsetHeight;
      bubble.style.left = Math.max(16, (window.innerWidth - width) / 2) + "px";
      bubble.style.top = Math.max(16, (window.innerHeight - height) / 2) + "px";
      return;
    }
    const rect = target.getBoundingClientRect();
    const width = bubble.offsetWidth;
    const height = bubble.offsetHeight;
    const margin = 12;
    let left = rect.left + rect.width / 2 - width / 2;
    let top = rect.bottom + margin;
    if (top + height > window.innerHeight - margin) {
      top = rect.top - height - margin;
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));
    top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));
    bubble.style.left = left + "px";
    bubble.style.top = top + "px";
  }

  function currentTarget() {
    return targetFor(steps[index]);
  }

  function reposition() {
    place(currentTarget());
  }

  function show(at) {
    index = at;
    root.hidden = false;
    steps.forEach((step, position) => {
      step.hidden = position !== index;
    });
    progress.textContent = index + 1 + " / " + steps.length;
    back.disabled = index === 0;
    next.textContent = index === steps.length - 1 ? "Done" : "Next";

    clearHighlight();
    const target = currentTarget();
    if (target) {
      highlighted = target;
      target.classList.add("tour-highlight");
      target.scrollIntoView({ block: "nearest" });
    }
    window.requestAnimationFrame(reposition);
    next.focus();
  }

  function finish() {
    rememberSeen();
    clearHighlight();
    root.hidden = true;
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", reposition);
    window.removeEventListener("scroll", reposition, true);
    if (previousFocus && typeof previousFocus.focus === "function") {
      previousFocus.focus();
    }
  }

  function onKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      finish();
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      if (index === steps.length - 1) finish();
      else show(index + 1);
      return;
    }
    if (event.key === "ArrowLeft" && index > 0) {
      event.preventDefault();
      show(index - 1);
    }
  }

  next.addEventListener("click", () => {
    if (index === steps.length - 1) finish();
    else show(index + 1);
  });
  back.addEventListener("click", () => {
    if (index > 0) show(index - 1);
  });
  skip.addEventListener("click", finish);
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", reposition);
  window.addEventListener("scroll", reposition, true);

  show(0);
})();
