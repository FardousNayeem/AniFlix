/* =========================================================================
   ANIFLIX front-end behaviour.

   Vanilla, no framework. The previous build loaded jQuery three times,
   Bootstrap 4 and 5 side by side, wow.js, waypoints, counterup and
   owlcarousel to run one carousel and one dropdown.

   Every module here is progressive: the page works with scripting disabled.
   Every animation honours prefers-reduced-motion.
   ========================================================================= */
(function () {
  "use strict";

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* --- helpers ---------------------------------------------------------- */
  const $ = (selector, scope) => (scope || document).querySelector(selector);
  const $$ = (selector, scope) => Array.from((scope || document).querySelectorAll(selector));

  function csrfToken() {
    const field = $('input[name="csrfmiddlewaretoken"]');
    if (field) return field.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  /* --- toasts ----------------------------------------------------------- */
  const TOAST_ICONS = {
    success: "ph-check-circle",
    error: "ph-warning-circle",
    warning: "ph-warning",
    info: "ph-info",
    debug: "ph-bug",
  };

  function toastStack() {
    let stack = $(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, level) {
    if (!message) return;
    const kind = level || "info";
    const node = document.createElement("div");
    node.className = "toast toast--" + kind;
    node.innerHTML =
      '<i class="ph ' + (TOAST_ICONS[kind] || TOAST_ICONS.info) + ' toast__icon" aria-hidden="true"></i>' +
      '<div class="toast__body"></div>' +
      '<button class="toast__close" type="button" aria-label="Dismiss">' +
      '<i class="ph ph-x" aria-hidden="true"></i></button>';
    $(".toast__body", node).textContent = message;

    const dismiss = () => {
      node.classList.add("is-leaving");
      window.setTimeout(() => node.remove(), prefersReducedMotion ? 0 : 260);
    };
    $(".toast__close", node).addEventListener("click", dismiss);
    toastStack().appendChild(node);
    window.setTimeout(dismiss, 4500);
  }

  window.aniflixToast = toast;

  function initServerMessages() {
    $$("[data-message]").forEach((node) => {
      toast(node.dataset.message, node.dataset.level || "info");
      node.remove();
    });
  }

  /* --- navigation ------------------------------------------------------- */
  function initNav() {
    const nav = $(".nav");
    if (!nav) return;

    // Scroll state via IntersectionObserver, not a scroll listener.
    const sentinel = document.createElement("div");
    sentinel.style.cssText = "position:absolute;top:0;height:1px;width:1px;";
    document.body.prepend(sentinel);
    new IntersectionObserver(
      ([entry]) => nav.classList.toggle("is-scrolled", !entry.isIntersecting),
      { threshold: 0 }
    ).observe(sentinel);

    const toggle = $(".nav__toggle", nav);
    const links = $(".nav__links", nav);
    if (toggle && links) {
      toggle.addEventListener("click", () => {
        const open = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!open));
        links.hidden = open;
        $("i", toggle).className = open ? "ph ph-list" : "ph ph-x";
      });
    }
  }

  /* --- dropdown menus --------------------------------------------------- */
  function initMenus() {
    const menus = $$(".menu");
    if (!menus.length) return;

    const closeAll = (except) => {
      menus.forEach((menu) => {
        if (menu === except) return;
        $(".menu__trigger", menu).setAttribute("aria-expanded", "false");
        $(".menu__panel", menu).hidden = true;
      });
    };

    menus.forEach((menu) => {
      const trigger = $(".menu__trigger", menu);
      const panel = $(".menu__panel", menu);
      if (!trigger || !panel) return;

      trigger.addEventListener("click", (event) => {
        event.stopPropagation();
        const open = trigger.getAttribute("aria-expanded") === "true";
        closeAll(menu);
        trigger.setAttribute("aria-expanded", String(!open));
        panel.hidden = open;
      });
    });

    document.addEventListener("click", () => closeAll());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAll();
    });
  }

  /* --- spotlight carousel ----------------------------------------------
     Motivated: the homepage promotes several titles and the rotation is what
     gives each one a turn. Pauses on hover and on focus, and does not
     auto-advance at all under reduced motion.
     --------------------------------------------------------------------- */
  function initSpotlight() {
    const spotlight = $("[data-spotlight]");
    if (!spotlight) return;

    const slides = $$(".spotlight__slide", spotlight);
    const dots = $$(".spotlight__dot", spotlight);
    const panels = $$("[data-spotlight-panel]", spotlight);
    if (slides.length < 2) return;

    let index = 0;
    let timer = null;

    const show = (next) => {
      index = (next + slides.length) % slides.length;
      slides.forEach((slide, i) => slide.classList.toggle("is-active", i === index));
      panels.forEach((panel, i) => (panel.hidden = i !== index));
      dots.forEach((dot, i) => {
        dot.classList.toggle("is-active", i === index);
        dot.setAttribute("aria-selected", String(i === index));
      });
    };

    const start = () => {
      if (prefersReducedMotion || timer) return;
      timer = window.setInterval(() => show(index + 1), 7000);
    };
    const stop = () => {
      window.clearInterval(timer);
      timer = null;
    };

    // Any manual move restarts the timer, so the slide does not change out
    // from under someone who just chose it.
    const goTo = (next) => {
      show(next);
      stop();
      start();
    };

    dots.forEach((dot, i) => dot.addEventListener("click", () => goTo(i)));

    const prev = $("[data-spotlight-prev]", spotlight);
    const next = $("[data-spotlight-next]", spotlight);
    if (prev) prev.addEventListener("click", () => goTo(index - 1));
    if (next) next.addEventListener("click", () => goTo(index + 1));

    // Arrow keys work once focus is inside the carousel, which is the
    // standard behaviour and cannot collide with the page's other shortcuts.
    spotlight.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        goTo(index - 1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        goTo(index + 1);
      }
    });

    spotlight.addEventListener("mouseenter", stop);
    spotlight.addEventListener("mouseleave", start);
    spotlight.addEventListener("focusin", stop);
    spotlight.addEventListener("focusout", start);
    document.addEventListener("visibilitychange", () =>
      document.hidden ? stop() : start()
    );

    show(0);
    start();
  }

  /* --- instant search ----------------------------------------------------
     Motivated: feedback. Typing two letters shows what the catalogue actually
     has, so a search that will return nothing is obvious before submitting.
     The form still works as a plain GET with scripting disabled.
     --------------------------------------------------------------------- */
  const SEARCH_DEBOUNCE_MS = 220;

  function debounce(fn, wait) {
    let handle;
    return function debounced() {
      const args = arguments;
      window.clearTimeout(handle);
      handle = window.setTimeout(() => fn.apply(null, args), wait);
    };
  }

  function initSearch() {
    const input = $("[data-search-input]");
    const box = input && input.closest("[data-search-box]");
    const list = box && $("[data-search-results]", box);
    const clear = box && $("[data-search-clear]", box);
    if (!input || !list) return;

    let items = [];
    let cursor = -1;
    let controller = null;
    const cache = new Map();

    const close = () => {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      cursor = -1;
    };

    const move = (delta) => {
      if (list.hidden || !items.length) return;
      cursor = (cursor + delta + items.length) % items.length;
      items.forEach((node, i) => node.classList.toggle("is-active", i === cursor));
      items[cursor].scrollIntoView({ block: "nearest" });
    };

    const render = (results, term) => {
      list.textContent = "";
      items = [];

      if (!results.length) {
        const empty = document.createElement("li");
        empty.className = "suggest__empty";
        empty.textContent = 'Nothing matches "' + term + '"';
        list.appendChild(empty);
      } else {
        results.forEach((result) => {
          const item = document.createElement("li");
          item.className = "suggest__item";
          item.setAttribute("role", "option");

          const link = document.createElement("a");
          link.href = result.url;
          link.style.cssText = "display:flex;align-items:center;gap:.75rem;width:100%;color:inherit";

          if (result.poster) {
            const thumb = document.createElement("img");
            thumb.className = "suggest__thumb";
            thumb.src = result.poster;
            thumb.alt = "";
            thumb.loading = "lazy";
            link.appendChild(thumb);
          }

          const text = document.createElement("span");
          const name = document.createElement("span");
          name.className = "suggest__name";
          name.style.display = "block";
          name.textContent = result.name;
          const meta = document.createElement("span");
          meta.className = "suggest__meta";
          meta.textContent = [result.year, result.studio].filter(Boolean).join(" \u00b7 ");
          text.appendChild(name);
          text.appendChild(meta);
          link.appendChild(text);

          item.appendChild(link);
          list.appendChild(item);
          items.push(item);
        });
      }

      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      cursor = -1;
    };

    const lookup = debounce((term) => {
      if (cache.has(term)) {
        render(cache.get(term), term);
        return;
      }
      if (controller) controller.abort();
      controller = new AbortController();

      fetch(input.dataset.suggestUrl + "?q=" + encodeURIComponent(term), {
        signal: controller.signal,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((response) => (response.ok ? response.json() : { results: [] }))
        .then((data) => {
          cache.set(term, data.results);
          if (input.value.trim() === term) render(data.results, term);
        })
        .catch(() => {
          /* Aborted or offline: the form still submits normally. */
        });
    }, SEARCH_DEBOUNCE_MS);

    input.addEventListener("input", () => {
      const term = input.value.trim();
      if (clear) clear.hidden = !term;
      if (term.length < 2) {
        close();
        return;
      }
      lookup(term);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-1);
      } else if (event.key === "Enter" && cursor > -1) {
        const link = $("a", items[cursor]);
        if (link) {
          event.preventDefault();
          window.location.href = link.href;
        }
      } else if (event.key === "Escape") {
        close();
        input.blur();
      }
    });

    input.addEventListener("focus", () => {
      if (items.length && input.value.trim().length >= 2) list.hidden = false;
    });

    if (clear) {
      clear.addEventListener("click", () => {
        input.value = "";
        clear.hidden = true;
        close();
        input.focus();
      });
    }

    document.addEventListener("click", (event) => {
      if (!box.contains(event.target)) close();
    });

    // "/" focuses search, the way it does in most catalogues.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey) return;
      const tag = (document.activeElement.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || document.activeElement.isContentEditable) return;
      event.preventDefault();
      input.focus();
      input.select();
    });
  }

  /* --- player ------------------------------------------------------------
     A cross-origin frame that is refused does not fire `error`, and a frame
     that is merely slow looks identical to one that is blocked. There is no
     signal that separates them, so the player never hides a frame on a guess:
     if nothing has loaded after the grace period the escape line below simply
     becomes prominent. A slow connection therefore loses nothing.
     --------------------------------------------------------------------- */
  const FRAME_GRACE_MS = 12000;

  function initPlayer() {
    $$("[data-player]").forEach((player) => {
      const frame = $("[data-player-frame]", player);
      if (!frame) return;

      const escape = $("[data-player-escape]", player.parentNode || document);
      const lead = escape && $("[data-escape-lead]", escape);
      let loaded = false;

      frame.addEventListener(
        "load",
        () => {
          loaded = true;
          window.clearTimeout(timer);
          if (escape) escape.classList.remove("is-prominent");
        },
        { once: true }
      );

      const timer = window.setTimeout(() => {
        if (loaded || !escape) return;
        escape.classList.add("is-prominent");
        if (lead) lead.textContent = "Still not playing?";
      }, FRAME_GRACE_MS);
    });
  }

  /* --- rail arrows -------------------------------------------------------
     Motivated: feedback. A horizontal rail is unreachable with a plain mouse
     wheel, so the arrows are the only way some visitors can see the rest.
     --------------------------------------------------------------------- */
  function initRails() {
    $$(".rail-wrap").forEach((wrap) => {
      const rail = $(".rail", wrap);
      const prev = $(".rail__nav--prev", wrap);
      const next = $(".rail__nav--next", wrap);
      if (!rail || !prev || !next) return;

      const sync = () => {
        const max = rail.scrollWidth - rail.clientWidth - 2;
        const overflows = max > 0;
        prev.hidden = !overflows || rail.scrollLeft <= 2;
        next.hidden = !overflows || rail.scrollLeft >= max;
      };

      const page = (direction) =>
        rail.scrollBy({ left: direction * rail.clientWidth * 0.85, behavior: prefersReducedMotion ? "auto" : "smooth" });

      prev.addEventListener("click", () => page(-1));
      next.addEventListener("click", () => page(1));
      rail.addEventListener("scroll", sync, { passive: true });
      new ResizeObserver(sync).observe(rail);
      sync();
    });
  }

  /* --- scroll reveal ---------------------------------------------------- */
  function initReveal() {
    const targets = $$(".reveal");
    if (!targets.length) return;
    if (prefersReducedMotion) {
      targets.forEach((node) => node.classList.add("is-visible"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );

    targets.forEach((node, i) => {
      node.style.transitionDelay = Math.min(i % 6, 5) * 55 + "ms";
      observer.observe(node);
    });
  }

  /* --- watchlist toggle ------------------------------------------------- */
  function initWatchlist() {
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-watchlist-url]");
      if (!button) return;
      event.preventDefault();
      if (button.disabled) return;

      button.disabled = true;
      fetch(button.dataset.watchlistUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
      })
        .then((response) => {
          if (response.status === 403 || response.redirected) {
            window.location.href = button.dataset.loginUrl || "/accounts/login/";
            return null;
          }
          if (!response.ok) throw new Error("Request failed");
          return response.json();
        })
        .then((data) => {
          if (!data) return;
          button.classList.toggle("is-active", data.in_watchlist);
          button.setAttribute("aria-pressed", String(data.in_watchlist));
          const icon = $("i", button);
          if (icon) {
            icon.className = "ph " + (data.in_watchlist ? "ph-check" : "ph-plus");
          }
          const label = $("[data-watchlist-label]", button);
          if (label) label.textContent = data.label;
          const title = button.dataset.title || "This title";
          toast(
            data.in_watchlist ? title + " added to My List." : title + " removed from My List.",
            "success"
          );
        })
        .catch(() => toast("Could not update My List. Try again.", "error"))
        .finally(() => {
          button.disabled = false;
        });
    });
  }

  /* --- cart ------------------------------------------------------------- */
  function initCart() {
    document.addEventListener("submit", (event) => {
      const form = event.target.closest("[data-cart-form]");
      if (!form) return;
      event.preventDefault();
      submitCart(form);
    });
  }

  function submitCart(form) {
    const scope = form.closest("[data-cart-scope]") || form;
    scope.classList.add("is-busy");

    // getAttribute, not `.action`: a control named `action` would shadow it.
    fetch(form.getAttribute("action"), {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      body: new FormData(form),
    })
      .then((response) => {
        if (response.status === 403 || response.redirected) {
          window.location.href = form.dataset.loginUrl || "/accounts/login/";
          return null;
        }
        return response.json();
      })
      .then((data) => {
        if (!data) return;
        updateCartBadge(data.count);
        toast(data.message, data.ok ? "success" : "error");
        if (!data.ok) return;
        bumpCartIcon();
        // The cart page shows its own totals, so it is patched in place. The
        // page used to reload on every quantity change, losing scroll position.
        if (form.dataset.cartReload === "true") {
          patchCartPage(data);
        }
      })
      .catch(() => toast("Could not update your cart. Try again.", "error"))
      .finally(() => scope.classList.remove("is-busy"));
  }

  function patchCartPage(data) {
    const lines = data.lines || {};

    $$("[data-cart-line]").forEach((row) => {
      const line = lines[row.dataset.cartLine];
      if (!line) {
        row.remove();
        return;
      }
      const quantity = $("[data-line-quantity]", row);
      const total = $("[data-line-total]", row);
      const changed = quantity && quantity.textContent.trim() !== String(line.quantity);
      if (quantity) quantity.textContent = line.quantity;
      if (total) total.textContent = data.currency + formatAmount(line.line_total);

      if (changed && !prefersReducedMotion) {
        row.classList.remove("is-updated");
        void row.offsetWidth;
        row.classList.add("is-updated");
      }
    });

    const count = $("[data-cart-items]");
    const subtotal = $("[data-cart-subtotal]");
    if (count) count.textContent = data.count;
    if (subtotal) subtotal.textContent = data.currency + formatAmount(data.subtotal);

    const heading = $("[data-cart-heading]");
    if (heading) {
      heading.textContent = data.count + " item" + (data.count === 1 ? "" : "s");
    }

    // Last line removed: the page has to become the empty state.
    if (!Object.keys(lines).length) window.location.reload();
  }

  function formatAmount(value) {
    const number = Number(value || 0);
    return number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function bumpCartIcon() {
    const badge = $("[data-cart-count]");
    if (!badge || prefersReducedMotion) return;
    badge.classList.remove("is-bumped");
    // Force a reflow so the animation restarts on a repeated add.
    void badge.offsetWidth;
    badge.classList.add("is-bumped");
  }

  function updateCartBadge(count) {
    const badge = $("[data-cart-count]");
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = !count;
  }

  /* --- rating ------------------------------------------------------------
     Motivated: feedback. Picking a star used to reload the whole page to show
     a number that is already on screen.
     --------------------------------------------------------------------- */
  function initRating() {
    const form = $("[data-rating-form]");
    if (!form) return;

    $$("input[name='score']", form).forEach((radio) => {
      radio.addEventListener("change", () => submitRating(form));
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitRating(form);
    });
  }

  function submitRating(form) {
    form.classList.add("is-busy");
    fetch(form.getAttribute("action"), {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" },
      body: new FormData(form),
    })
      .then((response) => {
        if (response.status === 403 || response.redirected) {
          window.location.href = "/accounts/login/";
          return null;
        }
        return response.json();
      })
      .then((data) => {
        if (!data) return;
        if (!data.ok) {
          toast(data.message, "error");
          return;
        }
        const average = $("[data-rating-average]");
        const count = $("[data-rating-count]");
        if (average) average.textContent = data.average;
        if (count) count.textContent = data.count_label;
        renderStars(data.average_value);
        toast(data.message, "success");
      })
      .catch(() => toast("Could not save your rating. Try again.", "error"))
      .finally(() => form.classList.remove("is-busy"));
  }

  function renderStars(value) {
    const meter = $("[data-rating-stars]");
    if (!meter) return;
    const filled = Math.round(Number(value) || 0);
    $$(".star", meter).forEach((star, index) => {
      star.classList.toggle("is-filled", index < filled);
    });
  }

  /* --- copy to clipboard -------------------------------------------------- */
  function initCopy() {
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-copy]");
      if (!button) return;
      const value = button.dataset.copy;
      if (!navigator.clipboard) {
        toast("Copying is not available in this browser.", "warning");
        return;
      }
      navigator.clipboard
        .writeText(value)
        .then(() => {
          toast("Copied " + value, "success");
          const icon = $("i", button);
          if (!icon) return;
          const original = icon.className;
          icon.className = "ph ph-check";
          window.setTimeout(() => {
            icon.className = original;
          }, 1600);
        })
        .catch(() => toast("Could not copy that.", "error"));
    });
  }

  /* --- back to top -------------------------------------------------------- */
  function initBackToTop() {
    const button = $("[data-back-to-top]");
    if (!button) return;

    // Watched with an observer rather than a scroll listener.
    const sentinel = document.createElement("div");
    sentinel.style.cssText = "position:absolute;top:80vh;height:1px;width:1px;";
    document.body.appendChild(sentinel);
    new IntersectionObserver(
      ([entry]) => button.classList.toggle("is-visible", !entry.isIntersecting),
      { threshold: 0 }
    ).observe(sentinel);

    button.addEventListener("click", () =>
      window.scrollTo({ top: 0, behavior: prefersReducedMotion ? "auto" : "smooth" })
    );
  }

  /* --- episode keyboard navigation ---------------------------------------
     Motivated: this is a player. Arrow keys are what people reach for.
     --------------------------------------------------------------------- */
  function initEpisodeKeys() {
    const previous = $("[data-episode-prev]");
    const next = $("[data-episode-next]");
    if (!previous && !next) return;

    document.addEventListener("keydown", (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const tag = (document.activeElement.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "iframe") return;

      if (event.key === "ArrowLeft" && previous) {
        window.location.href = previous.href;
      } else if (event.key === "ArrowRight" && next) {
        window.location.href = next.href;
      }
    });
  }

  /* --- quantity steppers ------------------------------------------------ */
  function initQuantity() {
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-qty-step]");
      if (!button) return;
      const wrapper = button.closest("[data-qty]");
      const input = $("input[name='quantity']", wrapper);
      if (!input) return;

      const step = Number(button.dataset.qtyStep);
      const min = Number(input.min || 1);
      const max = Number(input.max || 99);
      const next = Math.min(max, Math.max(min, Number(input.value || min) + step));
      if (next === Number(input.value)) return;
      input.value = next;
      const display = $("[data-qty-value]", wrapper);
      if (display) display.textContent = next;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  /* --- auto-submitting filters ------------------------------------------ */
  function initAutoSubmit() {
    $$("[data-autosubmit]").forEach((control) => {
      control.addEventListener("change", () => {
        const form = control.closest("form");
        if (form) form.submit();
      });
    });
  }

  /* --- confirm-before-destroy ------------------------------------------- */
  function initConfirm() {
    document.addEventListener("submit", (event) => {
      const form = event.target.closest("[data-confirm]");
      if (!form) return;
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  }

  /* --- boot ------------------------------------------------------------- */
  function boot() {
    initServerMessages();
    initNav();
    initMenus();
    initSpotlight();
    initSearch();
    initPlayer();
    initRails();
    initReveal();
    initWatchlist();
    initCart();
    initQuantity();
    initRating();
    initCopy();
    initBackToTop();
    initEpisodeKeys();
    initAutoSubmit();
    initConfirm();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
