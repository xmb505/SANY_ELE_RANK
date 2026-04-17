// 全局变量
let CONFIG = {};
let currentRatio = 10;
let currentNightDate = '';
let currentBuilding = '全部';
let currentChart = null;
let excludedBuildings = [];
let defaultExclude = [];
let allBuildings = [];

// 获取API地址
function getApiUrl() {
    return CONFIG && CONFIG.API_BASE_URL ? CONFIG.API_BASE_URL : 'http://localhost:8081';
}

// 带超时的fetch
async function fetchWithTimeout(url, options = {}) {
    const timeout = (CONFIG && CONFIG.API_TIMEOUT) ? CONFIG.API_TIMEOUT : 5000;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        throw error;
    }
}

// 统一错误处理
function handleError(error, context = '') {
    console.error(`${context}时出错:`, error);
    alert(`${context}失败，请检查网络连接或后端服务是否运行。`);
}

// 分数颜色
function getScoreColor(score) {
    if (score >= 60) return 'score-danger';
    if (score >= 30) return 'score-warning';
    return 'score-normal';
}

// 动态加载配置
function loadConfig() {
    return new Promise((resolve) => {
        if (window.DYNAMIC_CONFIG) {
            CONFIG = window.DYNAMIC_CONFIG;
            resolve();
        } else {
            resolve();
        }
    });
}

// 获取默认 night_date（凌晨侧日期）
function getDefaultNightDate() {
    const now = new Date();
    const hour = now.getHours();
    // 如果当前时间在 06:00 之后，说明今晚的数据要到明天才有
    // 默认选今天（即昨晚23:00~今天06:00的数据）
    if (hour < 6) {
        // 凌晨0~6点，当前就属于"今天"的night_date
        return formatDate(now);
    } else {
        // 白天/晚上，最近一个完整夜晚是今天凌晨
        return formatDate(now);
    }
}

function formatDate(d) {
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function formatNightDateRange(nightDate) {
    const d = new Date(nightDate);
    const prev = new Date(d);
    prev.setDate(prev.getDate() - 1);
    const pm = String(prev.getMonth() + 1).padStart(2, '0');
    const pd = String(prev.getDate()).padStart(2, '0');
    const cm = String(d.getMonth() + 1).padStart(2, '0');
    const cd = String(d.getDate()).padStart(2, '0');
    return `${pm}-${pd} 23:00 ~ ${cm}-${cd} 06:00`;
}

// ========== 视图切换 ==========

function showView(viewName) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));

    const view = document.getElementById(viewName + '-view');
    if (view) view.classList.add('active');

    const tab = document.querySelector(`.tab[data-view="${viewName}"]`);
    if (tab) tab.classList.add('active');
}

// ========== 楼栋排除 ==========

function getExcludeParam() {
    if (currentBuilding !== '全部' || excludedBuildings.length === 0) return '';
    return '&exclude=' + excludedBuildings.map(encodeURIComponent).join(',');
}

function renderExcludeTags() {
    const container = document.getElementById('exclude-tags');
    container.innerHTML = '';
    allBuildings.forEach(b => {
        const tag = document.createElement('span');
        tag.className = 'exclude-tag' + (excludedBuildings.includes(b) ? ' excluded' : '');
        tag.textContent = b;
        if (defaultExclude.includes(b)) {
            tag.title = '默认排除';
        }
        tag.addEventListener('click', () => {
            const idx = excludedBuildings.indexOf(b);
            if (idx >= 0) {
                excludedBuildings.splice(idx, 1);
            } else {
                excludedBuildings.push(b);
            }
            renderExcludeTags();
            if (currentNightDate) {
                const activeTab = document.querySelector('.tab.active');
                const activeView = activeTab ? activeTab.dataset.view : 'rank';
                if (activeView === 'rank') {
                    fetchRankData(currentNightDate, currentBuilding, currentRatio);
                } else if (activeView === 'overview') {
                    fetchOverviewData(currentNightDate);
                }
            }
        });
        container.appendChild(tag);
    });
}

function updateExcludeGroupVisibility() {
    const group = document.getElementById('exclude-group');
    group.style.display = currentBuilding === '全部' ? '' : 'none';
}

// ========== 排名视图 ==========

