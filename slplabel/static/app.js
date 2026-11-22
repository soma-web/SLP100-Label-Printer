'use strict';
const $ = s => document.querySelector(s);
const state = { gmode: 'none', gpos: 'left', align: 'center', image: '', icon: '', info: null, settings: {} };
let previewTimer = null, previewSeq = 0;

function toast(msg, err) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(t._h); t._h = setTimeout(() => t.className = 'toast', err ? 6000 : 2500);
}
async function api(path, method = 'GET', body) {
  const opts = { method };
  if (method !== 'GET') { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body || {}); }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = { error: text }; }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const guarded = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
function el(tag, props = {}, ...children) {
  const e = Object.assign(document.createElement(tag), props); e.append(...children); return e;
}
function setSeg(id, v) { $(id).querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === String(v))); }
function seg(id, key, cb) {
  $(id).addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    setSeg(id, b.dataset.v); if (key) state[key] = b.dataset.v; if (cb) cb();
  });
}
const num = (id, d = 0) => { const v = parseFloat($(id).value); return isNaN(v) ? d : v; };

// --- editor ---------------------------------------------------------------
function labelSpec() {
  return {
    lines: $('#text').value.split('\n'), font: $('#font').value, bold: $('#bold').checked,
    size_pt: num('#size'), align: state.align, line_spacing: num('#spacing', 1), margin_mm: num('#margin'),
    border: $('#border').checked, invert: $('#invert').checked, length_mm: num('#length'),
    qr: state.gmode === 'qr' ? $('#qr').value : '', icon: state.gmode === 'icon' ? state.icon : '',
    image: state.gmode === 'image' ? state.image : '',
    image_rotate: state.gmode === 'qr' ? 0 : parseInt($('#imgrot').value),
    graphic_pos: state.gpos, graphic_scale: num('#gscale', 90) / 100,
  };
}
function applySpec(s) {
  $('#text').value = (s.lines || []).join('\n');
  setFont(s.font);
  $('#bold').checked = s.bold ?? true; $('#size').value = s.size_pt || '';
  state.align = s.align || 'center'; setSeg('#align', state.align);
  $('#spacing').value = String(s.line_spacing ?? 1); $('#margin').value = s.margin_mm ?? 1;
  $('#border').checked = !!s.border; $('#invert').checked = !!s.invert;
  $('#length').value = s.length_mm || 0; syncMedia();
  state.gmode = s.qr ? 'qr' : s.icon ? 'icon' : s.image ? 'image' : 'none'; setSeg('#gmode', state.gmode);
  $('#qr').value = s.qr || ''; state.icon = s.icon || ''; state.image = s.image || ''; $('#img').value = '';
  $('#imgrot').value = s.image_rotate || 0;
  state.gpos = s.graphic_pos || 'left'; setSeg('#gpos', state.gpos);
  $('#gscale').value = Math.round((s.graphic_scale ?? 0.9) * 100); $('#gscale-val').textContent = $('#gscale').value + ' %';
  updateGraphicUI(); schedulePreview();
}
function setFont(name) {
  const f = $('#font'); f.value = name || state.settings.default_font || '';
  if (!f.value) f.selectedIndex = 0;       // fonts of the Windows version fall back
}
function updateGraphicUI() {
  for (const [id, mode] of [['#icon-box', 'icon'], ['#qr-box', 'qr'], ['#img-box', 'image']])
    $(id).classList.toggle('hidden', state.gmode !== mode);
  $('#gpos-box').classList.toggle('hidden', state.gmode === 'none');
  $('#rot-box').classList.toggle('hidden', state.gmode === 'qr');
  $('#to-icon').classList.toggle('hidden', !state.image);
  renderIcons();
}
const plain = s => s.toLowerCase().replace(/ß/g, 'ss').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
function fillIconCategories() {
  const cats = [...new Set(state.info.icons.map(i => i.category))], old = $('#icon-cat').value;
  $('#icon-cat').replaceChildren(new Option(`Alle (${state.info.icons.length})`, ''), ...cats.map(c => new Option(c, c)));
  $('#icon-cat').value = cats.includes(old) ? old : '';
}
function markIcon() {
  $('#icons').querySelectorAll('.icon').forEach(b => b.classList.toggle('on', b.dataset.name === state.icon));
  const sel = state.info.icons.find(i => i.name === state.icon);
  $('#icon-hint').textContent = (sel ? `Ausgewählt: ${sel.label}` : state.icon ? `Icon „${state.icon}“ gibt es nicht mehr.` : 'Noch kein Icon ausgewählt.')
    + ($('#icons').childElementCount ? '' : ' Keine Treffer für diese Suche.');
}
function renderIcons() {
  if (state.gmode !== 'icon') return;
  const words = plain($('#icon-search').value).split(/\s+/).filter(Boolean), cat = $('#icon-cat').value;
  const hits = state.info.icons.filter(i => {
    const text = plain(`${i.label} ${i.name} ${i.category} ${i.words}`);
    return (!cat || i.category === cat) && words.every(w => text.includes(w));
  });
  $('#icons').replaceChildren(...hits.map(i => {
    const b = el('button', { className: 'icon', title: i.label + (i.own ? ' (Rechtsklick: löschen)' : '') },
      el('img', { loading: 'lazy', src: '/api/icons/' + encodeURIComponent(i.name), alt: '' }), el('span', { textContent: i.label }));
    b.dataset.name = i.name;
    b.onclick = () => { state.icon = i.name; markIcon(); schedulePreview(); };
    if (i.own) b.oncontextmenu = guarded(async e => {
      e.preventDefault();
      if (!confirm(`Icon „${i.name}“ löschen?`)) return;
      await api('/api/icons/' + encodeURIComponent(i.name), 'DELETE'); await loadInfo(); renderIcons();
    });
    return b;
  }));
  markIcon();
  const on = $('#icons .on'); $('#icons').scrollTop = on ? on.offsetTop - 8 : 0;
}
function syncMedia() {
  const v = String(num('#length'));
  $('#media').value = [...$('#media').options].some(o => o.value === v) ? v : 'custom';
}
function schedulePreview() { clearTimeout(previewTimer); previewTimer = setTimeout(renderPreview, 200); }
async function renderPreview() {
  const seq = ++previewSeq, warn = $('#pwarn');
  try {
    const r = await api('/api/preview', 'POST', { label: labelSpec() });
    if (seq !== previewSeq) return;
    $('#preview').src = 'data:image/png;base64,' + r.png;
    $('#preview').style.width = Math.round(r.length_mm * 6) + 'px';
    $('#pmeta').textContent = `${r.length_mm.toFixed(0)} × 24 mm`;
    warn.textContent = r.warnings.join(' '); warn.classList.toggle('hidden', !r.warnings.length);
  } catch (e) {
    if (seq !== previewSeq) return;
    warn.textContent = e.message; warn.classList.remove('hidden');
  }
}
async function doPrint() {
  const b = $('#print'); if (b.disabled) return;
  b.disabled = true; b.textContent = 'Druckt …';
  try {
    const r = await api('/api/print', 'POST', { label: labelSpec(), copies: parseInt($('#copies').value) || 1 });
    toast(`${r.copies}× gedruckt (${r.length_mm} mm)`);
  } catch (e) { toast(e.message, true); }
  b.disabled = false; b.textContent = 'Drucken'; refreshStatus();
}

