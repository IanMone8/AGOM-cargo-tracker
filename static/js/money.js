/* Yen amounts in the Add box modal: live line totals (amount × qty) and the
   Bag + Items + Cargo fee = Total line. Loaded only for Owner / Admin / Japan sender. */
const yenNum = v => parseInt(String(v || '').replace(/[^0-9]/g, ''), 10) || 0;
const yenFmt = n => '¥' + n.toLocaleString('en-US');
function updateMoney() {
  const form = document.querySelector('#modal form');
  if (!form || !form.querySelector('[data-money-summary]')) return;
  let items = 0;
  form.querySelectorAll('#item-cards .item-card').forEach(card => {
    const del = card.querySelector('[data-delete-flag]');
    const amount = card.querySelector('[name$="-price_jpy"]'), qty = card.querySelector('[name$="-qty"]');
    if (!amount || (del && del.value === 'on')) return;
    const q = Math.max(1, yenNum(qty && qty.value)), line = yenNum(amount.value) * q;
    items += line;
    const out = card.querySelector('[data-line-total]');
    if (out) out.textContent = amount.value && q > 1 ? `${yenFmt(yenNum(amount.value))} × ${q} = ${yenFmt(line)}` : '';
  });
  const bag = yenNum((form.querySelector('[name="bag-price_jpy"]') || {}).value);
  const fee = yenNum((form.querySelector('[name="cargo_fee_jpy"]') || {}).value);
  const set = (k, n) => { const el = form.querySelector(`[data-sum="${k}"]`); if (el) el.textContent = yenFmt(n); };
  set('bag', bag); set('items', items); set('fee', fee); set('total', bag + items + fee);
}
document.addEventListener('input', e => { if (e.target.closest('#modal form')) updateMoney(); });
// show thousands separators once you leave the box (the server accepts 12,500 and ¥12,500)
document.addEventListener('focusout', e => {
  if (!e.target.matches('[data-yen]') || !e.target.value.trim()) return;
  const n = yenNum(e.target.value); if (String(n) === e.target.value.replace(/[^0-9]/g, '')) e.target.value = n.toLocaleString('en-US');
});
document.addEventListener('focusin', e => { if (e.target.matches('[data-yen]')) e.target.value = e.target.value.replace(/[^0-9]/g, ''); });
document.body.addEventListener('htmx:afterSettle', updateMoney);
