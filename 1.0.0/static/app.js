// 影片库前端交互
const $ = (id) => document.getElementById(id);

let currentDateField = 'added_at';
let editingId = null;
let lastScrapedCover = '';
let lastScrapedCoverUrl = '';
let actressOptions = [];
const isAdmin = document.body.dataset.isAdmin === '1';
let lastDate = document.body.dataset.lastDate || '';

function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 1800);
}

async function api(url, options = {}) {
  const r = await fetch(url, options);
  if (r.status === 401) { location.href = '/login'; return null; }
  return r.json();
}

// 记住当前账号最近操作的日期分组，首页在该日期旁显示红字
async function setLastPosition(dateKey, movieId = 0) {
  if (!dateKey) return;
  lastDate = dateKey;
  await api('/api/last-position', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({date: dateKey, movie_id: movieId || 0})
  });
}

// ---------- 筛选栏 ----------
function initDateSeg() {
  const seg = $('dateTypeSeg');
  const initial = seg.dataset.initial || 'added_at';
  currentDateField = initial;
  [...seg.querySelectorAll('.seg-item')].forEach(btn => {
    btn.classList.toggle('active', btn.dataset.field === initial);
    btn.addEventListener('click', () => {
      currentDateField = btn.dataset.field;
      [...seg.querySelectorAll('.seg-item')].forEach(b => b.classList.toggle('active', b === btn));
    });
  });
}

async function loadActresses() {
  const res = await api('/api/actresses');
  if (!res || !res.ok) return;
  const sel = $('actressFilter');
  const selected = sel.value || sel.dataset.selected || '';
  // 保留“全部”以及点击卡片演员名时动态加入的选项，其余重建，避免重复
  const extra = [...sel.options].filter(o => o.value && !o.dataset.fromApi);
  sel.innerHTML = '<option value="">全部</option>';
  res.actresses.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name; opt.dataset.fromApi = '1';
    sel.appendChild(opt);
  });
  extra.forEach(opt => {
    if (![...sel.options].some(o => o.value === opt.value)) sel.appendChild(opt);
  });
  sel.value = selected || sel.dataset.selected || '';
  if (!sel.value) sel.dataset.selected = '';
}

function queryString() {
  const p = new URLSearchParams();
  p.set('date_field', currentDateField);
  if ($('dateFrom').value) p.set('date_from', $('dateFrom').value);
  if ($('dateTo').value) p.set('date_to', $('dateTo').value);
  if ($('actressFilter').value) p.set('actress', $('actressFilter').value);
  if ($('keyword').value.trim()) p.set('keyword', $('keyword').value.trim());
  return p.toString();
}

// ---------- 卡片渲染 ----------
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 帧率保存纯数字，显示时统一补 fps 后缀（兼容旧数据里已带 fps 的值）
function fpsText(value) {
  const v = String(value || '').trim();
  if (!v) return '';
  return /fps$/i.test(v) ? v : v + 'fps';
}

function dateGroupKey(m) {
  const value = (m[currentDateField] || '').slice(0, 10);
  return value || '未标注日期';
}

function prettyDate(key) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key;
  const [y, m, d] = key.split('-');
  return `${y}年${Number(m)}月${Number(d)}日`;
}

