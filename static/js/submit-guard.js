/* One tap = one submit, on every form of the app (and /admin/).
 *
 * Why: on a slow server a second tap on "Log in" sends the old CSRF token (logging in replaces it) and fails with
 * 403; elsewhere a second tap could save something twice.
 *
 * - Normal forms: on submit the form is marked busy and its submit button(s) show a spinner + "Saving…"
 *   ("Logging in…" etc. via data-busy-text) at the same size; further submits (taps, Enter) are ignored until the
 *   next page loads. Re-enabled when another script cancels the submit (e.g. a confirm() answered "Cancel"), when
 *   the page comes back from the back/forward cache, or after 45 s if nothing happened (e.g. a download).
 * - HTMX: an element that is already sending a POST / PUT / PATCH / DELETE (a form, a ✓ Matched button, a status
 *   select…) can't send another until its response (or error) arrives. Searches and filters (GET) are not held back.
 * No libraries; works with touch and the keyboard. Forms with data-no-guard are left alone.
 */
(function () {
  if (window.agomSubmitGuard) return;
  window.agomSubmitGuard = true;

  // the spinner styles live here so the same file works on /admin/ (which doesn't load agom.css)
  const css = document.createElement('style');
  css.textContent =
    '.is-busy{cursor:progress!important;pointer-events:none;opacity:.85}' +
    '.busy-inner{display:inline-flex;align-items:center;justify-content:center;gap:.45em;white-space:nowrap;' +
    'overflow:hidden;max-width:100%}' +
    '.busy-spin{width:1em;height:1em;flex:none;border:2px solid currentColor;border-right-color:transparent;' +
    'border-radius:50%;animation:busy-spin .7s linear infinite}' +
    '@keyframes busy-spin{to{transform:rotate(360deg)}}' +
    '@media (prefers-reduced-motion:reduce){.busy-spin{animation-duration:2s}}';
  document.head.appendChild(css);

  const NARROW = 90;   // px: narrower buttons show only the spinner, so the text never spills out
  const SAFETY_MS = 45000;
  const busyText = (btn, form) => btn.dataset.busyText || (form && form.dataset.busyText) || 'Saving…';

  function showBusy(btn, text) {
    if (!btn || btn.dataset.busySaved !== undefined) return;
    const r = btn.getBoundingClientRect();
    btn.dataset.busySaved = btn.tagName === 'INPUT' ? btn.value : btn.innerHTML;
    btn.dataset.busyStyle = btn.getAttribute('style') || '';
    if (r.width) { btn.style.width = r.width + 'px'; btn.style.height = r.height + 'px'; }   // no layout jump
    btn.classList.add('is-busy');
    btn.setAttribute('aria-busy', 'true');
    btn.setAttribute('aria-disabled', 'true');
    if (btn.tagName === 'INPUT') { btn.value = text; return; }
    const label = r.width && r.width < NARROW ? '' : `<span>${text}</span>`;
    btn.innerHTML = `<span class="busy-inner"><span class="busy-spin" aria-hidden="true"></span>${label}</span>`;
    if (!label) btn.setAttribute('aria-label', text);
  }

  function clearBusy(btn) {
    if (!btn || btn.dataset.busySaved === undefined) return;
    if (btn.tagName === 'INPUT') btn.value = btn.dataset.busySaved; else btn.innerHTML = btn.dataset.busySaved;
    btn.setAttribute('style', btn.dataset.busyStyle);
    if (!btn.dataset.busyStyle) btn.removeAttribute('style');
    delete btn.dataset.busySaved; delete btn.dataset.busyStyle;
    btn.classList.remove('is-busy');
    btn.removeAttribute('aria-busy'); btn.removeAttribute('aria-disabled');
  }

  const submitButtons = form => [...form.querySelectorAll('button:not([type=button]):not([type=reset]), input[type=submit]')]
    .concat(form.id ? [...document.querySelectorAll(`button[form="${form.id}"]:not([type=button]), input[type=submit][form="${form.id}"]`)] : []);
  const isHtmxForm = form => form.matches('[hx-post],[hx-put],[hx-patch],[hx-delete],[data-hx-post],[hx-get],[data-hx-get]');

  function lock(form, submitter) {
    form.dataset.busy = '1';
    form.setAttribute('aria-busy', 'true');
    const buttons = submitButtons(form);
    const main = submitter && buttons.includes(submitter) ? submitter : buttons[0];
    buttons.forEach(b => b === main ? showBusy(b, busyText(b, form)) : b.classList.add('is-busy'));
  }

  function unlock(form) {
    if (!form || !form.dataset.busy) return;
    delete form.dataset.busy;
    form.removeAttribute('aria-busy');
    clearTimeout(form._busyTimer);
    submitButtons(form).forEach(b => { clearBusy(b); b.classList.remove('is-busy'); });
  }

  // --- normal (full page) forms: capture phase, so a second submit never reaches anything else -------------------
  document.addEventListener('submit', e => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || 'noGuard' in form.dataset || isHtmxForm(form)) return;
    if (form.dataset.busy) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (e.defaultPrevented) return;                         // another script already stopped this submit
    lock(form, e.submitter);
    setTimeout(() => { if (e.defaultPrevented) unlock(form); }, 0);   // cancelled later (e.g. confirm → Cancel)
    form._busyTimer = setTimeout(() => unlock(form), SAFETY_MS);       // nothing happened (download, offline…)
  }, true);

  // --- HTMX: one saving request at a time per element -------------------------------------------------------------
  const SAVING = /^(post|put|patch|delete)$/i;
  document.addEventListener('htmx:beforeRequest', e => {
    const elt = e.detail.elt, verb = (e.detail.requestConfig && e.detail.requestConfig.verb) || '';
    if (!SAVING.test(verb) || (elt.closest && elt.closest('[data-no-guard]'))) return;
    if (elt.dataset.busy) { e.preventDefault(); return; }    // still waiting for the first one
    elt.dataset.busy = '1';
    elt.setAttribute('aria-busy', 'true');
    if (elt instanceof HTMLFormElement) lock(elt, elt._lastSubmitter);
    else if (elt.matches('button, input[type=submit]')) showBusy(elt, busyText(elt));
    else if (elt.matches('select')) elt.classList.add('is-busy');
  });
  document.addEventListener('htmx:afterRequest', e => {      // success, error or network failure: always fires
    const elt = e.detail.elt;
    if (!elt || !elt.dataset || !elt.dataset.busy) return;
    if (elt instanceof HTMLFormElement) { unlock(elt); return; }
    delete elt.dataset.busy;
    elt.removeAttribute('aria-busy');
    clearBusy(elt);
    elt.classList.remove('is-busy');
  });
  // remember which button submitted an HTMX form (for the spinner)
  document.addEventListener('submit', e => { if (e.target instanceof HTMLFormElement) e.target._lastSubmitter = e.submitter; }, true);

  // --- back / forward cache: a page restored from history must never keep a busy button ---------------------------
  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    document.querySelectorAll('form[data-busy]').forEach(unlock);
    document.querySelectorAll('[data-busy-saved]').forEach(clearBusy);
    document.querySelectorAll('[data-busy]').forEach(el => { delete el.dataset.busy; el.removeAttribute('aria-busy'); el.classList.remove('is-busy'); });
  });
})();
