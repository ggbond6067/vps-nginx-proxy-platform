const state = {
  init: null,
  monitorTimer: null,
};

const dom = {};

function qs(id) {
  return document.getElementById(id);
}

function bindDom() {
  [
    'toast-area','summary-cards','public-ip-cards','env-table-body','route-form','route-original-domain','zone-selector-group','route-zone-id',
    'zone-mode-hint','full-domain-group','route-full-domain','prefix-group','route-dns-prefix','route-domain-preview','route-service-select',
    'route-service-name','route-service-port','route-dns-record-type','route-dns-value-group','route-dns-value','route-remark','route-bind-dns',
    'route-enable-https','route-reset','routes-table-body','routes-count','refresh-all','reload-nginx','deploy-form','deploy-project-slug',
    'deploy-service-name','deploy-image','deploy-container-name','deploy-port','deploy-restart','deploy-command','deploy-env','deploy-volumes',
    'compose-generate','compose-validate','deploy-project-list','compose-editor','compose-save','compose-deploy','compose-load',
    'monitor-summary-cards','engine-meta','monitor-table-body'
  ].forEach((id) => { dom[id] = qs(id); });
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function showToast(message, type = 'info') {
  const item = document.createElement('div');
  item.className = `toast ${type}`;
  item.textContent = message;
  dom['toast-area'].prepend(item);
  window.setTimeout(() => item.remove(), 5000);
}

async function apiFetch(url, options = {}) {
  const finalOptions = { ...options, headers: { ...(options.headers || {}) } };
  if (finalOptions.body && !finalOptions.headers['Content-Type']) {
    finalOptions.headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(url, finalOptions);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = { ok: response.ok, message: `请求失败: ${response.status}` };
  }
  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || `请求失败: ${response.status}`);
  }
  return payload.data || {};
}

function badge(ok, good = '正常', bad = '异常') {
  return `<span class="badge ${ok ? 'ok' : 'bad'}">${ok ? good : bad}</span>`;
}

function warnBadge(label) {
  return `<span class="badge warn">${escapeHtml(label)}</span>`;
}

