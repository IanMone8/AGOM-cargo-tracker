/* Prices on item cards – the same card for box items (JP¥, "¥12,500") and customer order lines (Ks, "450,000 Ks"):
   live line totals (price × qty, or the sum of different piece prices), the "Different prices" piece boxes,
   in the Add box modal the Bag + Items + Cargo fee = Total line, and in an order form the live order total.
   Loaded for Owner / Admin / Japan sender (yen) and for people who may see payments (Kyat). */
const yenNum = v => parseInt(String(v || '').replace(/[^0-9]/g, ''), 10) || 0;
const yenFmt = n => '¥' + n.toLocaleString('en-US');
const ksFmt = n => n.toLocaleString('en-US') + ' Ks';
const cardFmt = card => (card.dataset.currency === 'Ks' ? ksFmt : yenFmt);
const MAX_PIECES = 50;   // ItemCardMixin.MAX_PIECES
// item card fields: "items-0-qty" / "lines-0-qty" in formsets, plain "qty" in the box page's Add item form
const QTY = '[name="qty"], [name$="-qty"]';
const AMOUNT = '[name="price_jpy"], [name$="-price_jpy"], [name="price_ks"], [name$="-price_ks"]';
const escAttr = v => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
// same markup as templates/tracker/_piece_price.html
const pieceHtml = (card, name, n, value) => card.dataset.currency === 'Ks'
  ? `<label class="piece-price">Piece ${n}<span class="ks-wrap mt-1"><input type="text" name="${escAttr(name)}" value="${escAttr(value)}" class="field" inputmode="numeric" pattern="[0-9]*" autocomplete="off" placeholder="0" data-ks data-digits="Whole Kyat only, e.g. 450000" data-piece aria-label="Piece ${n} price (Ks)"><span class="ks-unit" aria-hidden="true">Ks</span></span></label>`
  : `<label class="piece-price">Piece ${n}<span class="yen-wrap mt-1"><span class="yen-sign" aria-hidden="true">¥</span><input type="text" name="${escAttr(name)}" value="${escAttr(value)}" class="field" inputmode="numeric" pattern="[0-9]*" autocomplete="off" placeholder="0" data-yen data-digits="Whole yen only, e.g. 12500" data-piece aria-label="Piece ${n} amount (JP¥)"></span></label>`;
const cardGoneMoney = card => card.style.display === 'none' || (card.querySelector('[data-delete-flag]') || {}).value === 'on'
  || card.closest('template');

// "Different prices" on an item card: the checkbox shows for qty >= 2; ticked, one box per piece (count follows qty).
function syncPieces(card, ticked) {
  const box = card.querySelector('[data-price-box]');
  if (!box) return;
  const cb = box.querySelector('[data-diff-prices]'), pieces = box.querySelector('[data-pieces]');
  const single = box.querySelector('[data-single-price]'), amount = single.querySelector(AMOUNT);
  const q = Math.max(1, parseInt((card.querySelector(QTY) || {}).value, 10) || 1);
  const first = () => { const p = pieces.querySelector('[data-piece]'); return p ? p.value : ''; };
  box.querySelector('[data-diff-toggle]').hidden = q < 2;
  if (q < 2 && cb.checked) { cb.checked = false; ticked = false; }
  if (ticked === false && first()) amount.value = first();       // back to one price: the first piece's
  if (cb.checked) {
    const want = Math.min(q, MAX_PIECES);
    let list = pieces.querySelectorAll('.piece-price');
    for (let n = list.length + 1; n <= want; n++)                  // new boxes start with the single price when ticking
      pieces.insertAdjacentHTML('beforeend', pieceHtml(card, pieces.dataset.pieceName, n, ticked ? amount.value : ''));
    list = pieces.querySelectorAll('.piece-price');
    for (let i = list.length - 1; i >= want; i--) list[i].remove();
  }
  pieces.hidden = !cb.checked; single.hidden = cb.checked;
  box.classList.toggle('has-pieces', cb.checked);
}

// one card's line total: price × qty, or the sum of the piece prices. null = no price yet
function cardLine(card) {
  const amount = card.querySelector(AMOUNT), qty = card.querySelector(QTY), fmt = cardFmt(card);
  const out = card.querySelector('[data-line-total]'), cb = card.querySelector('[data-diff-prices]');
  if (!amount) return 0;
  let line, text = '';
  if (cb && cb.checked) {
    const vals = [...card.querySelectorAll('[data-piece]')].map(p => p.value.trim());
    line = vals.every(v => v) ? vals.reduce((a, v) => a + yenNum(v), 0) : null;
    if (line !== null) text = vals.map(v => fmt(yenNum(v))).join(' + ') + ' = ' + fmt(line);
  } else {
    const q = Math.max(1, yenNum(qty && qty.value));
    line = amount.value.trim() ? yenNum(amount.value) * q : null;
    if (line !== null && q > 1) text = `${fmt(yenNum(amount.value))} × ${q} = ${fmt(line)}`;
  }
  if (out) out.textContent = text;
  return line;
}

function updateMoney() {
  document.querySelectorAll('.item-card').forEach(c => { if (!c.closest('template')) cardLine(c); });
  // customer order: Order total = sum of the lines (Ks)
  document.querySelectorAll('[data-order-form]').forEach(form => {
    const live = form.querySelector('[data-order-total-live]');
    if (!live) return;
    let total = 0, unpriced = 0, any = 0;
    form.querySelectorAll('[data-lines] .item-card').forEach(card => {
      if (cardGoneMoney(card)) return;
      any++;
      const line = cardLine(card);
      if (line === null) unpriced++; else total += line;
    });
    live.textContent = any ? `Order total ${ksFmt(total)}` + (unpriced ? ' + price not set' : '') : '';
  });
  // Add box modal: Bag + Items + Cargo fee = Total (yen)
  const form = document.querySelector('#modal form');
  if (!form || !form.querySelector('[data-money-summary]')) return;
  let items = 0;
  form.querySelectorAll('#item-cards .item-card').forEach(card => { if (!cardGoneMoney(card)) items += cardLine(card) || 0; });
  const bag = yenNum((form.querySelector('[name="bag-price_jpy"]') || {}).value);
  const fee = yenNum((form.querySelector('[name="cargo_fee_jpy"]') || {}).value);
  const set = (k, n) => { const el = form.querySelector(`[data-sum="${k}"]`); if (el) el.textContent = yenFmt(n); };
  set('bag', bag); set('items', items); set('fee', fee); set('total', bag + items + fee);
}
document.addEventListener('input', e => {
  const card = e.target.closest('.item-card');
  if (card && e.target.matches(QTY)) syncPieces(card);
  if (card || e.target.closest('#modal form')) updateMoney();
});
document.addEventListener('change', e => {
  if (!e.target.matches('[data-diff-prices]')) return;
  syncPieces(e.target.closest('.item-card'), e.target.checked);
  updateMoney();
});
document.body.addEventListener('htmx:afterSettle', () => { document.querySelectorAll('.item-card').forEach(c => syncPieces(c)); updateMoney(); });
document.querySelectorAll('.item-card').forEach(c => syncPieces(c));
updateMoney();
