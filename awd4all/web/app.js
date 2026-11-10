const state = {
  plugins: [],
  current: null,
  running: false,
  jobId: null,
  ws: null,
  started: 0,
  finished: 0,
  total: 0,
  flags: 0,
  errors: 0,
  findings: [],
  startedAt: 0,
  timer: null,
};

const $ = (id) => document.getElementById(id);

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `请求失败 (${response.status})`);
  }
  return data;
}

function toast(message, isError = false) {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = 'toast'; }, 2600);
}

/* ---------------- 插件 ---------------- */

async function loadPlugins() {
  try {
    const data = await api('/api/plugins');
    state.plugins = data.plugins || [];
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const list = $('plugin-list');
  list.innerHTML = '';
  if (!state.plugins.length) {
    list.innerHTML = '<p class="muted small">没有可用插件</p>';
    return;
  }
  state.plugins.forEach((plugin) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'plugin-item';
    button.innerHTML = `
      <span class="plugin-name">${escapeHtml(plugin.title)}</span>
      <span class="plugin-sub">${escapeHtml(plugin.name)}${plugin.default_port ? ' · :' + plugin.default_port : ''}</span>`;
    button.addEventListener('click', () => selectPlugin(plugin, button));
    list.appendChild(button);
  });
  selectPlugin(state.plugins[0], list.firstElementChild);
}

function selectPlugin(plugin, button) {
  state.current = plugin;
  document.querySelectorAll('.plugin-item').forEach((node) => node.classList.remove('active'));
  if (button) button.classList.add('active');
  $('plugin-title').textContent = plugin.title;
  $('plugin-desc').textContent = plugin.description || '';
  renderForm(plugin);
}

function renderForm(plugin) {
  const form = $('param-form');
  form.innerHTML = '';
  if (!plugin.params || !plugin.params.length) {
    form.innerHTML = '<p class="muted small">这个插件没有可调参数</p>';
    return;
  }
  plugin.params.forEach((param) => {
    const wide = param.type === 'textarea' || param.type === 'file';
    const wrap = document.createElement('div');
    wrap.className = 'param' + (wide ? ' wide' : '');

    const label = document.createElement('label');
    label.innerHTML = escapeHtml(param.label) + (param.required ? '<span class="required">*</span>' : '');
    wrap.appendChild(label);

    let field;
    if (param.type === 'textarea') {
      field = document.createElement('textarea');
      field.rows = param.rows || 4;
      field.value = param.default ?? '';
    } else if (param.type === 'select') {
      field = document.createElement('select');
      (param.options || []).forEach((option) => {
        const node = document.createElement('option');
        node.value = option;
        node.textContent = option;
        field.appendChild(node);
      });
      field.value = param.default ?? (param.options || [])[0] ?? '';
    } else if (param.type === 'bool') {
      field = document.createElement('input');
      field.type = 'checkbox';
      field.checked = Boolean(param.default);
    } else if (param.type === 'int' || param.type === 'float') {
      field = document.createElement('input');
      field.type = 'number';
      if (param.type === 'float') field.step = '0.1';
      field.value = param.default ?? 0;
    } else {
      field = document.createElement('input');
      field.type = param.type === 'password' ? 'password' : 'text';
      field.value = param.default ?? '';
      field.spellcheck = false;
      if (param.type === 'file') {
        field.placeholder = '例如 ./dict.txt';
      }
    }
    field.dataset.param = param.key;
    wrap.appendChild(field);

    if (param.help) {
      const help = document.createElement('div');
      help.className = 'help';
      help.textContent = param.help;
      wrap.appendChild(help);
    }
    form.appendChild(wrap);
  });
}

function collectParams() {
  const params = {};
  if (!state.current) return params;
  state.current.params.forEach((param) => {
    const node = document.querySelector(`[data-param="${param.key}"]`);
    if (!node) return;
    if (param.type === 'bool') {
      params[param.key] = node.checked;
    } else if (param.type === 'int') {
      params[param.key] = parseInt(node.value, 10) || 0;
    } else if (param.type === 'float') {
      params[param.key] = parseFloat(node.value) || 0;
    } else {
      params[param.key] = node.value;
    }
  });
  return params;
}

/* ---------------- 任务 ---------------- */