function renderMovies(movies) {
  const grid = $('movieGrid');
  grid.innerHTML = '';
  $('totalCount').textContent = movies.length;
  $('empty').hidden = movies.length > 0;
  $('empty').textContent = '没有符合条件的影片，换个筛选条件试试。';

  // 按当前日期模式分组：同一天放一起，最新日期在最上面
  const groups = new Map();
  movies.forEach(m => {
    const key = dateGroupKey(m);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(m);
  });
  const sortedKeys = [...groups.keys()].sort((a, b) => {
    if (a === '未标注日期') return 1;
    if (b === '未标注日期') return -1;
    return b.localeCompare(a);
  });

  sortedKeys.forEach(key => {
    const section = document.createElement('section');
    section.className = 'date-group';
    const label = document.createElement('div');
    label.className = 'date-group-head';
    label.innerHTML = `
      <label class="date-check" title="勾选该日期的全部影片">
        <input type="checkbox" class="date-group-check" data-date="${escapeHtml(key)}">
        <span>${escapeHtml(prettyDate(key))}</span>
      </label>
      ${key === lastDate ? '<span class="last-here">（上次到这里）</span>' : ''}
      <span class="date-group-count">${groups.get(key).length} 部</span>`;
    section.appendChild(label);
    const cards = document.createElement('div');
    cards.className = 'date-group-grid';
    section.appendChild(cards);

    groups.get(key).forEach(m => {
    const card = document.createElement('div');
    card.className = 'movie-card';

    // 封面：优先本地下载图，其次用抓取到的外链（no-referrer 规避防盗链）
    const cover = m.cover_path
      ? `<img src="/thumbs/${encodeURIComponent(m.cover_path)}" alt="封面" loading="lazy">`
      : m.cover_url
        ? `<img src="${escapeHtml(m.cover_url)}" alt="封面" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'cover-placeholder',textContent:'暂无封面'}))">`
        : `<div class="cover-placeholder">暂无封面</div>`;

    const tags = [];
    (m.resolution || '').split(/[,，/]/).map(s => s.trim()).filter(Boolean).forEach(r =>
      tags.push(`<span class="tag tag-res">${escapeHtml(r)}</span>`));
    if (m.is_original) tags.push(`<span class="tag tag-original">原盘</span>`);
    if (m.is_bluray) tags.push(`<span class="tag tag-bluray">蓝光</span>`);
    if (m.is_remux) tags.push(`<span class="tag tag-remux">Remux</span>`);
    if (m.framerate) tags.push(`<span class="tag tag-fps">${escapeHtml(fpsText(m.framerate))}</span>`);
    // 备注按顿号/逗号分隔，每段显示成一个标签
    (m.note || '').split(/[、,，]/).map(s => s.trim()).filter(Boolean).forEach(n =>
      tags.push(`<span class="tag tag-note">${escapeHtml(n)}</span>`));

    // 演员名可点击筛选
    const actressLinks = (m.actress || []).map(a =>
      `<span class="actress-link" data-actress="${escapeHtml(a)}" title="点击筛选该演员">${escapeHtml(a)}</span>`
    ).join('、');
    card.innerHTML = `
      <a class="cover-wrap detail-link" href="/movie/${m.id}?date_field=${encodeURIComponent(currentDateField)}&date=${encodeURIComponent(dateGroupKey(m))}" title="查看详情">
        ${cover}
        <span class="code-badge">${escapeHtml(m.code)}</span>
      </a>
      <div class="card-body">
        ${tags.length ? `<div class="tags">${tags.join('')}</div>` : ''}
        <a class="card-title detail-link" href="/movie/${m.id}?date_field=${encodeURIComponent(currentDateField)}&date=${encodeURIComponent(dateGroupKey(m))}" title="查看详情">${escapeHtml(m.title) || '&nbsp;'}</a>
        <div class="card-actress">${actressLinks || '&nbsp;'}</div>
        <div class="card-dates">
          <span>发行 <b>${escapeHtml(m.release_date) || '-'}</b></span>
          <span>录入 <b>${escapeHtml(m.added_at) || '-'}</b></span>
        </div>
        <div class="card-foot">
          <button class="copy-btn" data-copy="${m.id}" data-url="${escapeHtml(m.download_url)}">复制</button>
          ${isAdmin ? `<button class="edit-btn" data-edit="${m.id}" title="编辑">✎</button>
          <button class="card-del-btn" data-del="${m.id}" title="删除此影片">✕</button>` : ''}
          <label class="select-btn" title="勾选以多选">
            <input type="checkbox" class="movie-check" data-id="${m.id}" data-url="${escapeHtml(m.download_url)}" data-date="${escapeHtml(dateGroupKey(m))}">
          </label>
        </div>
      </div>`;
    cards.appendChild(card);
    });
    grid.appendChild(section);
  });

  grid.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', () => copyLink(btn));
  });
  grid.querySelectorAll('[data-edit]').forEach(btn => {
    btn.addEventListener('click', () => openModal(Number(btn.dataset.edit)));
  });
  grid.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', () => removeMovie(Number(btn.dataset.del)));
  });
  // 点击演员名 → 按该演员（含别名组）筛选
  grid.querySelectorAll('.actress-link').forEach(el => {
    el.addEventListener('click', () => {
      const name = el.dataset.actress;
      const sel = $('actressFilter');
      // 下拉里没有该名字（如别名）时补一个选项，保证能选中
      if (![...sel.options].some(o => o.value === name)) {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        sel.appendChild(opt);
      }
      sel.value = name;
      loadMovies();
      window.scrollTo({top: 0, behavior: 'smooth'});
    });
  });
  updateSelectUI();
}