function formatBytes(value) {
  const num = Number(value || 0);
  if (!num) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let current = num;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(current >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDuration(seconds) {
  if (seconds == null) return '-';
  const total = Number(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function selectedZone() {
  const option = dom['route-zone-id']?.selectedOptions?.[0];
  if (!option) return { id: '', name: '' };
  return { id: option.value || '', name: option.dataset.name || '' };
}

function updateRoutePreview() {
  const zones = state.init?.zones;
  if (!zones) return;
  if (zones.mode === 'manual') {
    const domain = (dom['route-full-domain'].value || '').trim();
    dom['route-domain-preview'].textContent = domain || '-';
    return;
  }
  const { name } = selectedZone();
  const prefix = (dom['route-dns-prefix'].value || '').trim().toLowerCase();
  if (!name) {
    dom['route-domain-preview'].textContent = '-';
    return;
  }
  if (!prefix || prefix === '@') {
    dom['route-domain-preview'].textContent = name;
    return;
  }
  dom['route-domain-preview'].textContent = `${prefix}.${name}`;
}

function syncDnsValueVisibility() {
  const isCname = dom['route-dns-record-type'].value === 'CNAME';
  dom['route-dns-value-group'].classList.toggle('hidden', !isCname);
}

function resetRouteForm(keepService = false) {
  dom['route-original-domain'].value = '';
  if (!keepService) {
    dom['route-service-name'].value = '';
    dom['route-service-port'].value = '8000';
    dom['route-remark'].value = '';
  }
  dom['route-full-domain'].value = '';
  dom['route-dns-prefix'].value = '';
  dom['route-dns-record-type'].value = 'A';
  dom['route-dns-value'].value = '';
  dom['route-bind-dns'].checked = false;
  dom['route-enable-https'].checked = true;
  dom['route-service-select'].value = '';
  syncDnsValueVisibility();
  updateRoutePreview();
}

function renderSummaryCards(overview) {
  const summary = overview.summary;
  const cards = [
    ['路由数', summary.route_count],
    ['证书数', summary.cert_count],
    ['HTTPS 生效', summary.https_active_count],
    ['可用主域', summary.zone_available_count],
    ['运行中容器', summary.running_container_count],
    ['Docker 可用', summary.docker_available ? '是' : '否'],
  ];
  dom['summary-cards'].innerHTML = cards.map(([label, value]) => `
    <article class="summary-card">
      <h4>${escapeHtml(label)}</h4>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `).join('');
}

function renderPublicIps(publicIps) {
  dom['public-ip-cards'].innerHTML = ['ipv4', 'ipv6'].map((family) => {
    const item = publicIps[family];
    return `
      <article class="ip-card">
        <h4>${family.toUpperCase()}</h4>
        <strong>${escapeHtml(item.value || '-')}</strong>
        <div class="metric-line"><span>来源</span><span>${escapeHtml(item.source_label)}</span></div>
      </article>
    `;
  }).join('');
}

function renderEnvItems(items) {
  dom['env-table-body'].innerHTML = items.map((item) => `
    <tr>
      <td>${escapeHtml(item.name)}</td>
      <td>${badge(item.ok, '通过', '失败')}</td>
      <td><code>${escapeHtml(item.detail)}</code></td>
    </tr>
  `).join('');
}

function renderZones(zones) {
  const selectorMode = zones.mode === 'selector';
  dom['zone-selector-group'].classList.toggle('hidden', !selectorMode);
  dom['prefix-group'].classList.toggle('hidden', !selectorMode);
  dom['full-domain-group'].classList.toggle('hidden', selectorMode);
  dom['zone-mode-hint'].textContent = zones.message || (selectorMode ? '从 Cloudflare 获取主域列表，可直接切换。' : '当前为完整域名输入模式。');

  if (selectorMode) {
    dom['route-zone-id'].innerHTML = zones.items.map((item) => `
      <option value="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>
    `).join('');
    if (zones.default_zone_id) {
      dom['route-zone-id'].value = zones.default_zone_id;
    }
  } else {
    dom['route-zone-id'].innerHTML = '';
  }
  updateRoutePreview();
}

function renderServices(services) {
  const options = ['<option value="">手动输入服务名</option>'];
  services.forEach((item) => {
    options.push(`<option value="${escapeHtml(item.service_name)}" data-port="${escapeHtml(item.ports.length === 1 ? item.ports[0] : '')}">${escapeHtml(item.service_name)} · ${escapeHtml(item.container_name)} · ${escapeHtml((item.ports || []).join(', ') || '无端口')}</option>`);
  });
  dom['route-service-select'].innerHTML = options.join('');
}

function renderRoutes(routes) {
  state.init.routes = routes;
  dom['routes-count'].textContent = `${routes.length} 条记录`;
  if (!routes.length) {
    dom['routes-table-body'].innerHTML = '<tr><td colspan="6">暂无路由，请先创建。</td></tr>';
    return;
  }
  dom['routes-table-body'].innerHTML = routes.map((route) => `
    <tr>
      <td>
        <div><code>${escapeHtml(route.domain)}</code></div>
        <div class="hint">${escapeHtml(route.remark || '-')}</div>
      </td>
      <td><code>${escapeHtml(route.service_name)}:${escapeHtml(route.service_port)}</code></td>
      <td>
        <div>${escapeHtml(route.dns_record_type)} → ${escapeHtml(route.dns_target_display || '-')}</div>
        <div class="hint">${escapeHtml(route.zone_name || '手动域名')}</div>
      </td>
      <td>${route.enable_https ? badge(true, '开启', '关闭') : warnBadge('关闭')}</td>
      <td>${route.https_active ? badge(true, '已生效', '未生效') : (route.cert_exists ? warnBadge('待启用') : badge(false, '已生效', '未申请'))}</td>
      <td>
        <div class="inline-actions">
          <button type="button" class="ghost route-action" data-action="edit" data-domain="${escapeHtml(route.domain)}">编辑</button>
          <button type="button" class="secondary route-action" data-action="https" data-domain="${escapeHtml(route.domain)}">${route.enable_https ? '关闭 HTTPS' : '开启 HTTPS'}</button>
          <button type="button" class="secondary route-action" data-action="dns" data-domain="${escapeHtml(route.domain)}">绑定 DNS</button>
          <button type="button" class="secondary route-action" data-action="cert" data-domain="${escapeHtml(route.domain)}">申请证书</button>
          <button type="button" class="danger route-action" data-action="delete" data-domain="${escapeHtml(route.domain)}">删除</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function renderProjects(projects) {
  const options = ['<option value="">选择已有项目</option>'];
  projects.forEach((project) => {
    options.push(`<option value="${escapeHtml(project.project_slug)}">${escapeHtml(project.project_slug)}</option>`);
  });
  dom['deploy-project-list'].innerHTML = options.join('');
}

function renderOverview(overview) {
  renderSummaryCards(overview);
  renderPublicIps(overview.public_ips);
  renderEnvItems(overview.env_items);
}

function renderMonitor(monitor) {
  const cards = [
    ['容器总数', monitor.summary.total],
    ['运行中', monitor.summary.running],
    ['异常健康', monitor.summary.unhealthy],
    ['Docker', monitor.available ? '在线' : '离线'],
  ];
  dom['monitor-summary-cards'].innerHTML = cards.map(([label, value]) => `
    <article class="summary-card">
      <h4>${escapeHtml(label)}</h4>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `).join('');

  const engine = monitor.engine || {};
  dom['engine-meta'].innerHTML = [
    ['名称', engine.name || '-'],
    ['版本', engine.server_version || '-'],
    ['系统', engine.operating_system || '-'],
    ['内核', engine.kernel_version || '-'],
    ['CPU', engine.cpu_count || '-'],
    ['总内存', formatBytes(engine.memory_total || 0)],
  ].map(([label, value]) => `
    <div class="meta-item">
      <h5>${escapeHtml(label)}</h5>
      <div>${escapeHtml(value)}</div>
    </div>
  `).join('');

  if (!monitor.containers?.length) {
    dom['monitor-table-body'].innerHTML = `<tr><td colspan="6">${escapeHtml(monitor.message || '暂无监控数据')}</td></tr>`;
    return;
  }

  dom['monitor-table-body'].innerHTML = monitor.containers.map((item) => `
    <tr>
      <td>
        <div><strong>${escapeHtml(item.name)}</strong></div>
        <div class="hint">${escapeHtml(item.image)}</div>
        <div class="hint">运行 ${escapeHtml(formatDuration(item.uptime_seconds))}</div>
      </td>
      <td>
        ${badge(item.status === 'running', item.status || 'running', item.status || 'stopped')}
        ${item.health === 'unhealthy' ? warnBadge('health: unhealthy') : item.health ? warnBadge(`health: ${item.health}`) : ''}
      </td>
      <td>${escapeHtml(item.cpu_percent.toFixed ? item.cpu_percent.toFixed(2) : item.cpu_percent)}%</td>
      <td>${escapeHtml(formatBytes(item.memory_usage))} / ${escapeHtml(formatBytes(item.memory_limit))}</td>
      <td>RX ${escapeHtml(formatBytes(item.rx_bytes))}<br/>TX ${escapeHtml(formatBytes(item.tx_bytes))}</td>
      <td>${escapeHtml((item.ports || []).join(', ') || '-')}<br/><span class="hint">${escapeHtml((item.networks || []).join(', ') || '-')}</span></td>
    </tr>
  `).join('');
}

function currentRoutePayload() {
  const zones = state.init?.zones;
  const selector = selectedZone();
  return {
    original_domain: dom['route-original-domain'].value || '',
    zone_id: zones?.mode === 'selector' ? selector.id : '',
    zone_name: zones?.mode === 'selector' ? selector.name : '',
    full_domain: zones?.mode === 'manual' ? dom['route-full-domain'].value.trim() : '',
    dns_prefix: dom['route-dns-prefix'].value.trim(),
    service_name: dom['route-service-name'].value.trim(),
    service_port: dom['route-service-port'].value.trim(),
    remark: dom['route-remark'].value.trim(),
    dns_record_type: dom['route-dns-record-type'].value,
    dns_value: dom['route-dns-value'].value.trim(),
    bind_dns: dom['route-bind-dns'].checked,
    enable_https: dom['route-enable-https'].checked,
  };
}

function hydrateRouteForm(route) {
  const zones = state.init?.zones;
  dom['route-original-domain'].value = route.domain || '';
  if (zones?.mode === 'selector') {
    if (route.zone_id) {
      dom['route-zone-id'].value = route.zone_id;
    }
    dom['route-dns-prefix'].value = route.dns_prefix === '@' ? '' : (route.dns_prefix || '');
    dom['route-full-domain'].value = '';
  } else {
    dom['route-full-domain'].value = route.domain || '';
  }
  dom['route-service-name'].value = route.service_name || '';
  dom['route-service-port'].value = route.service_port || '8000';
  dom['route-dns-record-type'].value = route.dns_record_type || 'A';
  dom['route-dns-value'].value = route.dns_value || '';
  dom['route-remark'].value = route.remark || '';
  dom['route-enable-https'].checked = !!route.enable_https;
  dom['route-bind-dns'].checked = false;
  syncDnsValueVisibility();
  updateRoutePreview();
  window.scrollTo({ top: dom['route-form'].getBoundingClientRect().top + window.scrollY - 20, behavior: 'smooth' });
}

function buildCompose() {
  const projectSlug = dom['deploy-project-slug'].value.trim();
  const serviceName = dom['deploy-service-name'].value.trim();
  const image = dom['deploy-image'].value.trim();
  const containerName = dom['deploy-container-name'].value.trim() || serviceName;
  const internalPort = dom['deploy-port'].value.trim() || '8000';
  const restartPolicy = dom['deploy-restart'].value;
  const command = dom['deploy-command'].value.trim();
  const envLines = dom['deploy-env'].value.split(/?
/).map((item) => item.trim()).filter(Boolean);
  const volumeLines = dom['deploy-volumes'].value.split(/?
/).map((item) => item.trim()).filter(Boolean);
  const networkName = state.init?.defaults?.proxy_network_name || 'proxy_net';

  const lines = [
    'services:',
    `  ${serviceName}:`,
    `    image: ${image}`,
    `    container_name: ${containerName}`,
    `    restart: ${restartPolicy}`,
    '    expose:',
    `      - "${internalPort}"`,
  ];
  if (envLines.length) {
    lines.push('    environment:');
    envLines.forEach((line) => lines.push(`      - ${line}`));
  }
  if (volumeLines.length) {
    lines.push('    volumes:');
    volumeLines.forEach((line) => lines.push(`      - ${line}`));
  }
  if (command) {
    lines.push(`    command: ${JSON.stringify(command)}`);
  }
  lines.push('    networks:');
  lines.push(`      - ${networkName}`);
  lines.push('');
  lines.push('networks:');
  lines.push(`  ${networkName}:`);
  lines.push('    external: true');
  lines.push(`    name: ${networkName}`);

  dom['compose-editor'].value = lines.join('
') + '
';
  if (!projectSlug) {
    dom['deploy-project-slug'].focus();
  }
}

async function loadProject(slug) {
  if (!slug) return;
  const data = await apiFetch(`/api/projects/${encodeURIComponent(slug)}`);
  const project = data.project;
  dom['compose-editor'].value = project.compose_content || '';
  const meta = project.meta || {};
  dom['deploy-project-slug'].value = project.project_slug || '';
  dom['deploy-service-name'].value = meta.primary_service_name || '';
  dom['deploy-image'].value = meta.image || '';
  dom['deploy-container-name'].value = meta.container_name || '';
  dom['deploy-port'].value = meta.internal_port || '8000';
}

async function refreshInit(showInfo = false) {
  const data = await apiFetch('/api/init');
  state.init = data;
  renderOverview(data.overview);
  renderZones(data.zones);
  renderServices(data.services || []);
  renderRoutes(data.routes || []);
  renderProjects(data.projects || []);
  if (showInfo) showToast('数据已刷新', 'info');
}

async function refreshMonitor() {
  const data = await apiFetch('/api/monitor');
  renderMonitor(data.monitor);
}

async function handleRouteSubmit(event) {
  event.preventDefault();
  try {
    const data = await apiFetch('/api/routes', { method: 'POST', body: JSON.stringify(currentRoutePayload()) });
    showToast(data.message || '路由已保存', 'success');
    if (data.nginx?.message) showToast(data.nginx.message, data.nginx.ok ? 'success' : 'error');
    if (data.dns?.message) showToast(data.dns.message, data.dns.ok ? 'success' : 'error');
    await refreshInit();
    resetRouteForm();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function handleRouteAction(event) {
  const button = event.target.closest('.route-action');
  if (!button) return;
  const action = button.dataset.action;
  const domain = button.dataset.domain;
  const route = (state.init?.routes || []).find((item) => item.domain === domain);
  if (!route) return;

  try {
    if (action === 'edit') {
      hydrateRouteForm(route);
      return;
    }
    if (action === 'https') {
      const data = await apiFetch(`/api/routes/${encodeURIComponent(domain)}/https`, { method: 'POST', body: JSON.stringify({ enable_https: !route.enable_https }) });
      showToast(data.message || 'HTTPS 状态已更新', 'success');
      if (data.nginx?.message) showToast(data.nginx.message, data.nginx.ok ? 'success' : 'error');
    }
    if (action === 'dns') {
      const data = await apiFetch(`/api/routes/${encodeURIComponent(domain)}/dns`, { method: 'POST', body: JSON.stringify({}) });
      showToast(data.message || 'DNS 已绑定', 'success');
    }
    if (action === 'cert') {
      const data = await apiFetch(`/api/routes/${encodeURIComponent(domain)}/cert`, { method: 'POST', body: JSON.stringify({}) });
      showToast(data.message || '证书申请完成', 'success');
      if (data.nginx?.message) showToast(data.nginx.message, data.nginx.ok ? 'success' : 'error');
    }
    if (action === 'delete') {
      if (!window.confirm(`确认删除路由 ${domain}？`)) return;
      const data = await apiFetch(`/api/routes/${encodeURIComponent(domain)}`, { method: 'DELETE' });
      showToast(data.message || '路由已删除', 'success');
      if (data.nginx?.message) showToast(data.nginx.message, data.nginx.ok ? 'success' : 'error');
    }
    await refreshInit();
    await refreshMonitor();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function handleComposeValidate() {
  try {
    const data = await apiFetch('/api/projects/validate', {
      method: 'POST',
      body: JSON.stringify({
        project_slug: dom['deploy-project-slug'].value.trim(),
        compose_content: dom['compose-editor'].value,
      }),
    });
    showToast(data.message || 'Compose 校验通过', 'success');
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function handleComposeSave() {
  try {
    const data = await apiFetch('/api/projects/save', {
      method: 'POST',
      body: JSON.stringify({
        project_slug: dom['deploy-project-slug'].value.trim(),
        primary_service_name: dom['deploy-service-name'].value.trim(),
        internal_port: dom['deploy-port'].value.trim(),
        image: dom['deploy-image'].value.trim(),
        container_name: dom['deploy-container-name'].value.trim(),
        compose_content: dom['compose-editor'].value,
      }),
    });
    showToast(data.message || 'Compose 已保存', 'success');
    await refreshInit();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function handleComposeDeploy() {
  try {
    const data = await apiFetch('/api/projects/deploy', {
      method: 'POST',
      body: JSON.stringify({
        project_slug: dom['deploy-project-slug'].value.trim(),
        primary_service_name: dom['deploy-service-name'].value.trim(),
        internal_port: dom['deploy-port'].value.trim(),
        image: dom['deploy-image'].value.trim(),
        container_name: dom['deploy-container-name'].value.trim(),
        compose_content: dom['compose-editor'].value,
      }),
    });
    showToast(data.message || '项目部署成功', 'success');
    if (data.deploy?.message) showToast(data.deploy.message, 'info');
    if (data.route_draft) {
      dom['route-service-name'].value = data.route_draft.service_name || '';
      dom['route-service-port'].value = data.route_draft.service_port || '8000';
      dom['route-remark'].value = data.route_draft.remark || '';
      showToast('已将部署结果预填到路由表单', 'info');
    }
    await refreshInit();
    await refreshMonitor();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

function bindEvents() {
  dom['route-zone-id']?.addEventListener('change', updateRoutePreview);
  dom['route-dns-prefix']?.addEventListener('input', updateRoutePreview);
  dom['route-full-domain']?.addEventListener('input', updateRoutePreview);
  dom['route-dns-record-type']?.addEventListener('change', syncDnsValueVisibility);
  dom['route-form']?.addEventListener('submit', handleRouteSubmit);
  dom['route-reset']?.addEventListener('click', () => resetRouteForm());
  dom['routes-table-body']?.addEventListener('click', handleRouteAction);
  dom['refresh-all']?.addEventListener('click', async () => {
    try {
      await refreshInit(true);
      await refreshMonitor();
    } catch (error) {
      showToast(error.message, 'error');
    }
  });
  dom['reload-nginx']?.addEventListener('click', async () => {
    try {
      const data = await apiFetch('/api/nginx/reload', { method: 'POST', body: JSON.stringify({}) });
      showToast(data.nginx?.message || data.message || 'Nginx 已重载', 'success');
    } catch (error) {
      showToast(error.message, 'error');
    }
  });
  dom['route-service-select']?.addEventListener('change', () => {
    const option = dom['route-service-select'].selectedOptions[0];
    if (!option || !option.value) return;
    dom['route-service-name'].value = option.value;
    if (option.dataset.port) dom['route-service-port'].value = option.dataset.port;
  });
  dom['compose-generate']?.addEventListener('click', buildCompose);
  dom['compose-validate']?.addEventListener('click', handleComposeValidate);
  dom['compose-save']?.addEventListener('click', handleComposeSave);
  dom['compose-deploy']?.addEventListener('click', handleComposeDeploy);
  dom['compose-load']?.addEventListener('click', async () => {
    try {
      await loadProject(dom['deploy-project-list'].value);
    } catch (error) {
      showToast(error.message, 'error');
    }
  });
}

async function boot() {
  bindDom();
  bindEvents();
  syncDnsValueVisibility();
  resetRouteForm();
  try {
    await refreshInit();
    await refreshMonitor();
    state.monitorTimer = window.setInterval(() => {
      refreshMonitor().catch((error) => showToast(error.message, 'error'));
    }, 15000);
  } catch (error) {
    showToast(error.message, 'error');
  }
}

document.addEventListener('DOMContentLoaded', boot);