async function fetchRankData(nightDate, building, ratio) {
    const url = `${getApiUrl()}/?mode=rank&night_date=${nightDate}&building=${encodeURIComponent(building)}&ratio=${ratio}${getExcludeParam()}`;

    document.getElementById('rank-list').innerHTML = '<div class="loading">加载中...</div>';

    try {
        const response = await fetchWithTimeout(url);
        const data = await response.json();

        if (data.code !== 200) {
            document.getElementById('rank-list').innerHTML = `<div class="empty">查询失败: ${data.error || '未知错误'}</div>`;
            return;
        }

        renderRankCards(data.rows);
        renderStats(data.stats, data.showing, data.total, ratio);
    } catch (error) {
        handleError(error, '获取排名数据');
        document.getElementById('rank-list').innerHTML = '<div class="empty">加载失败，请检查后端服务</div>';
    }
}

function renderRankCards(rows) {
    const container = document.getElementById('rank-list');

    if (!rows || rows.length === 0) {
        container.innerHTML = '<div class="empty">暂无数据</div>';
        return;
    }

    // 找最大用电量用于柱状图比例
    let maxUsage = 0;
    rows.forEach(r => {
        for (let i = 1; i <= 7; i++) {
            const v = r[`n${i}_use_ele`] || 0;
            if (v > maxUsage) maxUsage = v;
        }
    });
    if (maxUsage === 0) maxUsage = 1;

    let html = '';
    rows.forEach(r => {
        const scoreClass = getScoreColor(r.ele_score);
        const isTop3 = r.score_rank <= 3 ? 'top3' : '';
        const stableClass = r.stable_data ? 'stable' : 'unstable';
        const stableText = r.stable_data ? '数据质量高' : '质量不行喵';

        // 小柱状图
        let barsHtml = '';
        for (let i = 1; i <= 7; i++) {
            const v = r[`n${i}_use_ele`] || 0;
            const h = Math.max(2, (v / maxUsage) * 28);
            barsHtml += `<div class="usage-bar" style="height:${h}px" title="${v.toFixed(2)} kWh"></div>`;
        }

        html += `
            <div class="rank-card" data-device-id="${r.device_id}" onclick="openDetail('${r.device_id}')">
                <div class="rank-number ${isTop3}">${r.score_rank}</div>
                <div class="rank-info">
                    <div class="rank-name">${r.equipmentName}</div>
                    <div class="rank-site">${r.installationSite}</div>
                </div>
                <div class="rank-usage-bars">${barsHtml}</div>
                <div class="rank-score ${scoreClass}">${r.ele_score.toFixed(1)}</div>
                <div class="rank-stable ${stableClass}">${stableText}</div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function renderStats(stats, showing, total, ratio) {
    if (!stats) return;
    const bar = document.getElementById('rank-stats');
    let text = `显示前${ratio}%（${showing}/${total}台） | 平均: ${stats.avg_score} | 最高: ${stats.max_score} | >=60分: ${stats.count_ge60} | >=30分: ${stats.count_ge30}`;
    if (excludedBuildings.length > 0 && currentBuilding === '全部') {
        text += ` | 已排除: ${excludedBuildings.join(',')}`;
    }
    bar.innerHTML = text;
}

// ========== 详情视图 ==========

async function openDetail(deviceId) {
    showView('detail');

    const days = (CONFIG && CONFIG.DEFAULT_DAYS) ? CONFIG.DEFAULT_DAYS : 7;
    let url = `${getApiUrl()}/?mode=detail&device_id=${deviceId}&days=${days}`;
    if (currentNightDate) {
        url += `&night_date=${currentNightDate}`;
    }

    document.getElementById('device-info').innerHTML = '<p>加载中...</p>';
    document.getElementById('history-tbody').innerHTML = '';

    try {
        const response = await fetchWithTimeout(url);
        const data = await response.json();

        if (data.code !== 200) {
            document.getElementById('device-info').innerHTML = `<p>查询失败: ${data.error || '未知错误'}</p>`;
            return;
        }

        renderDetailView(data);
    } catch (error) {
        handleError(error, '获取设备详情');
        document.getElementById('device-info').innerHTML = '<p>加载失败</p>';
    }
}

function renderDetailView(data) {
    // 设备信息
    const info = document.getElementById('device-info');
    info.innerHTML = `
        <h2>${data.equipmentName}</h2>
        <p>${data.installationSite} | 设备ID: ${data.device_id}</p>
    `;

    // 图表
    renderUsageChart(data.records);

    // 历史表格
    const tbody = document.getElementById('history-tbody');
    let html = '';
    data.records.forEach(r => {
        const scoreClass = getScoreColor(r.ele_score);
        html += `
            <tr>
                <td>${r.date_range}</td>
                <td>${r.n1_use_ele.toFixed(2)}</td>
                <td>${r.n2_use_ele.toFixed(2)}</td>
                <td>${r.n3_use_ele.toFixed(2)}</td>
                <td>${r.n4_use_ele.toFixed(2)}</td>
                <td>${r.n5_use_ele.toFixed(2)}</td>
                <td>${r.n6_use_ele.toFixed(2)}</td>
                <td>${r.n7_use_ele.toFixed(2)}</td>
                <td class="${scoreClass}">${r.ele_score.toFixed(1)}</td>
                <td>${r.score_rank || '-'}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

function renderUsageChart(records) {
    if (currentChart) {
        currentChart.destroy();
        currentChart = null;
    }

    if (!records || records.length === 0) return;

    const ctx = document.getElementById('usage-chart').getContext('2d');
    const labels = ['23-00', '00-01', '01-02', '02-03', '03-04', '04-05', '05-06'];
    const colors = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f', '#722ed1', '#13c2c2', '#eb2f96'];

    const datasets = records.map((r, idx) => ({
        label: r.date_range,
        data: [r.n1_use_ele, r.n2_use_ele, r.n3_use_ele, r.n4_use_ele, r.n5_use_ele, r.n6_use_ele, r.n7_use_ele],
        backgroundColor: colors[idx % colors.length] + '99',
        borderColor: colors[idx % colors.length],
        borderWidth: 1,
    }));

    currentChart = new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top' },
                title: { display: true, text: '夜间各时段用电量 (kWh)' },
            },
            scales: {
                y: { beginAtZero: true, title: { display: true, text: 'kWh' } },
            },
        },
    });
}