async function loadMovies() {
  const res = await api('/api/movies?' + queryString());
  if (!res || !res.ok) return;
  renderMovies(res.movies);
}

// 通用剪贴板写入（带降级方案）
async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
}

// 复制与下载合并为一个按钮：有下载地址则复制，否则提示先补录
async function copyLink(btn) {
  const url = btn.dataset.url || '';
  if (!url) { toast('该影片还没有下载地址，请先编辑补录'); return; }
  await writeClipboard(url);
  btn.classList.add('copied');
  btn.textContent = '已复制';
  toast('下载地址已复制');
  setTimeout(() => { btn.classList.remove('copied'); btn.textContent = '复制'; }, 1500);
}

// ---------- 多选 / 全选 / 一键复制 ----------
function selectedMovies() {
  return [...document.querySelectorAll('.movie-check:checked')];
}

function updateSelectUI() {
  const checked = selectedMovies();
  const all = document.querySelectorAll('.movie-check');
  $('selectCount').textContent = `已选 ${checked.length} 部`;
  $('batchCopyBtn').disabled = checked.length === 0;
  // 全选复选框状态：全选 / 部分选 / 未选
  const selAll = $('selectAll');
  selAll.checked = all.length > 0 && checked.length === all.length;
  selAll.indeterminate = checked.length > 0 && checked.length < all.length;
  // 每组日期复选框状态：该组全选 / 部分选 / 未选
  document.querySelectorAll('.date-group-check').forEach(dc => {
    const boxes = [...document.querySelectorAll('.movie-check')].filter(c => c.dataset.date === dc.dataset.date);
    const n = boxes.filter(c => c.checked).length;
    dc.checked = boxes.length > 0 && n === boxes.length;
    dc.indeterminate = n > 0 && n < boxes.length;
  });
}

function bindSelectAll() {
  $('selectAll').addEventListener('change', e => {
    document.querySelectorAll('.movie-check').forEach(c => { c.checked = e.target.checked; });
    updateSelectUI();
  });
  document.getElementById('movieGrid').addEventListener('change', e => {
    if (e.target.classList.contains('movie-check')) updateSelectUI();
    if (e.target.classList.contains('date-group-check')) {
      const date = e.target.dataset.date;
      document.querySelectorAll('.movie-check').forEach(c => {
        if (c.dataset.date === date) c.checked = e.target.checked;
      });
      updateSelectUI();
    }
  });
  $('batchCopyBtn').addEventListener('click', async () => {
    const urls = selectedMovies()
      .map(c => c.dataset.url)
      .filter(u => u && u.trim());
    if (!urls.length) { toast('选中的影片都没有下载地址'); return; }
    await writeClipboard(urls.join('\n'));
    toast(`已复制 ${urls.length} 个下载地址`);
  });
}

// ---------- 弹窗 ----------
function openModal(id = null) {
  editingId = id;
  lastScrapedCover = '';
  lastScrapedCoverUrl = '';
  $('modalTitle').textContent = id ? '编辑影片' : '录入影片';
  $('fetchStatus').hidden = true;
  $('preview').hidden = true;
  ['fCode','fCode2','fTitle','fActress','fRelease','fAdded','fFramerate','fDownload','fNote'].forEach(i => $(i).value = '');
  $('fRes4K').checked = false;
  $('fRes1080').checked = false;
  $('fOriginal').checked = false;
  $('fBluray').checked = false;
  $('fRemux').checked = false;
  $('deleteBtn').hidden = !id;
  actressOptions = [];
  renderActressOptions();

  if (id) {
    api('/api/movies/' + id).then(res => {
      if (!res || !res.ok) return;
      const m = res.movie;
      $('fCode').value = m.code;
      $('fCode2').value = m.code;
      $('fTitle').value = m.title;
      $('fActress').value = (m.actress || []).join(', ');
      $('fRelease').value = m.release_date || '';
      $('fAdded').value = m.added_at || '';
      const resText = (m.resolution || '').toUpperCase();
      $('fRes4K').checked = resText.includes('4K');
      $('fRes1080').checked = resText.includes('1080');
      $('fFramerate').value = String(m.framerate || '').replace(/fps$/i, '').trim();
      $('fOriginal').checked = !!m.is_original;
      $('fBluray').checked = !!m.is_bluray;
      $('fRemux').checked = !!m.is_remux;
      $('fDownload').value = m.download_url || '';
      $('fNote').value = m.note || '';
      lastScrapedCover = m.cover_path || '';
      lastScrapedCoverUrl = m.cover_url || '';
      if (m.cover_path || m.cover_url) showPreview(m);
      loadCandidatesForCurrentActress();
    });
  }
  $('modal').hidden = false;
  setTimeout(() => $('fCode').focus(), 50);
}

