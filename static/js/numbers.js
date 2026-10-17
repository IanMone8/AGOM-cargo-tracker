/* Whole-number amount boxes – yen (¥) and Myanmar Kyat (Ks) – marked data-digits="<hint>" (forms.YenField / KsField):
   digits only while typing (like the phone field), 12,500 shown once you leave the box, and the separators taken out
   again before the form is checked. Loaded for every signed-in page. */
// While typing, anything that isn't a digit is dropped – no decimal point,
// no minus – and the caret stays where it was.
document.addEventListener('input', e => {
  const el = e.target;
  if (!el.matches || !el.matches('[data-digits]')) return;
  const caret = el.selectionStart ?? el.value.length;
  const digits = el.value.replace(/[^0-9]/g, '');
  if (digits === el.value) return;
  const before = el.value.slice(0, caret).replace(/[^0-9]/g, '').length;
  el.value = digits;
  el.setSelectionRange(before, before);
  if (/[^0-9,]/.test(e.data || '')) digitsNote(el);    // e.g. a decimal point or minus: say why it's gone
  if (typeof updateMoney === 'function') updateMoney();
}, true);
// Pasting "¥12,500" gives 12500; "12,500.50" keeps only the whole yen before the point (not 1250050).
document.addEventListener('paste', e => {
  const el = e.target;
  if (!el.matches || !el.matches('[data-digits]')) return;
  const text = (e.clipboardData || window.clipboardData).getData('text');
  const whole = text.split(/[.]/)[0].replace(/[^0-9]/g, '');
  e.preventDefault();
  el.setRangeText(whole, el.selectionStart, el.selectionEnd, 'end');
  if (whole !== text.trim()) digitsNote(el);
  el.dispatchEvent(new Event('input', {bubbles: true}));
});
// a short "Whole yen only…" / "Whole Kyat only…" note under the box (decimals and minus signs are not allowed)
function digitsNote(el) {
  const wrap = el.closest('.yen-wrap') || el;
  let note = wrap.nextElementSibling;
  if (!note || !note.matches('.yen-note')) {
    note = document.createElement('p');
    note.className = 'yen-note'; note.setAttribute('role', 'status');
    wrap.after(note);
  }
  note.textContent = el.dataset.digits || 'Whole numbers only';
  clearTimeout(note._t); note._t = setTimeout(() => note.remove(), 3000);
}
// pattern="[0-9]*": take the 12,500 separators out just before the browser checks the form (a click on a submit
// button – also what Enter does – comes before the check); the server stores whole yen either way.
document.addEventListener('click', e => {
  const btn = e.target.closest && e.target.closest('button:not([type=button]), input[type=submit]');
  if (btn && btn.form) btn.form.querySelectorAll('[data-digits]').forEach(el => el.value = el.value.replace(/[^0-9]/g, ''));
}, true);
// show thousands separators once you leave the box (the server accepts 12,500 and ¥12,500)
document.addEventListener('focusout', e => {
  if (!e.target.matches('[data-digits]') || !e.target.value.trim()) return;
  const n = parseInt(e.target.value.replace(/[^0-9]/g, ''), 10) || 0; if (String(n) === e.target.value.replace(/[^0-9]/g, '')) e.target.value = n.toLocaleString('en-US');
});
document.addEventListener('focusin', e => {
  if (!e.target.matches('[data-digits]')) return;
  const plain = e.target.value.replace(/[^0-9]/g, '');
  if (plain !== e.target.value) e.target.value = plain;
});