// --- templates ------------------------------------------------------------
async function loadTemplates() {
  const t = await api('/api/templates'), names = Object.keys(t).sort((a, b) => a.localeCompare(b, 'de'));
  $('#tpl-empty').classList.toggle('hidden', names.length > 0);
  $('#tpls').replaceChildren(...names.map(n => {
    const img = el('img', { alt: '' });
    api('/api/preview', 'POST', { label: t[n] }).then(r => img.src = 'data:image/png;base64,' + r.png).catch(() => {});
    const btn = (text, fn) => el('button', { className: 'btn secondary', textContent: text, onclick: guarded(fn) });
    return el('div', { className: 'tpl' }, el('b', { textContent: n, title: n }), img, el('div', { className: 'row' },
      btn('Öffnen', () => { applySpec(t[n]); showTab('editor'); }),
      btn('Drucken', async e => {
        e.target.disabled = true;
        try { await api('/api/print', 'POST', { label: t[n], copies: 1 }); toast(`„${n}“ gedruckt`); }
        finally { e.target.disabled = false; }
      }),
      btn('Löschen', async () => {
        if (!confirm(`Vorlage „${n}“ löschen?`)) return;
        await api('/api/templates?name=' + encodeURIComponent(n), 'DELETE'); await loadTemplates();
      })));
  }));
  return t;
}
async function saveTemplate() {
  const name = (prompt('Name der Vorlage:') || '').trim(); if (!name) return;
  const t = await api('/api/templates');
  if (name in t && !confirm(`„${name}“ gibt es schon. Überschreiben?`)) return;
  await api('/api/templates', 'POST', { name, label: labelSpec() }); toast('Vorlage gespeichert');
}
async function exportTemplates() {
  const blob = new Blob([JSON.stringify(await api('/api/templates'), null, 2)], { type: 'application/json' });
  const a = el('a', { href: URL.createObjectURL(blob), download: 'templates.json' }); a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
async function importTemplates() {
  const f = $('#import-file').files[0]; if (!f) return;
  let data; try { data = JSON.parse(await f.text()); } catch { throw new Error('Die Datei ist kein gültiges JSON.'); }
  $('#import-file').value = '';
  const have = await api('/api/templates');
  const clash = Object.keys(data).filter(n => n in have).length;
  const overwrite = clash > 0 && confirm(`${clash} Vorlage(n) gibt es schon. Überschreiben?`);
  const r = await api('/api/templates/import', 'POST', { templates: data, overwrite });
  toast(`${r.saved} importiert` + (r.skipped ? `, ${r.skipped} übersprungen` : '')); await loadTemplates();
}

// --- settings -------------------------------------------------------------
function fillSettings() {
  const s = state.settings;
  setSeg('#density', s.density); $('#fine').checked = s.fine;
  $('#dfont').value = s.default_font; if (!$('#dfont').value) $('#dfont').selectedIndex = 0;
  $('#dlen').value = s.default_length_mm;
  $('#cal-from').value = s.area_top_mm; $('#cal-to').value = 24 - s.area_bottom_mm;
  $('#mcp-on').checked = s.mcp_enabled; $('#mcp-box').classList.toggle('hidden', !s.mcp_enabled);
  $('#nopw').classList.toggle('hidden', state.info.password_set);
  $('#version').textContent = 'Version ' + state.info.version;
}
async function saveSettings(changes, msg = 'Gespeichert') {
  state.settings = await api('/api/settings', 'POST', changes); fillSettings(); schedulePreview(); toast(msg);
}
async function showMcp() {
  if (!state.settings.mcp_enabled) return;
  const m = await api('/api/mcp'), url = location.origin + '/mcp';
  $('#mcp-url').value = url;
  $('#mcp-cmd').value = `claude mcp add --transport http etiketten ${url} --header "Authorization: Bearer ${m.token}"`;
}
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    $('#sdot').className = 'dot ' + (!s.connected ? 'err' : s.ready ? 'ok' : 'warn');
    $('#stext').textContent = s.message;
    $('#pinfo').textContent = s.connected
      ? `${s.product || 'SLP100'}: ${s.message}` + (s.port_status ? ` (Port ${s.port_status}, Status ${s.status_byte || '–'})` : '') + ' · 203 dpi, 192 Punkte (24 mm)'
      : s.message;
  } catch { $('#sdot').className = 'dot err'; $('#stext').textContent = 'Server nicht erreichbar'; }
}
function showTab(t) {
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  document.querySelectorAll('.tab').forEach(s => s.classList.toggle('active', s.id === 'tab-' + t));
  if (t === 'templates') guarded(loadTemplates)();
  if (t === 'settings') { refreshStatus(); guarded(showMcp)(); }
}

