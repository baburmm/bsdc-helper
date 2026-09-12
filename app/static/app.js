const rows = document.getElementById('rows');
const empty = document.getElementById('empty');
const drop = document.getElementById('drop');
const picker = document.getElementById('picker');
const hint = document.getElementById('hint');
const countEl = document.getElementById('count');
const progress = document.getElementById('progress');
const bar = progress.querySelector('i');
const label = progress.querySelector('.label');
const toast = document.getElementById('toast');

let limits = { max_mb: 0, allowed: [] };
let toastTimer;

function notify(message, kind = 'ok') {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = 'show ' + kind;
  toastTimer = setTimeout(() => (toast.className = kind), 4000);
}

function formatSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (res.status === 401) { location.href = '/login'; throw new Error('signed out'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function refresh() {
  const data = await api('/api/files');
  limits = data.limits;
  hint.textContent =
    `${limits.allowed.map(e => '.' + e).join(', ')} · up to ${limits.max_mb} MB each`;

  rows.replaceChildren();
  countEl.textContent = data.files.length ? `· ${data.files.length}` : '';
  empty.hidden = data.files.length > 0;

  for (const f of data.files) {
    const tr = document.createElement('tr');

    const name = document.createElement('td');
    name.className = 'name';
    name.textContent = f.name;

    const size = document.createElement('td');
    size.className = 'num';
    size.textContent = formatSize(f.size);

    const when = document.createElement('td');
    when.className = 'num when';
    when.textContent = formatDate(f.modified);

    const actions = document.createElement('td');
    actions.className = 'actions';

    const dl = document.createElement('button');
    dl.className = 'ghost';
    dl.textContent = 'Download';
    dl.onclick = () => { location.href = '/api/download/' + encodeURIComponent(f.name); };

    const del = document.createElement('button');
    del.className = 'danger';
    del.textContent = 'Delete';
    del.onclick = async () => {
      if (!confirm(`Delete "${f.name}"? This cannot be undone.`)) return;
      del.disabled = true;
      try {
        await api('/api/files/' + encodeURIComponent(f.name), { method: 'DELETE' });
        notify(`Deleted ${f.name}`);
        refresh();
      } catch (e) {
        notify(e.message, 'error');
        del.disabled = false;
      }
    };

    actions.append(dl, del);
    tr.append(name, size, when, actions);
    rows.append(tr);
  }
}

function uploadOne(file, index, total) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.style.width = pct + '%';
      label.textContent =
        `${total > 1 ? `(${index + 1}/${total}) ` : ''}${file.name} — ${pct}%`;
    };
    xhr.onload = () => {
      if (xhr.status === 401) { location.href = '/login'; return; }
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch {}
      xhr.status >= 200 && xhr.status < 300
        ? resolve(data)
        : reject(new Error(data.error || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.send(body);
  });
}

async function uploadAll(files) {
  const list = [...files];
  if (!list.length) return;

  progress.style.display = 'block';
  drop.style.pointerEvents = 'none';
  let done = 0;

  for (const [i, file] of list.entries()) {
    bar.style.width = '0%';
    try {
      await uploadOne(file, i, list.length);
      done++;
    } catch (e) {
      notify(`${file.name}: ${e.message}`, 'error');
    }
  }

  progress.style.display = 'none';
  bar.style.width = '0%';
  drop.style.pointerEvents = '';
  picker.value = '';
  if (done) notify(`Uploaded ${done} file${done > 1 ? 's' : ''}`);
  refresh();
}

drop.addEventListener('click', () => picker.click());
picker.addEventListener('change', () => uploadAll(picker.files));

['dragenter', 'dragover'].forEach(ev =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); })
);
['dragleave', 'drop'].forEach(ev =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); })
);
drop.addEventListener('drop', (e) => uploadAll(e.dataTransfer.files));
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => e.preventDefault());

document.getElementById('logout').onclick = async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.href = '/login';
};

refresh().catch(e => notify(e.message, 'error'));
