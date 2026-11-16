// 影片库前端交互
const $ = (id) => document.getElementById(id);

let currentDateField = 'added_at';
let editingId = null;
let lastScrapedCover = '';
let lastScrapedCoverUrl = '';

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
  res.actresses.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    sel.appendChild(opt);
  });
  if (sel.dataset.selected) sel.value = sel.dataset.selected;
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

function renderMovies(movies) {
  const grid = $('movieGrid');
  grid.innerHTML = '';
  $('totalCount').textContent = movies.length;
  $('empty').hidden = movies.length > 0;
  $('empty').textContent = '没有符合条件的影片，换个筛选条件试试。';

  movies.forEach(m => {
    const card = document.createElement('div');
    card.className = 'movie-card';

    // 封面：优先本地下载图，其次用抓取到的外链（no-referrer 规避防盗链）
    const cover = m.cover_path
      ? `<img src="/covers/${encodeURIComponent(m.cover_path)}" alt="封面" loading="lazy">`
      : m.cover_url
        ? `<img src="${escapeHtml(m.cover_url)}" alt="封面" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'cover-placeholder',textContent:'暂无封面'}))">`
        : `<div class="cover-placeholder">暂无封面</div>`;

    const tags = [];
    if (m.resolution) tags.push(`<span class="tag tag-res">${escapeHtml(m.resolution)}</span>`);
    if (m.is_original) tags.push(`<span class="tag tag-original">原档</span>`);
    if (m.is_remux) tags.push(`<span class="tag tag-remux">REMUX</span>`);
    if (m.framerate) tags.push(`<span class="tag tag-fps">${escapeHtml(m.framerate)}</span>`);

    const actresses = (m.actress || []).join('、');
    card.innerHTML = `
      <div class="cover-wrap">
        ${cover}
        <span class="code-badge">${escapeHtml(m.code)}</span>
        ${tags.length ? `<div class="tags">${tags.join('')}</div>` : ''}
      </div>
      <div class="card-body">
        <div class="card-title">${escapeHtml(m.title) || '&nbsp;'}</div>
        <div class="card-actress">${escapeHtml(actresses) || '&nbsp;'}</div>
        <div class="card-dates">
          <span>发行 <b>${escapeHtml(m.release_date) || '-'}</b></span>
          <span>录入 <b>${escapeHtml(m.added_at) || '-'}</b></span>
        </div>
        <div class="card-foot">
          <button class="copy-btn" data-copy="${m.id}" data-url="${escapeHtml(m.download_url)}">复制</button>
          <button class="edit-btn" data-edit="${m.id}" title="编辑">✎</button>
          <button class="del-btn" data-del="${m.id}" title="删除">×</button>
        </div>
      </div>`;
    grid.appendChild(card);
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
}

async function loadMovies() {
  const res = await api('/api/movies?' + queryString());
  if (!res || !res.ok) return;
  renderMovies(res.movies);
}

// 复制与下载合并为一个按钮：有下载地址则复制，否则提示先补录
async function copyLink(btn) {
  const url = btn.dataset.url || '';
  if (!url) { toast('该影片还没有下载地址，请先编辑补录'); return; }
  try {
    await navigator.clipboard.writeText(url);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  btn.classList.add('copied');
  btn.textContent = '已复制';
  toast('下载地址已复制');
  setTimeout(() => { btn.classList.remove('copied'); btn.textContent = '复制'; }, 1500);
}

// ---------- 弹窗 ----------
function openModal(id = null) {
  editingId = id;
  lastScrapedCover = '';
  lastScrapedCoverUrl = '';
  $('modalTitle').textContent = id ? '编辑影片' : '录入影片';
  $('fetchStatus').hidden = true;
  $('preview').hidden = true;
  ['fCode','fCode2','fTitle','fActress','fRelease','fAdded','fResolution','fFramerate','fDownload','fNote'].forEach(i => $(i).value = '');
  $('fOriginal').checked = false;
  $('fRemux').checked = false;

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
      $('fResolution').value = m.resolution || '';
      $('fFramerate').value = m.framerate || '';
      $('fOriginal').checked = !!m.is_original;
      $('fRemux').checked = !!m.is_remux;
      $('fDownload').value = m.download_url || '';
      $('fNote').value = m.note || '';
      lastScrapedCover = m.cover_path || '';
      lastScrapedCoverUrl = m.cover_url || '';
      if (m.cover_path || m.cover_url) showPreview(m);
    });
  }
  $('modal').hidden = false;
  setTimeout(() => $('fCode').focus(), 50);
}

function closeModal() { $('modal').hidden = true; }

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
    resolution: $('fResolution').value,
    framerate: $('fFramerate').value,
    is_original: $('fOriginal').checked ? 1 : 0,
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
  toast(editingId ? '已更新' : '已入库');
  closeModal();
  loadMovies();
}

async function removeMovie(id) {
  if (!confirm('确定删除这条记录吗？封面也会一并删除。')) return;
  const res = await api('/api/movies/' + id, {method: 'DELETE'});
  if (res && res.ok) { toast('已删除'); loadMovies(); }
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
  $('addBtn').addEventListener('click', () => openModal());
  $('fetchBtn').addEventListener('click', doFetch);
  $('saveBtn').addEventListener('click', saveMovie);
  document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModal));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
}

async function init() {
  initDateSeg();
  bind();
  await loadActresses();
  await loadMovies();
}
init();