async function loadInfo() {
  state.info = await api('/api/info'); state.settings = state.info.settings; fillIconCategories();
}
async function init() {
  await loadInfo();
  for (const id of ['#font', '#dfont']) $(id).replaceChildren(...state.info.fonts.map(f => new Option(f, f)));
  setFont('');
  $('#media').replaceChildren(new Option('Automatisch (passend zum Inhalt)', '0'),
    ...state.info.media.map(m => new Option(`${m.name} – ${m.width} × ${m.length} mm`, String(m.length))),
    new Option('Eigene Länge …', 'custom'));
  $('#length').value = state.settings.default_length_mm; syncMedia();
  $('#copies').max = state.info.max_copies;
  fillSettings();

  seg('#gmode', 'gmode', () => { updateGraphicUI(); schedulePreview(); });
  seg('#gpos', 'gpos', schedulePreview); seg('#align', 'align', schedulePreview); seg('#density');
  for (const id of ['#text', '#qr', '#size', '#margin']) $(id).addEventListener('input', schedulePreview);
  for (const id of ['#font', '#spacing', '#bold', '#border', '#invert', '#imgrot']) $(id).addEventListener('change', schedulePreview);
  $('#length').addEventListener('input', () => { syncMedia(); schedulePreview(); });
  $('#gscale').addEventListener('input', () => { $('#gscale-val').textContent = $('#gscale').value + ' %'; schedulePreview(); });
  $('#media').addEventListener('change', () => {
    if ($('#media').value === 'custom') $('#length').focus(); else $('#length').value = $('#media').value;
    schedulePreview();
  });
  $('#icon-search').addEventListener('input', renderIcons); $('#icon-cat').addEventListener('change', renderIcons);
  $('#img').addEventListener('change', () => {
    const f = $('#img').files[0]; if (!f) return;
    if (f.size > 5e6) return toast('Das Bild ist zu groß (höchstens 5 MB).', true);
    const r = new FileReader(); r.onload = () => { state.image = r.result; updateGraphicUI(); schedulePreview(); }; r.readAsDataURL(f);
  });
  $('#to-icon').onclick = guarded(async () => {
    const name = prompt('Name des Icons:'); if (!name) return;
    const r = await api('/api/icons', 'POST', { name, image: state.image }); await loadInfo();
    state.gmode = 'icon'; state.icon = r.name; setSeg('#gmode', 'icon'); updateGraphicUI(); schedulePreview(); toast('Icon gespeichert');
  });
  $('#print').onclick = doPrint;
  $('#feed').onclick = guarded(async () => { await api('/api/feed', 'POST'); toast('Etikett vorgeschoben'); });
  $('#save').onclick = guarded(saveTemplate);
  $('#export').onclick = guarded(exportTemplates);
  $('#import').onclick = () => $('#import-file').click();
  $('#import-file').onchange = guarded(importTemplates);
  $('#refresh').onclick = refreshStatus;
  $('#calib').onclick = guarded(async () => { await api('/api/calibrate', 'POST'); toast('Kalibrier-Etikett gedruckt'); });
  $('#save-cal').onclick = guarded(() => saveSettings({ area_top_mm: num('#cal-from'), area_bottom_mm: 24 - num('#cal-to', 24) }));
  $('#save-print').onclick = guarded(() => saveSettings({
    density: parseInt($('#density .on')?.dataset.v ?? 1), fine: $('#fine').checked,
    default_font: $('#dfont').value, default_length_mm: num('#dlen') }));
  $('#mcp-on').onchange = guarded(async () => {
    const on = $('#mcp-on').checked;
    await saveSettings({ mcp_enabled: on }, on ? 'MCP-Server aktiviert' : 'MCP-Server deaktiviert'); await showMcp();
  });
  $('#mcp-copy').onclick = guarded(async () => {
    const t = $('#mcp-cmd'); t.select();     // clipboard API needs HTTPS, so fall back
    if (navigator.clipboard) await navigator.clipboard.writeText(t.value); else document.execCommand('copy');
    toast('Kopiert');
  });
  document.querySelectorAll('nav button').forEach(b => b.onclick = () => showTab(b.dataset.tab));
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'p') { e.preventDefault(); doPrint(); } });

  renderPreview(); refreshStatus(); setInterval(() => { if (!document.hidden) refreshStatus(); }, 15000);
}
init().catch(e => toast('Start fehlgeschlagen: ' + e.message, true));
