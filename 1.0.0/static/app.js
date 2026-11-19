// 影片库前端交互
const $ = (id) => document.getElementById(id);

let currentDateField = 'added_at';
let currentCategory = 'FT.A.V';
let editingId = null;
let lastScrapedCover = '';
let lastScrapedCoverUrl = '';
let lastScrapedSource = '';
let actressOptions = [];
const isAdmin = document.body.dataset.isAdmin === '1';
let lastDate = document.body.dataset.lastDate || '';

// 分页：首屏只加载第一页（默认 120 条），滚动到底部再追加，避免一次传输/渲染上千条
const PAGE_SIZE = 120;
let moviePage = 1;
let movieTotal = 0;
let movieHasMore = false;
let movieLoading = false;
let lastRenderedDateKey = null;   // 追加时用于合并跨页的同一日期组

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
  if (currentCategory) p.set('category', currentCategory);
  return p.toString();
}

// 顶部栏目标签切换：保留其他筛选条件，切换后重新加载
function initCategoryNav() {
  const nav = $('categoryNav');
  if (!nav) return;
  currentCategory = nav.dataset.current || 'FT.A.V';
  nav.querySelectorAll('.category-tab').forEach(a => {
    if (a.dataset.category === currentCategory) a.classList.add('active');
    a.addEventListener('click', e => {
      e.preventDefault();
      const next = a.dataset.category || 'FT.A.V';
      if (next === currentCategory) return;  // 点当前栏目不重复加载
      currentCategory = next;
      nav.querySelectorAll('.category-tab').forEach(x => x.classList.toggle('active', x === a));
      // 同步地址栏，便于刷新后保持栏目
      const url = new URL(location.href);
      url.searchParams.set('category', currentCategory);
      history.replaceState(null, '', url);
      loadMovies(true);  // 立即重新拉取并渲染，无需 F5
    });
  });
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

function renderMovies(movies, append = false) {
  const grid = $('movieGrid');
  if (!append) {
    grid.innerHTML = '';
    lastRenderedDateKey = null;
  }
  $('totalCount').textContent = movieTotal || movies.length;
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
    // 追加页若与上一页末尾同一天，复用该日期组，避免拆成两个相同的日期标题
    let section = null, cards = null;
    if (append && lastRenderedDateKey === key && grid.lastElementChild && grid.lastElementChild.classList.contains('date-group')) {
      section = grid.lastElementChild;
      cards = section.querySelector('.date-group-grid');
      const cnt = section.querySelector('.date-group-count');
      if (cnt) cnt.textContent = (parseInt(cnt.textContent, 10) || 0) + groups.get(key).length + ' 部';
    }
    if (!section) {
      section = document.createElement('section');
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
      cards = document.createElement('div');
      cards.className = 'date-group-grid';
      section.appendChild(cards);
      grid.appendChild(section);
    }

    groups.get(key).forEach(m => {
    const card = document.createElement('div');
    card.className = 'movie-card';

    // 封面：优先本地下载图，其次用抓取到的外链（no-referrer 规避防盗链）
    const cover = m.cover_path
      ? `<img src="/thumbs/${encodeURIComponent(m.cover_path)}?v=${encodeURIComponent(m.updated_at || '')}" alt="封面" loading="lazy">`
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
    const allUrls = (m.download_urls && m.download_urls.length) ? m.download_urls : [m.download_url].filter(Boolean);
    const urlsData = encodeURIComponent(JSON.stringify(allUrls));
    const copyLabel = allUrls.length > 1 ? `复制 ${allUrls.length} 部` : '复制';
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
        </div>
        <div class="card-foot">
          <button class="copy-btn" data-copy="${m.id}" data-urls="${urlsData}" title="${allUrls.length > 1 ? '一次复制全部 ' + allUrls.length + ' 个地址' : '复制下载地址'}">${copyLabel}</button>
          ${isAdmin ? `<button class="edit-btn" data-edit="${m.id}" title="编辑">✎</button>
          <button class="card-del-btn" data-del="${m.id}" title="删除此影片">✕</button>` : ''}
          <label class="select-btn" title="勾选以多选">
            <input type="checkbox" class="movie-check" data-id="${m.id}" data-urls="${urlsData}" data-date="${escapeHtml(dateGroupKey(m))}">
          </label>
        </div>
      </div>`;
    cards.appendChild(card);
    });
    lastRenderedDateKey = key;
  });

  // 卡片按钮用事件委托（在 bind() 里绑定到 grid 一次），追加页无需重复绑定
  updateSelectUI();
  updateLoadMoreUI();
}

// 卡片区域事件委托：首屏与滚动追加的卡片统一生效
function bindGridDelegates() {
  const grid = $('movieGrid');
  grid.addEventListener('click', e => {
    const copy = e.target.closest('[data-copy]');
    if (copy) { copyLink(copy); return; }
    const edit = e.target.closest('[data-edit]');
    if (edit) { openModal(Number(edit.dataset.edit)); return; }
    const del = e.target.closest('[data-del]');
    if (del) { removeMovie(Number(del.dataset.del)); return; }
    // 点击演员名 → 按该演员（含别名组）筛选
    const link = e.target.closest('.actress-link');
    if (link) {
      const name = link.dataset.actress;
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
    }
  });
}

function updateLoadMoreUI() {
  const bar = $('loadMore');
  if (!bar) return;
  const loaded = document.querySelectorAll('.movie-check').length;
  $('loadMoreText').textContent = `已显示 ${loaded} / 共 ${movieTotal || loaded} 部`;
  $('loadProgress').textContent = `已加载 ${loaded}/${movieTotal || loaded}`;
  bar.hidden = !movieHasMore;
  $('loadProgress').hidden = !movieHasMore;
}

// 重新加载（筛选/搜索/切换栏目/ force 刷新）：回到第 1 页
async function loadMovies(force = false) {
  const p = new URLSearchParams(queryString());
  p.set('page', '1');
  p.set('page_size', String(PAGE_SIZE));
  if (force) p.set('_t', String(Date.now()));
  const res = await api('/api/movies?' + p.toString(), {cache: 'no-store'});
  if (!res || !res.ok) return;
  moviePage = res.page || 1;
  movieTotal = res.total ?? res.movies.length;
  movieHasMore = !!res.has_more;
  renderMovies(res.movies, false);
}

// 滚动到底部加载下一页：追加到已有日期组，保留勾选状态
async function loadMore() {
  if (movieLoading || !movieHasMore) return;
  movieLoading = true;
  try {
    const p = new URLSearchParams(queryString());
    p.set('page', String(moviePage + 1));
    p.set('page_size', String(PAGE_SIZE));
    const res = await api('/api/movies?' + p.toString(), {cache: 'no-store'});
    if (!res || !res.ok) return;
    moviePage = res.page || (moviePage + 1);
    movieTotal = res.total ?? movieTotal;
    movieHasMore = !!res.has_more;
    renderMovies(res.movies, true);
  } finally {
    movieLoading = false;
  }
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

// 从 data-urls 解析地址数组（兼容旧的单条 data-url）
function urlsOf(el) {
  if (el.dataset.urls) {
    try {
      const arr = JSON.parse(decodeURIComponent(el.dataset.urls));
      if (Array.isArray(arr)) return arr.filter(u => u && String(u).trim());
    } catch (e) { /* 回退到单条 */ }
  }
  const one = el.dataset.url || '';
  return one ? [one] : [];
}

// 复制与下载合并为一个按钮：有下载地址则复制全部（多行），否则提示先补录
async function copyLink(btn) {
  const urls = urlsOf(btn);
  if (!urls.length) { toast('该影片还没有下载地址，请先编辑补录'); return; }
  await writeClipboard(urls.join('\n'));
  btn.classList.add('copied');
  const oldText = btn.textContent;
  btn.textContent = '已复制';
  toast(urls.length > 1 ? `已复制 ${urls.length} 个下载地址` : '下载地址已复制');
  setTimeout(() => { btn.classList.remove('copied'); btn.textContent = oldText; }, 1500);
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
  // 一键删除（仅管理员渲染了该按钮）：有勾选才可用
  const delBtn = $('batchDeleteBtn');
  if (delBtn) delBtn.disabled = checked.length === 0;
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
  $('selectAll').addEventListener('change', async e => {
    // 注意：加载更多会重建部分 DOM，全选框的 checked 可能被浏览器改写，
    // 因此在任何 await 之前先捕获用户意图。
    const wantChecked = e.target.checked;
    // 勾选全部时：若还有未加载的页，先把剩余页拉完，保证"全选"覆盖所有筛选结果
    if (wantChecked && movieHasMore) {
      const btn = $('loadMoreBtn');
      if (btn) btn.disabled = true;
      $('loadProgress').textContent = '正在加载全部影片…';
      try {
        let guard = 0;
        while (movieHasMore && guard++ < 100) await loadMore();
      } finally {
        if (btn) btn.disabled = false;
      }
    }
    document.querySelectorAll('.movie-check').forEach(c => { c.checked = wantChecked; });
    e.target.checked = wantChecked;
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
    const urls = selectedMovies().flatMap(c => urlsOf(c));
    if (!urls.length) { toast('选中的影片都没有下载地址'); return; }
    await writeClipboard(urls.join('\n'));
    toast(`已复制 ${urls.length} 个下载地址`);
  });
  // 一键删除（仅管理员可见）：逐条删除勾选的影片（含封面）
  const delBtn = $('batchDeleteBtn');
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      const ids = selectedMovies().map(c => c.dataset.id);
      if (!ids.length) return;
      if (!confirm(`确定删除选中的 ${ids.length} 部影片吗？封面也会一并删除，不可恢复。`)) return;
      delBtn.disabled = true;
      let ok = 0, fail = 0;
      for (const id of ids) {
        const res = await api('/api/movies/' + id, {method: 'DELETE'});
        if (res && res.ok) ok++; else fail++;
      }
      toast(fail ? `已删除 ${ok} 部，${fail} 部失败` : `已删除 ${ok} 部影片`);
      loadMovies();
    });
  }
}

// ---------- 弹窗 ----------
// 下载地址动态行：一部影片可有多个（上下两部）
function downloadRow(value = '') {
  const row = document.createElement('div');
  row.className = 'download-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'download-input';
  input.value = value || '';
  input.placeholder = '磁力链接 / 网盘地址 / 本地路径';
  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'download-remove';
  del.title = '删除此地址';
  del.textContent = '✕';
  del.addEventListener('click', () => {
    row.remove();
    if (!$('downloadList').children.length) addDownloadRow();
  });
  row.appendChild(input);
  row.appendChild(del);
  return row;
}

function addDownloadRow(value = '') {
  $('downloadList').appendChild(downloadRow(value));
}

function renderDownloadRows(urls) {
  const box = $('downloadList');
  box.innerHTML = '';
  (urls && urls.length ? urls : ['']).forEach(u => addDownloadRow(u));
}

function collectDownloads() {
  return [...$('downloadList').querySelectorAll('.download-input')]
    .map(i => i.value.trim()).filter(Boolean);
}

function bindDownloadAdd() {
  const btn = $('addDownloadBtn');
  if (btn) btn.addEventListener('click', () => { addDownloadRow(); $('downloadList').lastElementChild.querySelector('input').focus(); });
}

function openModal(id = null) {
  editingId = id;
  lastScrapedCover = '';
  lastScrapedCoverUrl = '';
  lastScrapedSource = '';
  $('modalTitle').textContent = id ? '编辑影片' : '录入影片';
  $('fetchStatus').hidden = true;
  $('preview').hidden = true;
  $('previewCover').removeAttribute('src');
  ['fCode','fCode2','fTitle','fActress','fRelease','fAdded','fFramerate','fNote'].forEach(i => $(i).value = '');
  renderDownloadRows([]);
  $('fCategory').value = currentCategory === 'LTX破解' ? 'LTX破解' : 'FT.A.V';
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
      renderDownloadRows(m.download_urls && m.download_urls.length ? m.download_urls : [m.download_url || '']);
      $('fCategory').value = m.category || 'FT.A.V';
      $('fNote').value = m.note || '';
      lastScrapedCover = m.cover_path || '';
      lastScrapedCoverUrl = m.cover_url || '';
      lastScrapedSource = m.source || '';
      if (m.cover_path || m.cover_url) showPreview(m);
      loadCandidatesForCurrentActress();
    });
  }
  $('modal').hidden = false;
  setTimeout(() => $('fCode').focus(), 50);
}

function closeModal() { const el = $('modal'); if (el) el.hidden = true; }

function showPreview(data) {
  const cover = $('previewCover');
  if (data.cover_path) {
    cover.src = '/covers/' + encodeURIComponent(data.cover_path) + '?t=' + Date.now();
    cover.removeAttribute('referrerpolicy');
  } else if (data.cover_url) {
    cover.src = data.cover_url;
    cover.setAttribute('referrerpolicy', 'no-referrer');
  } else {
    // 没有封面就整块隐藏，并清空 src，避免残留上一次抓取的图片
    cover.removeAttribute('src');
    $('preview').hidden = true;
    return;
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
  status.textContent = '正在抓取…';
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
  lastScrapedSource = d.source || '';
  showPreview(d);

  const filled = [d.title && '标题', d.actress?.length && '演员', d.release_date && '发行时间'].filter(Boolean);
  const errs = (d.errors || []).length ? '（' + d.errors.join('；') + '）' : '';
  status.textContent = filled.length
    ? `已抓取：${filled.join('、')}${errs}。请核对并补录左侧字段。`
    : `未抓到信息${errs}，请手动填写。`;
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
    download_urls: collectDownloads(),
    category: $('fCategory').value,
    note: $('fNote').value.trim(),
    cover_path: lastScrapedCover,
    cover_url: lastScrapedCoverUrl,
    source: $('fetchStatus').textContent.includes('已抓取') ? (lastScrapedSource || 'scrape') : 'manual'
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
  loadRenameOptions();
}

// 统一改名：下拉列出库里的演员名
async function loadRenameOptions() {
  const sel = $('renameFrom');
  const current = sel.value;
  const res = await api('/api/actresses');
  sel.innerHTML = '<option value="">选择要改名的演员</option>';
  (res?.actresses || []).forEach(name => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    sel.appendChild(opt);
  });
  if (current && [...sel.options].some(o => o.value === current)) sel.value = current;
}

async function renameActress() {
  const from = $('renameFrom').value.trim();
  const to = $('renameTo').value.trim();
  if (!from) { toast('请选择要改名的演员'); return; }
  if (!to) { toast('请填写新名字'); return; }
  if (from === to) { toast('新旧名字相同'); return; }
  if (!confirm(`确定把所有影片里的「${from}」统一改成「${to}」吗？`)) return;
  const res = await api('/api/actresses/rename', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({from, to})
  });
  if (!res || !res.ok) { toast(res?.error || '改名失败'); return; }
  toast(`已把「${from}」改为「${to}」，共更新 ${res.changed} 部影片`);
  $('renameTo').value = '';
  $('renameFrom').value = '';
  await loadActresses();
  await loadMovies();
  loadRenameOptions();
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

// ---------- 账号管理（查看全部账号 / 改密码 / 创建账号） ----------
async function loadUserList() {
  const box = $('userList');
  const res = await api('/api/users');
  if (!res || !res.ok) { box.innerHTML = '<div class="alias-empty">账号列表加载失败</div>'; return; }
  box.innerHTML = '';
  res.users.forEach(u => {
    const row = document.createElement('div');
    row.className = 'user-row';
    const name = document.createElement('span');
    name.className = 'user-row-name';
    name.textContent = u.username;
    row.appendChild(name);
    if (u.is_admin) {
      const badge = document.createElement('span');
      badge.className = 'admin-badge';
      badge.textContent = '管理员';
      row.appendChild(badge);
    }
    if (u.username === document.body.dataset.username) {
      const badge = document.createElement('span');
      badge.className = 'user-row-self';
      badge.textContent = '（当前登录）';
      row.appendChild(badge);
    }
    const pass = document.createElement('input');
    pass.type = 'password';
    pass.className = 'user-row-pass';
    pass.placeholder = '输入新密码（至少 4 位）';
    pass.autocomplete = 'new-password';
    row.appendChild(pass);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-ghost user-row-save';
    btn.textContent = '改密码';
    btn.addEventListener('click', async () => {
      const pwd = pass.value;
      if (!pwd || pwd.length < 4) { toast('密码至少 4 位'); return; }
      btn.disabled = true;
      const res2 = await api('/api/users/' + encodeURIComponent(u.username), {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: pwd})
      });
      btn.disabled = false;
      if (!res2 || !res2.ok) { toast(res2?.error || '修改失败'); return; }
      pass.value = '';
      toast(`账号 ${u.username} 的密码已更新`);
    });
    row.appendChild(btn);
    box.appendChild(row);
  });
}

async function openUserModal() {
  $('uName').value = '';
  $('uPass').value = '';
  $('userList').innerHTML = '<div class="alias-empty">加载中…</div>';
  $('userModal').hidden = false;
  setTimeout(() => $('uName').focus(), 50);
  loadUserList();
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
  $('uName').value = '';
  $('uPass').value = '';
  loadUserList();
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
    $('renameBtn').addEventListener('click', renameActress);
    document.querySelectorAll('[data-alias-close]').forEach(el => el.addEventListener('click', closeAliasModal));
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if ($('userModal') && !$('userModal').hidden) closeUserModal();
    else if ($('aliasModal') && !$('aliasModal').hidden) closeAliasModal();
    else closeModal();
  });
  bindSelectAll();
  bindGridDelegates();
  // 手动加载更多 + 滚动到底自动加载（首屏只拉一页，数据量大时按需取）
  const moreBtn = $('loadMoreBtn');
  if (moreBtn) moreBtn.addEventListener('click', loadMore);
  window.addEventListener('scroll', () => {
    if (!movieHasMore || movieLoading) return;
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 300) loadMore();
  }, {passive: true});
}

async function init() {
  initDateSeg();
  initCategoryNav();
  bindDownloadAdd();
  bind();
  // 演员列表与首屏影片并行拉取，减少首屏等待
  loadActresses();
  await loadMovies();
  // 从详情页点「编辑」回来时自动打开对应弹窗（普通账号不能编辑）
  const editId = new URLSearchParams(location.search).get('edit');
  if (editId && isAdmin) openModal(Number(editId));
}
init();