// ========== 概览视图 ==========

async function fetchOverviewData(nightDate) {
    const url = `${getApiUrl()}/?mode=overview&night_date=${nightDate}${getExcludeParam()}`;

    document.getElementById('overview-cards').innerHTML = '<div class="loading">加载中...</div>';
    document.getElementById('building-list').innerHTML = '';

    try {
        const response = await fetchWithTimeout(url);
        const data = await response.json();

        if (data.code !== 200) {
            document.getElementById('overview-cards').innerHTML = `<div class="empty">查询失败: ${data.error || '未知错误'}</div>`;
            return;
        }

        renderOverview(data);
    } catch (error) {
        handleError(error, '获取概览数据');
        document.getElementById('overview-cards').innerHTML = '<div class="empty">加载失败</div>';
    }
}

function renderOverview(data) {
    // 统计卡片
    const cards = document.getElementById('overview-cards');
    cards.innerHTML = `
        <div class="overview-card">
            <div class="card-value">${data.total_devices}</div>
            <div class="card-label">电表设备总数</div>
        </div>
        <div class="overview-card">
            <div class="card-value">${data.valid_count}</div>
            <div class="card-label">有效数据</div>
        </div>
        <div class="overview-card">
            <div class="card-value">${data.skip_count}</div>
            <div class="card-label">跳过设备</div>
        </div>
        <div class="overview-card">
            <div class="card-value">${data.avg_score}</div>
            <div class="card-label">平均分</div>
        </div>
        <div class="overview-card">
            <div class="card-value danger">${data.max_score}</div>
            <div class="card-label">最高分</div>
        </div>
        <div class="overview-card">
            <div class="card-value danger">${data.count_ge60}</div>
            <div class="card-label">>=60分 (异常)</div>
        </div>
        <div class="overview-card">
            <div class="card-value">${data.count_ge30}</div>
            <div class="card-label">>=30分 (可疑)</div>
        </div>
    `;

    // 楼栋统计
    const list = document.getElementById('building-list');
    if (!data.building_stats || data.building_stats.length === 0) {
        list.innerHTML = '<div class="empty">暂无楼栋数据</div>';
        return;
    }

    const maxAvg = Math.max(...data.building_stats.map(b => b.avg_score));
    const barMaxWidth = maxAvg > 0 ? maxAvg : 1;

    let html = '';
    data.building_stats.forEach(b => {
        const barWidth = (b.avg_score / barMaxWidth) * 100;
        const barColor = b.avg_score >= 30 ? '#ff4d4f' : b.avg_score >= 15 ? '#faad14' : '#52c41a';
        html += `
            <div class="building-row">
                <div class="building-name">${b.building}</div>
                <div class="building-bar-container">
                    <div class="building-bar" style="width:${barWidth}%; background:${barColor}"></div>
                </div>
                <div class="building-stats-text">平均${b.avg_score}分 | ${b.count}台 | ${b.count_ge60}台>=60</div>
            </div>
        `;
    });
    list.innerHTML = html;
}