async function run() {
  if (state.running || !state.current) return;
  const targets = $('targets').value.trim();
  if (!targets) {
    toast('先填目标范围', true);
    return;
  }
  const body = {
    plugin: state.current.name,
    targets,
    params: collectParams(),
    concurrency: parseInt($('concurrency').value, 10) || 32,
    delay: parseFloat($('delay').value) || 0,
    retries: parseInt($('retries').value, 10) || 0,
    stop_on_hit: $('stop-on-hit').checked,
  };

  resetView();
  try {
    const result = await api('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    state.jobId = result.id;
    state.total = result.targets;
    $('stat-total').textContent = result.targets;
    addLog('info', `任务 ${result.id} 已创建，${result.targets} 个目标，并发 ${body.concurrency}`);
    setRunning(true);
    connectWs(result.id);
  } catch (error) {
    toast(error.message, true);
  }
}

function connectWs(jobId) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${scheme}://${location.host}/api/jobs/${jobId}/ws`);
  state.ws = ws;
  ws.addEventListener('message', (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    handleEvent(event);
  });
  ws.addEventListener('close', () => {
    if (state.running) {
      setRunning(false);
      addLog('warn', '事件流已断开');
    }
  });
}

function handleEvent(event) {
  switch (event.type) {
    case 'job_start':
      state.total = event.targets;
      $('stat-total').textContent = event.targets;
      break;
    case 'target_start':
      state.started += 1;
      updateStats();
      break;
    case 'target_done':
      state.finished += 1;
      updateStats();
      updateProgress();
      break;
    case 'log':
      addLog(event.level || 'info', event.message, event.target);
      break;
    case 'target_error':
      state.errors += 1;
      updateStats();
      addLog('error', event.message, event.target);
      break;
    case 'finding':
      onFinding(event.finding);
      break;
    case 'job_end':
      finishJob(event);
      break;
    default:
      break;
  }
}

function onFinding(finding) {
  state.findings.push(finding);
  renderFindings();
  if (finding.flag) {
    state.flags += 1;
    updateStats();
    addLog('ok', `命中 ${finding.flag}`, finding.target);
  } else {
    addLog('ok', `登录成功${finding.extra && finding.extra.user ? ' ' + finding.extra.user : ''}`, finding.target);
  }
}

function finishJob(event) {
  setRunning(false);
  const summary = event.summary || {};
  if (summary.total) {
    state.total = summary.total;
    state.finished = summary.finished ?? state.finished;
  }
  if (summary.flags) state.flags = summary.flags;
  if (summary.errors) state.errors = summary.errors;
  updateStats();
  if (summary.stopped) {
    updateProgress(false);
  } else {
    updateProgress(true);
  }
  $('stat-elapsed').textContent = `${summary.elapsed ?? 0}s`;
  const level = event.status === 'error' ? 'error' : summary.stopped ? 'warn' : 'ok';
  addLog(
    level,
    event.status === 'error'
      ? `任务异常结束: ${event.error}`
      : `任务结束：${summary.finished ?? 0}/${summary.total ?? 0} 完成，${summary.flags ?? 0} 个 flag，耗时 ${summary.elapsed ?? 0}s${summary.stopped ? '（已停止）' : ''}`
  );
  refreshFlags();
}

function setRunning(value) {
  state.running = value;
  $('run-btn').disabled = value;
  $('stop-btn').disabled = !value;
  $('run-btn').textContent = value ? '打点中…' : '开始打点';
  const dot = $('conn-dot');
  dot.className = 'dot' + (value ? ' busy' : '');
  $('conn-text').textContent = value ? '运行中' : '就绪';
  if (value) {
    state.startedAt = Date.now();
    clearInterval(state.timer);
    state.timer = setInterval(() => {
      $('stat-elapsed').textContent = `${((Date.now() - state.startedAt) / 1000).toFixed(0)}s`;
    }, 500);
  } else {
    clearInterval(state.timer);
  }
}

function resetView() {
  state.started = 0;
  state.finished = 0;
  state.total = 0;
  state.flags = 0;
  state.errors = 0;
  state.findings = [];
  $('stat-total').textContent = '0';
  $('stat-started').textContent = '0';
  $('stat-flags').textContent = '0';
  $('stat-errors').textContent = '0';
  $('stat-elapsed').textContent = '0s';
  $('progress-bar').style.width = '0%';
  $('progress-wrap').classList.remove('visible');
  $('log').innerHTML = '';
  renderFindings();
}

