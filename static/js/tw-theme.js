/* Tailwind theme: semantic colour names that point at the CSS colour tokens in css/agom.css.
   Loaded right after the Tailwind CDN script, so templates write e.g. "text-ink-muted" or
   "bg-ok-bg" and every colour comes from one place. (Also read by the local test CSS build.) */
(function (root) {
  var theme = {
    theme: {
      extend: {
        colors: {
          page: "var(--bg)",
          card: "var(--card)",
          line: { DEFAULT: "var(--card-border)", strong: "var(--line-strong)" },
          grad: { from: "var(--grad-from)", to: "var(--grad-to)" },
          primary: { DEFAULT: "var(--primary)", hover: "var(--primary-hover)" },
          ink: { DEFAULT: "var(--text)", muted: "var(--text-muted)" },
          sage: "var(--sage)", mint: "var(--mint)", cream: "var(--cream)", sand: "var(--sand)",
          ok: { DEFAULT: "var(--ok)", ink: "var(--ok-ink)", bg: "var(--ok-bg)" },
          warn: { DEFAULT: "var(--warn)", ink: "var(--warn-ink)", bg: "var(--warn-bg)" },
          danger: { DEFAULT: "var(--danger)", ink: "var(--danger-ink)", bg: "var(--danger-bg)" },
          info: { DEFAULT: "var(--info)", ink: "var(--info-ink)", bg: "var(--info-bg)" },
          viber: { DEFAULT: "var(--viber)", ink: "var(--viber-ink)", bg: "var(--viber-bg)" }
        }
      }
    }
  };
  if (typeof module !== "undefined" && module.exports) module.exports = theme;
  else if (root.tailwind) root.tailwind.config = theme;
})(this);
