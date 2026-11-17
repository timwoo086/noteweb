// 详情页交互：复制下载地址、跳转到编辑
const $ = (id) => document.getElementById(id);

function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 1800);
}

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
}

$('copyBtn').addEventListener('click', async () => {
  const url = $('copyBtn').dataset.url || '';
  if (!url) { toast('该影片还没有下载地址'); return; }
  await writeClipboard(url);
  toast('下载地址已复制');
});

// 编辑按钮回到列表并自动打开录入/编辑弹窗（普通账号没有此按钮）
const editBtn = $('editBtn');
if (editBtn) {
  editBtn.addEventListener('click', () => {
    const id = location.pathname.split('/').pop();
    location.href = '/?edit=' + encodeURIComponent(id);
  });
}