function updateStats() {
  $('stat-total').textContent = state.total;
  $('stat-started').textContent = state.started;
  $('stat-flags').textContent = state.flags;
  $('stat-errors').textContent = state.errors;
}

function updateProgress(done = false) {
  const wrap = $('progress-wrap');
  wrap.classList.add('visible');
  const percent = done ? 100 : state.total ? Math.min(99, (state.finished / state.total) * 100) : 0;
  $('progress-bar').style.width = `${percent}%`;
}

/* ---------------- 日志与结果 ---------------- */

function addLog(level, message, target) {
  const log = $('log');
  const line = document.createElement('div');
  line.className = `log-line ${level}`;
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  line.innerHTML = `
    <span class="ts">${now}</span>
    ${target ? `<span class="target">${escapeHtml(target)}</span>` : ''}
    <span class="msg">${escapeHtml(message)}</span>`;
  log.appendChild(line);
  if (log.children.length > 2000) log.removeChild(log.firstElementChild);
  log.scrollTop = log.scrollHeight;
}

function renderFindings() {
  const body = $('findings-body');
  $('findings-count').textContent = state.findings.length;
  if (!state.findings.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="2">还没有命中</td></tr>';
    return;
  }
  body.innerHTML = state.findings.slice().reverse().map((finding) => {
    let result;
    if (finding.flag) {
      result = `<span class="flag-cell" data-copy="${escapeHtml(finding.flag)}">${escapeHtml(finding.flag)}</span>`;
    } else {
      const user = finding.extra && finding.extra.user ? escapeHtml(finding.extra.user) : '';
      result = `<span class="badge-ok">登录成功</span> <span class="muted small">${user}</span>`;
    }
    return `<tr><td class="mono">${escapeHtml(finding.target)}</td><td>${result}</td></tr>`;
  }).join('');
  body.querySelectorAll('[data-copy]').forEach((node) => {
    node.addEventListener('click', () => copyText(node.dataset.copy));
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制');
  } catch {
    toast('复制失败，手动选中吧', true);
  }
}

/* ---------------- flag 池 ---------------- */

async function refreshFlags() {
  try {
    const data = await api('/api/flags');
    renderFlags(data.flags || []);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderFlags(flags) {
  const body = $('flags-body');
  if (!flags.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="4">flag 池是空的</td></tr>';
    return;
  }
  body.innerHTML = flags.map((item) => {
    const time = new Date(item.created_at * 1000).toLocaleTimeString('zh-CN', { hour12: false });
    return `<tr>
      <td class="mono muted">${time}</td>
      <td class="mono">${escapeHtml(item.target)}</td>
      <td class="mono muted">${escapeHtml(item.plugin)}</td>
      <td><span class="flag-cell" data-copy="${escapeHtml(item.flag)}">${escapeHtml(item.flag)}</span></td>
    </tr>`;
  }).join('');
  body.querySelectorAll('[data-copy]').forEach((node) => {
    node.addEventListener('click', () => copyText(node.dataset.copy));
  });
}

/* ---------------- 事件绑定 ---------------- */

async function stopJob() {
  if (!state.running || !state.jobId) return;
  try {
    await api(`/api/jobs/${state.jobId}/stop`, { method: 'POST' });
    addLog('warn', '已发送停止请求，等当前目标收尾');
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('run-btn').addEventListener('click', run);
  $('stop-btn').addEventListener('click', stopJob);
  $('export-flags').addEventListener('click', () => {
    window.location.href = '/api/flags/export.csv';
  });
  $('reset-params').addEventListener('click', () => state.current && renderForm(state.current));
  $('clear-log').addEventListener('click', () => { $('log').innerHTML = ''; });
  $('refresh-flags').addEventListener('click', refreshFlags);
  $('clear-flags').addEventListener('click', async () => {
    try {
      await api('/api/flags', { method: 'DELETE' });
      refreshFlags();
      toast('flag 池已清空');
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') run();
  });

  loadPlugins();
  refreshFlags();
});
