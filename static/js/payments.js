/* Customer page: "+ Add payment" (split suggestion), "Copy reminder", the Delivered-with-balance warning and the
   "Mark selected orders as Paid (old order)" bar. Loaded only for people who may see payments (not the Japan sender). */
(function () {
  const num = v => parseInt(String(v || '').replace(/[^0-9]/g, ''), 10) || 0;
  const fmt = n => n.toLocaleString('en-US');
  const form = document.querySelector('[data-payment-form]');

  // --- the payment form -------------------------------------------------------------------------------------
  if (form) {
    const amount = form.querySelector('[name=amount_ks]');
    const forOrder = form.querySelector('[name=for_order]');
    const useCredit = form.querySelector('[name=use_credit]');
    const creditBox = form.querySelector('[name=alloc_credit]');
    const rows = [...form.querySelectorAll('[data-split-row]')];
    const credit = num(form.dataset.credit);
    const kind = () => (form.querySelector('[name=kind]:checked') || {}).value || 'payment';
    let creditTouched = false;

    function open(orderPk, name) {
      form.classList.remove('hidden');
      forOrder.value = orderPk || '';
      const label = form.querySelector('[data-pay-for]');
      label.hidden = !orderPk;
      form.querySelector('[data-pay-for-name]').textContent = name || '';
      rows.forEach(r => r.classList.toggle('is-other', !!orderPk && r.dataset.pk !== String(orderPk)));
      suggest();
      form.scrollIntoView({behavior: 'smooth', block: 'start'});
      amount.focus({preventScroll: true});
    }
    document.addEventListener('click', e => {
      const btn = e.target.closest('[data-open-payment], [data-pay-order]');
      if (btn) { open(btn.dataset.payOrder, btn.dataset.payName); return; }
      if (e.target.closest('[data-close-payment]')) form.classList.add('hidden');
      if (e.target.closest('[data-pay-for-clear]')) open('', '');
    });

    // oldest unpaid first (rows are listed oldest first); with an order picked, all of it goes to that order
    function suggest() {
      const total = num(amount.value), only = forOrder.value;
      const owed = new Map(rows.map(r => [r.dataset.pk, num(r.dataset.balance)]));
      const split = new Map(rows.map(r => [r.dataset.pk, 0]));
      let left = total, toCredit = 0;
      if (kind() === 'payment') {
        if (useCredit && useCredit.checked && credit > 0) {      // credit is used first, so less is still owed
          let c = credit;
          for (const r of rows) {
            if (only && r.dataset.pk !== only) continue;
            const take = Math.min(owed.get(r.dataset.pk), c);
            owed.set(r.dataset.pk, owed.get(r.dataset.pk) - take); c -= take;
          }
        }
        if (only) { split.set(only, left); left = 0; }
        for (const r of rows) {
          if (left <= 0) break;
          const take = Math.min(owed.get(r.dataset.pk), left);
          split.set(r.dataset.pk, take); left -= take;
        }
        toCredit = left;
      } else {                                                    // refund: from credit first, then the latest paid orders
        toCredit = Math.min(credit, left); left -= toCredit;
        if (only) { split.set(only, left); left = 0; }
        for (const r of [...rows].reverse()) {
          if (left <= 0) break;
          const take = Math.min(num(r.dataset.paid), left);
          split.set(r.dataset.pk, take); left -= take;
        }
      }
      rows.forEach(r => { const v = split.get(r.dataset.pk); r.querySelector('input').value = v ? fmt(v) : ''; });
      creditBox.value = toCredit ? fmt(toCredit) : '';
      creditTouched = false;
      showSum();
    }

    function showSum() {
      const total = num(amount.value);
      const orders = rows.reduce((s, r) => s + num(r.querySelector('input').value), 0);
      const out = form.querySelector('[data-split-sum]');
      const sum = orders + num(creditBox.value);
      out.classList.toggle('is-off', sum !== total);
      out.textContent = total ? (sum === total ? `Split ${fmt(sum)} Ks = ${fmt(total)} Ks ✓`
        : `Split ${fmt(sum)} Ks ≠ ${fmt(total)} Ks – the split must equal the amount`) : '';
      form.querySelector('[data-credit-label]').textContent = kind() === 'refund' ? 'From credit' : 'Keep as credit';
    }

    amount.addEventListener('input', suggest);
    form.querySelectorAll('[name=kind]').forEach(r => r.addEventListener('change', suggest));
    if (useCredit) useCredit.addEventListener('change', suggest);
    form.addEventListener('input', e => {
      if (!e.target.matches('.split-input')) return;
      if (e.target === creditBox) creditTouched = true;
      else if (!creditTouched) {                                  // the rest of the amount stays credit
        const orders = rows.reduce((s, r) => s + num(r.querySelector('input').value), 0);
        const rest = num(amount.value) - orders;
        creditBox.value = rest > 0 ? fmt(rest) : '';
      }
      showSum();
    });
    if (!form.classList.contains('hidden')) {                     // shown again after an error: keep what was typed
      const pk = forOrder.value;
      if (pk) form.querySelector('[data-pay-for-name]').textContent =
        (document.querySelector(`[data-pay-order="${pk}"]`) || {dataset: {}}).dataset.payName || '';
      showSum();
    }
  }

  // --- "Copy reminder" (Burmese + English, every unpaid order and the total) -----------------------------------
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

  // --- "Mark selected orders as Paid (old order)" ---------------------------------------------------------------
  document.addEventListener('submit', e => {
    if (!e.target.matches('[data-settle-form]')) return;
    const n = document.querySelectorAll('[data-settle-pick]:checked').length;
    if (!n) { e.preventDefault(); alert('Tick the old orders to mark as paid first.'); return; }
    if (!confirm(`Mark ${n} order(s) as paid (old order)? They will count as All clear.`)) e.preventDefault();
  });
})();
