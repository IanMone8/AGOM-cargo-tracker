/* Customer orders: "+ Add item" lines (live line totals and order total), "Deposit 20%", "Copy reminder",
   the Delivered-with-balance warning and the "Mark as paid (old order)" bar. */
(function () {
  const num = v => parseInt(String(v || '').replace(/[^0-9]/g, ''), 10) || 0;
  const fmt = n => n.toLocaleString('en-US') + ' Ks';

  // --- order item lines ---------------------------------------------------------------------------------------
  function totals(form) {
    let total = 0, priced = true, any = false;
    form.querySelectorAll('[data-line]').forEach(line => {
      if (line.closest('template')) return;
      const del = line.querySelector('[name$="-DELETE"]');
      if (del && del.checked) return;
      const qty = Math.max(1, num((line.querySelector('[data-line-qty]') || {}).value));
      const priceBox = line.querySelector('[data-line-price]');
      const out = line.querySelector('[data-line-sum]');
      if (!priceBox) return;
      const item = line.querySelector('[data-item-text]');
      if (item && !item.value.trim() && !priceBox.value.trim()) { const o = line.querySelector('[data-line-sum]'); if (o) o.textContent = ''; return; }
      any = true;
      if (priceBox.value.trim() === '') { priced = false; if (out) out.textContent = ''; return; }
      const sum = num(priceBox.value) * qty;
      total += sum;
      if (out) out.textContent = qty > 1 ? `${fmt(num(priceBox.value))} × ${qty} = ${fmt(sum)}` : '';
    });
    const live = form.querySelector('[data-order-total-live]');
    if (live) live.textContent = any ? (priced ? `Order total ${fmt(total)}` : `Order total ${fmt(total)} + price not set`) : '';
  }
  document.addEventListener('input', e => { const f = e.target.closest('[data-order-form]'); if (f) totals(f); });
  document.addEventListener('change', e => { const f = e.target.closest('[data-order-form]'); if (f) totals(f); });
  document.addEventListener('click', e => {
    const add = e.target.closest('[data-add-line]');
    if (add) {
      const form = add.closest('[data-order-form]');
      const total = form.querySelector('[name$="-TOTAL_FORMS"]');
      const html = form.querySelector('[data-line-template]').innerHTML.replace(/__prefix__/g, total.value);
      const box = form.querySelector('[data-lines]');
      box.insertAdjacentHTML('beforeend', html);
      total.value = +total.value + 1;
      const line = box.lastElementChild;
      htmx.process(line);                                   // the item combo box talks to the server
      line.querySelector('[data-item-text]').focus();
      return;
    }
    const rm = e.target.closest('[data-remove-line]');
    if (rm) { const form = rm.closest('[data-order-form]'); rm.closest('[data-line]').remove(); totals(form); }
  });
  document.body.addEventListener('htmx:afterSettle', () => document.querySelectorAll('[data-order-form]').forEach(totals));
  document.querySelectorAll('[data-order-form]').forEach(totals);

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