function closeModal() { const el = $('modal'); if (el) el.hidden = true; }

function showPreview(data) {
  if (data.cover_path) {
    $('previewCover').src = '/covers/' + encodeURIComponent(data.cover_path) + '?t=' + Date.now();
    $('previewCover').removeAttribute('referrerpolicy');
    $('previewCover').style.display = '';
  } else if (data.cover_url) {
    $('previewCover').src = data.cover_url;
    $('previewCover').setAttribute('referrerpolicy', 'no-referrer');
    $('previewCover').style.display = '';
  } else {
    $('previewCover').style.display = 'none';
  }
  $('previewTitle').textContent = data.title || '（未抓到标题）';
  const parts = [];
  if ((data.actress || []).length) parts.push('演员：' + data.actress.join('、'));
  if (data.release_date) parts.push('发行：' + data.release_date);
  if (data.source) parts.push('来源：' + data.source);
  $('previewMeta').textContent = parts.join('　');
  $('preview').hidden = false;
}

// 抓取到的演员名如果有多个写法，列出来让用户手动点选（中文名排在最前）
function currentActressList() {
  return $('fActress').value.split(/[,，、]/).map(s => s.trim()).filter(Boolean);
}

function renderActressOptions() {
  const box = $('actressOptions');
  box.innerHTML = '';
  if (!actressOptions.length) { box.hidden = true; return; }

  const title = document.createElement('div');
  title.className = 'actress-options-title';
  title.textContent = '演员名字可点选（默认已转成中文名）';
  box.appendChild(title);

  actressOptions.forEach((item, index) => {
    const row = document.createElement('div');
    row.className = 'actress-option-row';
    const label = document.createElement('span');
    label.className = 'actress-option-label';
    label.textContent = item.original + '：';
    row.appendChild(label);

    const current = currentActressList()[index] || item.candidates[0];
    item.candidates.forEach(name => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'actress-option-btn' + (name === current ? ' active' : '');
      btn.textContent = name;
      btn.addEventListener('click', () => {
        const list = currentActressList();
        while (list.length < actressOptions.length) {
          list.push(actressOptions[list.length].candidates[0]);
        }
        list[index] = name;
        $('fActress').value = list.join(',');
        row.querySelectorAll('.actress-option-btn').forEach(b => b.classList.toggle('active', b === btn));
      });
      row.appendChild(btn);
    });
    box.appendChild(row);
  });
  box.hidden = false;
}

async function loadCandidatesForCurrentActress() {
  // 编辑旧记录时，也给已有演员名取候选，方便直接改成中文名
  const names = currentActressList();
  if (!names.length) { actressOptions = []; renderActressOptions(); return; }
  const results = await Promise.all(names.map(name =>
    api('/api/actress-candidates?name=' + encodeURIComponent(name))
  ));
  actressOptions = results.map((res, i) => ({
    original: names[i],
    candidates: (res && res.candidates) || [names[i]]
  }));
  renderActressOptions();
}

async function doFetch() {
  const code = $('fCode').value.trim();
  if (!code) { toast('请先输入编号'); return; }
  const status = $('fetchStatus');
  status.hidden = false;
  status.classList.remove('error');
  status.textContent = '正在抓取两个站点…';
  $('fetchBtn').disabled = true;

  const res = await api('/api/scrape', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code})
  });
  $('fetchBtn').disabled = false;
  if (!res || !res.ok) { status.textContent = '抓取请求失败'; status.classList.add('error'); return; }

  const d = res.data;
  // 把抓到的字段回填表单
  $('fCode2').value = d.code;
  $('fTitle').value = d.title;
  $('fActress').value = (d.actress || []).join(', ');
  actressOptions = d.actress_options || [];
  renderActressOptions();
  $('fRelease').value = d.release_date || '';
  lastScrapedCover = d.cover_path || '';
  lastScrapedCoverUrl = d.cover_url || '';
  showPreview(d);

  const filled = [d.title && '标题', d.actress?.length && '演员', d.release_date && '发行时间'].filter(Boolean);
  const errs = (d.errors || []).length ? '（' + d.errors.join('；') + '）' : '';
  status.textContent = filled.length
    ? `已抓取：${filled.join('、')}${errs}。请核对并补录左侧字段。`
    : `两站均未抓到信息${errs}，请手动填写。`;
  if (!filled.length) status.classList.add('error');
}

