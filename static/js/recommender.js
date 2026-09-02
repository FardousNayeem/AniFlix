/* =========================================================================
   ANIFLIX recommendation widget.

   A guided conversation over the trained taste model. The questions are not
   defined here — they are fetched from the server, which reads them from the
   same file the scorer uses, so the widget cannot ask something the model has
   never heard of.

   Vanilla, like the rest of the front end. The panel reveals itself only
   after the server confirms a model is trained, so a site that has not run
   `manage.py train_recommender` shows no launcher at all.
   ========================================================================= */
(function () {
  "use strict";

  const root = document.querySelector("[data-recos]");
  if (!root) return;

  const panel = root.querySelector("[data-recos-panel]");
  const thread = root.querySelector("[data-recos-thread]");
  const form = root.querySelector("[data-recos-form]");
  const input = root.querySelector("[data-recos-input]");
  const openButton = root.querySelector("[data-recos-open]");
  const closeButton = root.querySelector("[data-recos-close]");
  const subtitle = root.querySelector("[data-recos-subtitle]");

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const THINK_MS = prefersReducedMotion ? 0 : 420;

  const state = {
    questions: [],
    index: 0,
    answers: {},
    busy: false,
    started: false,
  };

  /* --- small helpers ---------------------------------------------------- */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function scrollToEnd() {
    thread.scrollTo({ top: thread.scrollHeight, behavior: prefersReducedMotion ? "auto" : "smooth" });
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  /* --- messages --------------------------------------------------------- */
  function say(text, hint) {
    const node = el("div", "recos__msg recos__msg--bot");
    node.appendChild(el("p", null, text));
    if (hint) node.appendChild(el("p", "recos__hint", hint));
    thread.appendChild(node);
    scrollToEnd();
    return node;
  }

  function echo(text) {
    thread.appendChild(el("div", "recos__msg recos__msg--user", text));
    scrollToEnd();
  }

  function thinking() {
    const node = el("div", "recos__msg recos__msg--bot");
    const dots = el("span", "recos__thinking");
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    dots.setAttribute("aria-label", "Thinking");
    node.appendChild(dots);
    thread.appendChild(node);
    scrollToEnd();
    return node;
  }

  /* --- the conversation ------------------------------------------------- */
  function askNext() {
    if (state.index >= state.questions.length) {
      submit();
      return;
    }

    const question = state.questions[state.index];
    say(question.prompt, question.hint);

    const choices = el("div", "recos__choices");
    const picked = new Set();
    // Multi-select needs a commit step; single-select commits on tap.
    let commit = null;

    question.options.forEach((option) => {
      const chip = el("button", "recos__chip");
      chip.type = "button";
      chip.setAttribute("aria-pressed", "false");
      if (option.icon) {
        const icon = el("i", "ph " + option.icon);
        icon.setAttribute("aria-hidden", "true");
        chip.appendChild(icon);
      }
      chip.appendChild(el("span", null, option.label));

      chip.addEventListener("click", () => {
        if (!question.multiple) {
          answer(question, [option], choices, [option.label]);
          return;
        }
        if (picked.has(option)) {
          picked.delete(option);
        } else if (picked.size < question.maxChoices) {
          picked.add(option);
        } else {
          return;
        }
        chip.setAttribute("aria-pressed", String(picked.has(option)));
        commit.disabled = picked.size === 0 && !question.optional;
        commit.textContent = picked.size
          ? "That's me (" + picked.size + ")"
          : question.optional
            ? "Nothing to avoid"
            : "Pick at least one";
      });

      choices.appendChild(chip);
    });

    thread.appendChild(choices);

    if (question.multiple) {
      commit = el("button", "btn btn--primary btn--sm recos__confirm");
      commit.type = "button";
      commit.textContent = question.optional ? "Nothing to avoid" : "Pick at least one";
      commit.disabled = !question.optional;
      commit.addEventListener("click", () => {
        const chosen = Array.from(picked);
        answer(
          question,
          chosen,
          choices,
          chosen.length ? chosen.map((option) => option.label) : ["Nothing to avoid"],
          commit
        );
      });
      thread.appendChild(commit);
    }

    scrollToEnd();
  }

  function answer(question, options, choices, labels, confirmButton) {
    state.answers[question.key] = options.map((option) => option.value);
    choices.remove();
    if (confirmButton) confirmButton.remove();
    echo(labels.join(", "));
    state.index += 1;
    askNext();
  }

  /* --- asking the model ------------------------------------------------- */
  async function submit(freeText) {
    if (state.busy) return;
    state.busy = true;

    const dots = thinking();
    const started = Date.now();

    try {
      const response = await fetch(root.dataset.askUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ answers: state.answers, text: freeText || "" }),
      });
      const data = await response.json().catch(() => null);

      // A reply that lands instantly reads as canned; hold the dots briefly.
      await wait(Math.max(0, THINK_MS - (Date.now() - started)));
      dots.remove();

      if (!response.ok || !data || !data.ok) {
        say((data && data.message) || "Something went wrong on my end. Try again?");
        offerRestart();
        return;
      }
      renderResults(data.results);
    } catch (error) {
      dots.remove();
      say("I could not reach the server. Check your connection and try again.");
      offerRestart();
    } finally {
      state.busy = false;
    }
  }

  function renderResults(results) {
    if (!results || !results.length) {
      say("I could not find anything in the catalogue for that. Try a different mood?");
      offerRestart();
      return;
    }

    const best = results[0].match;
    if (best < 25) {
      // Honest about a small catalogue: say it is a stretch rather than
      // dressing up a weak match as a confident pick.
      say("Nothing here is a close match for that, but this is the nearest I have.");
    } else {
      say(results.length > 1 ? "Here is what I would put on." : "This is the one.");
    }

    const list = el("div", "recos__results");
    results.forEach((item) => list.appendChild(resultCard(item)));
    thread.appendChild(list);

    offerRestart();
    // Land on the first recommendation rather than the bottom of the list:
    // the best answer is the top one, and it is what should be on screen.
    list.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "start" });
  }

  function resultCard(item) {
    // A title we carry is a link to its page. One we do not is still worth
    // showing — the model ranked it above everything we do have — so it is
    // rendered as a plain card that says so, rather than a dead link.
    const card = el(item.available ? "a" : "div", "recos__card");
    if (item.available) {
      card.href = item.url;
    } else {
      card.classList.add("recos__card--elsewhere");
    }

    if (item.poster) {
      const poster = el("img", "recos__poster");
      poster.src = item.poster;
      poster.alt = "";
      poster.loading = "lazy";
      card.appendChild(poster);
    } else {
      card.appendChild(el("span", "recos__poster"));
    }

    const body = el("div", "recos__card-body");
    body.appendChild(el("span", "recos__name", item.name));

    const meta = el("p", "recos__meta");
    if (item.year) meta.appendChild(el("span", null, String(item.year)));
    if (item.episodes) {
      meta.appendChild(el("span", null, item.episodes + (item.episodes === 1 ? " episode" : " episodes")));
    }
    if (item.alreadySeen) meta.appendChild(el("span", null, "already on your list"));
    body.appendChild(meta);

    if (!item.available) {
      const notice = el("p", "recos__unavailable");
      const icon = el("i", "ph ph-info");
      icon.setAttribute("aria-hidden", "true");
      notice.appendChild(icon);
      notice.appendChild(el("span", null, "Not available on this site"));
      body.appendChild(notice);
    }

    if (item.reasons && item.reasons.length) {
      const reasons = el("div", "recos__reasons");
      item.reasons.forEach((reason) => reasons.appendChild(el("span", "recos__reason", reason)));
      body.appendChild(reasons);
    }

    const match = el("p", "recos__match");
    const bar = el("span", "recos__bar");
    const fill = el("span");
    fill.style.width = Math.max(3, item.match) + "%";
    bar.appendChild(fill);
    match.appendChild(bar);
    match.appendChild(el("span", null, item.match + "% match"));
    body.appendChild(match);

    if (item.neighbours && item.neighbours.length) {
      body.appendChild(
        el("p", "recos__note", "Fans of this also watch " + item.neighbours.join(" and ") + ".")
      );
    }

    card.appendChild(body);
    return card;
  }

  function offerRestart() {
    const actions = el("div", "recos__actions");
    const again = el("button", "btn btn--ghost btn--sm");
    again.type = "button";
    again.textContent = "Ask me again";
    again.addEventListener("click", () => {
      actions.remove();
      restart();
    });
    actions.appendChild(again);
    thread.appendChild(actions);
    scrollToEnd();
  }

  function restart() {
    state.index = 0;
    state.answers = {};
    askNext();
  }

  /* --- open, close, focus ----------------------------------------------- */
  function open() {
    panel.hidden = false;
    root.classList.add("is-open");
    openButton.setAttribute("aria-expanded", "true");

    if (!state.started) {
      state.started = true;
      say("Tell me what you are in the mood for and I will pick from the catalogue.");
      askNext();
    }
    window.setTimeout(() => input.focus({ preventScroll: true }), 0);
  }

  function close() {
    panel.hidden = true;
    root.classList.remove("is-open");
    openButton.setAttribute("aria-expanded", "false");
    openButton.focus({ preventScroll: true });
  }

  openButton.addEventListener("click", () => (panel.hidden ? open() : close()));
  closeButton.addEventListener("click", close);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) close();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || state.busy) return;
    input.value = "";
    echo(text);
    // Typing beats tapping: a sentence is a complete request on its own, so
    // it is sent with whatever has been answered so far and ends the round.
    state.index = state.questions.length;
    document.querySelectorAll(".recos__choices, .recos__confirm").forEach((node) => node.remove());
    submit(text);
  });

  /* --- boot ------------------------------------------------------------- */
  fetch(root.dataset.questionsUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      if (!data || !data.available || !data.questions.length) return;
      state.questions = data.questions;
      if (subtitle) subtitle.textContent = "Trained on taste, not on tags.";
      root.hidden = false;
    })
    .catch(() => {
      /* No widget rather than a broken one. */
    });
})();