// ========== 楼栋列表 ==========

async function loadBuildings() {
    try {
        const url = `${getApiUrl()}/?mode=buildings`;
        const response = await fetchWithTimeout(url);
        const data = await response.json();

        if (data.code === 200 && data.buildings) {
            allBuildings = data.buildings;
            defaultExclude = data.default_exclude || [];
            excludedBuildings = [...defaultExclude];

            const select = document.getElementById('building-select');
            select.innerHTML = '<option value="全部">全部</option>';
            data.buildings.forEach(b => {
                select.innerHTML += `<option value="${b}">${b}</option>`;
            });

            renderExcludeTags();
            updateExcludeGroupVisibility();
        }
    } catch (e) {
        console.error('加载楼栋列表失败:', e);
    }
}

// ========== 初始化 ==========

function initApp() {
    // 设置默认日期
    const dateInput = document.getElementById('night-date');
    dateInput.value = getDefaultNightDate();
    currentNightDate = dateInput.value;

    // 查询按钮
    document.getElementById('load-btn').addEventListener('click', () => {
        currentNightDate = dateInput.value;
        if (!currentNightDate) {
            alert('请选择日期');
            return;
        }

        const activeTab = document.querySelector('.tab.active');
        const activeView = activeTab ? activeTab.dataset.view : 'rank';

        if (activeView === 'rank') {
            fetchRankData(currentNightDate, currentBuilding, currentRatio);
        } else if (activeView === 'overview') {
            fetchOverviewData(currentNightDate);
        }
    });

    // Tab 切换
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const viewName = tab.dataset.view;
            showView(viewName);

            if (currentNightDate) {
                if (viewName === 'rank') {
                    fetchRankData(currentNightDate, currentBuilding, currentRatio);
                } else if (viewName === 'overview') {
                    fetchOverviewData(currentNightDate);
                }
            }
        });
    });

    // 楼栋筛选
    document.getElementById('building-select').addEventListener('change', (e) => {
        currentBuilding = e.target.value;
        updateExcludeGroupVisibility();
        if (currentNightDate) {
            fetchRankData(currentNightDate, currentBuilding, currentRatio);
        }
    });

    // 重置排除为默认
    document.getElementById('reset-exclude').addEventListener('click', () => {
        excludedBuildings = [...defaultExclude];
        renderExcludeTags();
        if (currentNightDate) {
            const activeTab = document.querySelector('.tab.active');
            const activeView = activeTab ? activeTab.dataset.view : 'rank';
            if (activeView === 'rank') {
                fetchRankData(currentNightDate, currentBuilding, currentRatio);
            } else if (activeView === 'overview') {
                fetchOverviewData(currentNightDate);
            }
        }
    });

    // 百分比预设按钮
    document.querySelectorAll('.ratio-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.ratio-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('custom-ratio').value = '';
            currentRatio = parseInt(btn.dataset.ratio);
            if (currentNightDate) {
                fetchRankData(currentNightDate, currentBuilding, currentRatio);
            }
        });
    });

    // 自定义百分比
    document.getElementById('set-custom-ratio').addEventListener('click', () => {
        const input = document.getElementById('custom-ratio');
        const val = parseInt(input.value);
        if (isNaN(val) || val < 1 || val > 100) {
            alert('请输入 1~100 之间的数字');
            return;
        }
        document.querySelectorAll('.ratio-btn').forEach(b => b.classList.remove('active'));
        currentRatio = val;
        if (currentNightDate) {
            fetchRankData(currentNightDate, currentBuilding, currentRatio);
        }
    });

    // 返回按钮
    document.getElementById('back-btn').addEventListener('click', () => {
        showView('rank');
    });

    // 加载楼栋列表
    loadBuildings();
}

// 启动
document.addEventListener('DOMContentLoaded', () => {
    loadConfig().then(() => {
        if (CONFIG.BG_URL) {
            document.body.style.backgroundImage = `url(${CONFIG.BG_URL})`;
            document.body.style.backgroundSize = 'cover';
            document.body.style.backgroundPosition = 'center';
            document.body.style.backgroundAttachment = 'fixed';
        }
        initApp();
    });
});