async function saveMovie() {
  const payload = {
    code: $('fCode2').value.trim(),
    title: $('fTitle').value.trim(),
    actress: $('fActress').value.trim(),
    release_date: $('fRelease').value,
    added_at: $('fAdded').value,
    resolution: [$('fRes4K').checked ? '4K' : '', $('fRes1080').checked ? '1080P' : ''].filter(Boolean).join(','),
    framerate: $('fFramerate').value.trim().replace(/fps$/i, '').trim(),
    is_original: $('fOriginal').checked ? 1 : 0,
    is_bluray: $('fBluray').checked ? 1 : 0,
    is_remux: $('fRemux').checked ? 1 : 0,
    download_url: $('fDownload').value.trim(),
    note: $('fNote').value.trim(),
    cover_path: lastScrapedCover,
    cover_url: lastScrapedCoverUrl,
    source: $('fetchStatus').textContent.includes('已抓取') ? 'javbus/javdb' : 'manual'
  };
  if (!payload.code) { toast('编号不能为空'); return; }
  const url = editingId ? '/api/movies/' + editingId : '/api/movies';
  const res = await api(url, {
    method: editingId ? 'PUT' : 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (!res || !res.ok) { toast(res?.error || '保存失败'); return; }
  const today = new Date().toLocaleDateString('sv-SE');
  const dateKey = currentDateField === 'release_date'
    ? (payload.release_date || '未标注日期')
    : (payload.added_at || today);
  await setLastPosition(dateKey, editingId || 0);
  toast(editingId ? '已更新' : '已入库');
  closeModal();
  loadMovies();
}

async function removeMovie(id) {
  if (!confirm('确定删除这条记录吗？封面也会一并删除。')) return;
  const box = document.querySelector(`.movie-check[data-id="${id}"]`);
  const dateKey = box ? box.dataset.date : '';
  const res = await api('/api/movies/' + id, {method: 'DELETE'});
  if (res && res.ok) {
    await setLastPosition(dateKey, id);
    toast('已删除');
    loadMovies();
  }
}

// ---------- 演员别名设置 ----------
let aliasGroups = [];

function aliasInput(value, placeholder, onRemove) {
  const row = document.createElement('div');
  row.className = 'alias-input-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.value = value || '';
  input.placeholder = placeholder;
  row.appendChild(input);
  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'alias-remove';
  del.title = '移除';
  del.textContent = '×';
  del.addEventListener('click', onRemove);
  row.appendChild(del);
  return row;
}

// 把页面上正在编辑的内容回写到内存，避免增删行时丢失未保存的输入
function syncAliasGroups() {
  aliasGroups = [...$('aliasList').querySelectorAll('.alias-group')].map(group => {
    const values = [...group.querySelectorAll('.alias-input-row input')].map(i => i.value.trim());
    return { name: values[0] || '', aliases: values.slice(1) };
  });
}

function renderAliasGroups() {
  const box = $('aliasList');
  box.innerHTML = '';
  aliasGroups.forEach((g, gi) => {
    const group = document.createElement('div');
    group.className = 'alias-group';

    const head = document.createElement('div');
    head.className = 'alias-group-head';
    head.innerHTML = `<span class="alias-group-label">第 ${gi + 1} 组</span>`;
    const delGroup = document.createElement('button');
    delGroup.type = 'button';
    delGroup.className = 'alias-group-del';
    delGroup.textContent = '删除此组';
    delGroup.addEventListener('click', () => {
      syncAliasGroups();
      aliasGroups.splice(gi, 1);
      renderAliasGroups();
    });
    head.appendChild(delGroup);
    group.appendChild(head);

    // 第一行即统一显示名
    group.appendChild(aliasInput(g.name || '', '统一显示名（第一行为准）', () => {
      syncAliasGroups();
      aliasGroups[gi].name = '';
      renderAliasGroups();
    }));
    (g.aliases || []).forEach((a, ai) => {
      group.appendChild(aliasInput(a, '别名', () => {
        syncAliasGroups();
        aliasGroups[gi].aliases.splice(ai, 1);
        renderAliasGroups();
      }));
    });

    const addAlias = document.createElement('button');
    addAlias.type = 'button';
    addAlias.className = 'alias-add';
    addAlias.textContent = '+ 添加别名';
    addAlias.addEventListener('click', () => {
      syncAliasGroups();
      aliasGroups[gi].aliases.push('');
      renderAliasGroups();
      const inputs = $('aliasList').querySelectorAll('.alias-group')[gi].querySelectorAll('.alias-input-row input');
      inputs[inputs.length - 1].focus();
    });
    group.appendChild(addAlias);
    box.appendChild(group);
  });
  if (!aliasGroups.length) {
    box.innerHTML = '<div class="alias-empty">还没有分组，点下面的按钮添加。</div>';
  }
}

function collectAliasGroups() {
  syncAliasGroups();
  return aliasGroups
    .map(g => ({ name: (g.name || '').trim(), aliases: (g.aliases || []).map(a => (a || '').trim()).filter(Boolean) }))
    .filter(g => g.name);
}

async function openAliasModal() {
  const res = await api('/api/aliases');
  if (!res || !res.ok) return;
  aliasGroups = (res.aliases || []).map(g => ({
    name: g.name || '',
    aliases: [...(g.aliases || [])]
  }));
  renderAliasGroups();
  $('aliasModal').hidden = false;
}

function closeAliasModal() { $('aliasModal').hidden = true; }

async function saveAliases() {
  const groups = collectAliasGroups();
  const res = await api('/api/aliases', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({aliases: groups})
  });
  if (!res || !res.ok) { toast('保存别名失败'); return; }
  toast('演员别名已保存');
  closeAliasModal();
  await loadActresses();
  await loadMovies();
}

// ---------- 管理员创建账号 ----------
function openUserModal() {
  $('uName').value = '';
  $('uPass').value = '';
  $('userModal').hidden = false;
  setTimeout(() => $('uName').focus(), 50);
}

function closeUserModal() { $('userModal').hidden = true; }

async function createUser() {
  const username = $('uName').value.trim();
  const password = $('uPass').value;
  if (!username || !password) { toast('请填写账号和密码'); return; }
  const res = await api('/api/users', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password})
  });
  if (!res || !res.ok) { toast(res?.error || '创建失败'); return; }
  toast(`账号 ${res.user.username} 已创建`);
  closeUserModal();
}

