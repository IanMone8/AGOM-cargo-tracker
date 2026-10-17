/* Yen amounts on item cards: live line totals (amount × qty, or the sum of different piece prices),
   the "Different prices" piece boxes, and in the Add box modal the
   Bag + Items + Cargo fee = Total line. Loaded only for Owner / Admin / Japan sender. */
const yenNum = v => parseInt(String(v || '').replace(/[^0-9]/g, ''), 10) || 0;
const yenFmt = n => '¥' + n.toLocaleString('en-US');
const MAX_PIECES = 50;   // JapanItemForm.MAX_PIECES
// item card fields: "items-0-qty" in the Add box modal, plain "qty" in the box page's Add item form
const QTY = '[name="qty"], [name$="-qty"]', AMOUNT = '[name="price_jpy"], [name$="-price_jpy"]';
const escAttr = v => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
// same markup as templates/tracker/_piece_price.html
const pieceHtml = (name, n, value) => `<label class="piece-price">Piece ${n}<span class="yen-wrap mt-1"><span class="yen-sign" aria-hidden="true">¥</span><input type="text" name="${escAttr(name)}" value="${escAttr(value)}" class="field" inputmode="numeric" pattern="[0-9]*" autocomplete="off" placeholder="0" data-yen data-digits="Whole yen only, e.g. 12500" data-piece aria-label="Piece ${n} amount (JP¥)"></span></label>`;

// "Different prices" on an item card: the checkbox shows for qty >= 2; ticked, one ¥ box per piece (count follows qty).
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
      pieces.insertAdjacentHTML('beforeend', pieceHtml(pieces.dataset.pieceName, n, ticked ? amount.value : ''));
    list = pieces.querySelectorAll('.piece-price');
    for (let i = list.length - 1; i >= want; i--) list[i].remove();
  }
  pieces.hidden = !cb.checked; single.hidden = cb.checked;
  box.classList.toggle('has-pieces', cb.checked);
}

// one card's line total: amount × qty, or the sum of the piece prices
function cardLine(card) {
  const amount = card.querySelector(AMOUNT), qty = card.querySelector(QTY);
  const out = card.querySelector('[data-line-total]'), cb = card.querySelector('[data-diff-prices]');
  if (!amount) return 0;
  let line, text = '';
  if (cb && cb.checked) {
    const vals = [...card.querySelectorAll('[data-piece]')].map(p => yenNum(p.value));
    line = vals.reduce((a, b) => a + b, 0);
    if (line) text = vals.map(yenFmt).join(' + ') + ' = ' + yenFmt(line);
  } else {
    const q = Math.max(1, yenNum(qty && qty.value));
    line = yenNum(amount.value) * q;
    if (amount.value && q > 1) text = `${yenFmt(yenNum(amount.value))} × ${q} = ${yenFmt(line)}`;
  }
  if (out) out.textContent = text;
  return line;
}

function updateMoney() {
  document.querySelectorAll('.item-card').forEach(cardLine);
  const form = document.querySelector('#modal form');
  if (!form || !form.querySelector('[data-money-summary]')) return;
  let items = 0;
  form.querySelectorAll('#item-cards .item-card').forEach(card => {
    const del = card.querySelector('[data-delete-flag]');
    if (!(del && del.value === 'on')) items += cardLine(card);
  });
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
