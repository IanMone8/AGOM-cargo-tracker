/* Customer orders: "+ Add item" (one more item card), "Deposit 20%", "Copy reminder",
   the Delivered-with-balance warning and the "Mark as paid (old order)" bar. */
(function () {
  const num = v => parseInt(String(v || '').replace(/[^0-9]/g, ''), 10) || 0;

  // --- order items: "+ Add item" adds one more item card (the same card as the Add box modal) ---------------------
  // Line totals and the live order total are kept by static/js/money.js; Copy / Delete come from base.html.
  document.addEventListener('click', e => {
    const add = e.target.closest('[data-add-line]');
    if (!add) return;
    const form = add.closest('[data-order-form]');
    const total = form.querySelector('[name$="-TOTAL_FORMS"]');
    const html = form.querySelector('[data-line-template]').innerHTML.replace(/__prefix__/g, total.value);
    const box = form.querySelector('[data-lines]');
    box.insertAdjacentHTML('beforeend', html);
    total.value = +total.value + 1;
    const card = box.lastElementChild;
    htmx.process(card);                                   // the Model box talks to the server
    card.querySelector('[data-brand-select]').focus();
    if (typeof updateMoney === 'function') updateMoney();
  });

  // --- "Deposit 20%": fills the amount, still editable ---------------------------------------------------------
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-deposit]');
    if (!btn) return;
    const box = btn.closest('[data-pay-form]').querySelector('[data-pay-amount]');
    box.value = num(btn.dataset.deposit).toLocaleString('en-US');
    box.focus();
  });

  // --- "Copy reminder" (Burmese + English: items, total, paid, balance) ------------------------------------------
  document.addEventListener('click', async e => {
    const btn = e.target.closest('[data-copy-reminder]');
    if (!btn) return;
    const text = btn.dataset.copyReminder, label = btn.querySelector('[data-copy-label]');
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {                                               // older phones: copy through a hidden text box
      const ta = Object.assign(document.createElement('textarea'), {value: text});
      ta.style.cssText = 'position:fixed;opacity:0'; document.body.append(ta); ta.select();
      document.execCommand('copy'); ta.remove();
    }
    label.textContent = 'Copied ✓';
    setTimeout(() => label.textContent = 'Copy reminder', 2000);
  });

  // --- Delivered while money is still owed: warn, but it can go ahead -------------------------------------------
  document.body.addEventListener('htmx:confirm', e => {
    const sel = e.detail.elt;
    if (!sel.matches || !sel.matches('select[data-balance]')) return;
    if (sel.value !== 'delivered' || num(sel.dataset.balance) <= 0) return;
    e.preventDefault();
    if (confirm(`This order still owes ${sel.dataset.balanceText}. Mark it as Delivered anyway?`)) e.detail.issueRequest(true);
    else sel.value = sel.dataset.was;
  });

  // --- a card's money changed (payment added / deleted): refresh the customer page's Money section and owes badge ----
  document.body.addEventListener('htmx:afterRequest', e => {
    const money = document.getElementById('money');
    if (!money || !e.detail.successful || e.detail.requestConfig.verb !== 'post') return;
    if (!e.detail.elt.closest('[id^="request-"]') && !e.detail.target.matches('[id^="request-"]')) return;
    fetch(location.pathname, {credentials: 'same-origin'}).then(r => r.text()).then(html => {
      const fresh = new DOMParser().parseFromString(html, 'text/html');
      ['money', 'owes-slot'].forEach(id => {
        const now = document.getElementById(id), next = fresh.getElementById(id);
        if (now && next) now.replaceWith(next);
      });
    });
  });

  // --- "Mark as paid (old order)" ---------------------------------------------------------------------------------
  document.addEventListener('submit', e => {
    if (!e.target.matches('[data-settle-form]')) return;
    const n = document.querySelectorAll('[data-settle-pick]:checked').length;
    if (!n) { e.preventDefault(); alert('Tick the old orders to mark as paid first.'); return; }
    if (!confirm(`Mark ${n} order(s) as paid (old order)? They will count as fully paid.`)) e.preventDefault();
  });
})();