// ---------- 事件绑定 ----------
function bind() {
  $('searchBtn').addEventListener('click', loadMovies);
  $('resetBtn').addEventListener('click', () => {
    $('dateFrom').value = ''; $('dateTo').value = '';
    $('actressFilter').value = ''; $('keyword').value = '';
    initDateSeg(); loadMovies();
  });
  $('keyword').addEventListener('keydown', e => { if (e.key === 'Enter') loadMovies(); });
  if ($('addBtn')) $('addBtn').addEventListener('click', () => openModal());
  if ($('createUserBtn')) {
    $('createUserBtn').addEventListener('click', openUserModal);
    $('createUserSaveBtn').addEventListener('click', createUser);
    document.querySelectorAll('[data-user-close]').forEach(el => el.addEventListener('click', closeUserModal));
  }
  if ($('fetchBtn')) {
    $('fetchBtn').addEventListener('click', doFetch);
    $('saveBtn').addEventListener('click', saveMovie);
    $('deleteBtn').addEventListener('click', () => {
      if (editingId) removeMovie(editingId);
    });
    document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModal));
  }
  // 演员别名弹窗（仅管理员显示入口与弹窗）
  if ($('aliasManageBtn')) {
    $('aliasManageBtn').addEventListener('click', openAliasModal);
    $('addAliasGroupBtn').addEventListener('click', () => {
      syncAliasGroups();
      aliasGroups.push({ name: '', aliases: [] });
      renderAliasGroups();
      const inputs = $('aliasList').querySelectorAll('.alias-group:last-child .alias-input-row input');
      if (inputs.length) inputs[0].focus();
    });
    $('saveAliasBtn').addEventListener('click', saveAliases);
    document.querySelectorAll('[data-alias-close]').forEach(el => el.addEventListener('click', closeAliasModal));
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if ($('userModal') && !$('userModal').hidden) closeUserModal();
    else if ($('aliasModal') && !$('aliasModal').hidden) closeAliasModal();
    else closeModal();
  });
  bindSelectAll();
}

async function init() {
  initDateSeg();
  bind();
  await loadActresses();
  await loadMovies();
  // 从详情页点「编辑」回来时自动打开对应弹窗（普通账号不能编辑）
  const editId = new URLSearchParams(location.search).get('edit');
  if (editId && isAdmin) openModal(Number(editId));
}
init();
