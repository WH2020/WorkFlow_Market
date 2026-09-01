/**
 * Agent4Market Stage A4 - 前端组件示例
 *
 * 展示如何使用 A4 API 构建信号、建议和 Play 匹配界面
 */

// ============================================================================
// API 客户端封装
// ============================================================================

class A4ApiClient {
  constructor(baseUrl = '/api/a4') {
    this.baseUrl = baseUrl;
  }

  async request(endpoint, options = {}) {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    });

    const data = await response.json();

    if (!data.success && data.error) {
      throw new Error(data.error);
    }

    return data;
  }

  // Play 匹配
  async matchPlay(userInput, accountData = null) {
    return this.request('/match-play', {
      method: 'POST',
      body: JSON.stringify({ user_input: userInput, account_data: accountData }),
    });
  }

  // 评估信号
  async evaluateSignals(accountData) {
    return this.request('/evaluate-signals', {
      method: 'POST',
      body: JSON.stringify({ account_data: accountData }),
    });
  }

  // 获取建议
  async getRecommendations(accountId = null) {
    const query = accountId ? `?account_id=${accountId}` : '';
    return this.request(`/recommendations${query}`);
  }

  // 接受建议
  async acceptRecommendation(recommendationId, options = {}) {
    return this.request('/recommendations/accept', {
      method: 'POST',
      body: JSON.stringify({
        recommendation_id: recommendationId,
        ...options,
      }),
    });
  }

  // 忽略建议
  async ignoreRecommendation(recommendationId, reason = null) {
    return this.request('/recommendations/ignore', {
      method: 'POST',
      body: JSON.stringify({
        recommendation_id: recommendationId,
        reason,
      }),
    });
  }

  // 验证工作流输入
  async validateWorkflowInput(workflowId, providedInput) {
    return this.request('/validate-workflow-input', {
      method: 'POST',
      body: JSON.stringify({
        workflow_id: workflowId,
        provided_input: providedInput,
      }),
    });
  }

  // 获取工作流摘要
  async getWorkflowSummary(workflowId) {
    return this.request(`/workflow-summary?workflow_id=${workflowId}`);
  }

  // 获取采纳率
  async getAdoptionRate() {
    return this.request('/adoption-rate');
  }
}

// ============================================================================
// UI 组件示例
// ============================================================================

/**
 * 信号面板组件
 * 显示客户的高优先级信号
 */
class SignalPanel {
  constructor(containerId, apiClient) {
    this.container = document.getElementById(containerId);
    this.apiClient = apiClient;
  }

  async render(accountData) {
    const response = await this.apiClient.evaluateSignals(accountData);
    const signals = response.data.signals;

    if (signals.length === 0) {
      this.container.innerHTML = '<div class="no-signals">暂无需要关注的信号</div>';
      return;
    }

    const html = signals.map(signal => `
      <div class="signal-card signal-${signal.severity}">
        <div class="signal-header">
          <span class="signal-badge">${this.getSeverityLabel(signal.severity)}</span>
          <span class="signal-type">${signal.signal_type}</span>
        </div>
        <h3>${signal.title}</h3>
        <p>${signal.description}</p>
        <div class="signal-actions">
          ${signal.suggested_actions.map(action => `<li>${action}</li>`).join('')}
        </div>
      </div>
    `).join('');

    this.container.innerHTML = html;
  }

  getSeverityLabel(severity) {
    const labels = {
      high: '高优先级',
      medium: '中优先级',
      low: '低优先级',
    };
    return labels[severity] || severity;
  }
}

/**
 * 建议卡片组件
 * 显示待处理的行动建议
 */
class RecommendationCard {
  constructor(containerId, apiClient) {
    this.container = document.getElementById(containerId);
    this.apiClient = apiClient;
  }

  async render(accountId = null) {
    const response = await this.apiClient.getRecommendations(accountId);
    const recommendations = response.data.recommendations;

    if (recommendations.length === 0) {
      this.container.innerHTML = '<div class="no-recommendations">暂无建议</div>';
      return;
    }

    const html = recommendations.map(rec => `
      <div class="recommendation-card" data-rec-id="${rec.recommendation_id}">
        <div class="rec-header">
          <span class="rec-priority priority-${rec.priority}">${rec.priority}</span>
          <span class="rec-account">${rec.account_name}</span>
        </div>
        <h3>${rec.title}</h3>
        <p>${rec.description}</p>
        <div class="rec-suggestions">
          <strong>建议行动：</strong>
          <ul>
            ${rec.suggested_actions.map(action => `<li>${action}</li>`).join('')}
          </ul>
        </div>
        <div class="rec-actions">
          <button class="btn-accept" data-rec-id="${rec.recommendation_id}">接受</button>
          <button class="btn-edit" data-rec-id="${rec.recommendation_id}">编辑后接受</button>
          <button class="btn-ignore" data-rec-id="${rec.recommendation_id}">忽略</button>
        </div>
      </div>
    `).join('');

    this.container.innerHTML = html;

    // 绑定事件
    this.bindEvents();
  }

  bindEvents() {
    this.container.querySelectorAll('.btn-accept').forEach(btn => {
      btn.addEventListener('click', (e) => this.handleAccept(e.target.dataset.recId));
    });

    this.container.querySelectorAll('.btn-edit').forEach(btn => {
      btn.addEventListener('click', (e) => this.handleEdit(e.target.dataset.recId));
    });

    this.container.querySelectorAll('.btn-ignore').forEach(btn => {
      btn.addEventListener('click', (e) => this.handleIgnore(e.target.dataset.recId));
    });
  }

  async handleAccept(recommendationId) {
    try {
      await this.apiClient.acceptRecommendation(recommendationId);
      alert('建议已接受');
      this.render(); // 重新渲染
    } catch (error) {
      alert(`操作失败: ${error.message}`);
    }
  }

  async handleEdit(recommendationId) {
    const title = prompt('请输入行动标题：');
    if (!title) return;

    const description = prompt('请输入行动描述：');
    if (!description) return;

    try {
      await this.apiClient.acceptRecommendation(recommendationId, {
        user_edits: { title, description },
      });
      alert('建议已编辑并接受');
      this.render();
    } catch (error) {
      alert(`操作失败: ${error.message}`);
    }
  }

  async handleIgnore(recommendationId) {
    const reason = prompt('请说明忽略原因（可选）：');

    try {
      await this.apiClient.ignoreRecommendation(recommendationId, reason);
      alert('建议已忽略');
      this.render();
    } catch (error) {
      alert(`操作失败: ${error.message}`);
    }
  }
}

/**
 * Play 匹配输入框组件
 * 自然语言工作入口
 */
class PlayMatcher {
  constructor(inputId, resultId, apiClient) {
    this.input = document.getElementById(inputId);
    this.result = document.getElementById(resultId);
    this.apiClient = apiClient;
  }

  init() {
    this.input.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        this.handleMatch();
      }
    });
  }

  async handleMatch() {
    const userInput = this.input.value.trim();
    if (!userInput) return;

    try {
      const response = await this.apiClient.matchPlay(userInput);
      this.renderResult(response.data);
    } catch (error) {
      this.renderError(error.message);
    }
  }

  renderResult(data) {
    if (data.status === 'no_match') {
      this.result.innerHTML = `
        <div class="no-match">
          <p>${data.message}</p>
          <p>您可以尝试：</p>
          <ul>
            ${data.suggestions.map(s => `<li>${s}</li>`).join('')}
          </ul>
        </div>
      `;
      return;
    }

    if (data.status === 'need_more_info') {
      this.result.innerHTML = `
        <div class="need-info">
          <h3>已匹配：${data.play}</h3>
          <p>请补充以下信息：</p>
          <ul>
            ${data.questions.map(q => `<li>${q}</li>`).join('')}
          </ul>
        </div>
      `;
      return;
    }

    if (data.status === 'ready') {
      this.result.innerHTML = `
        <div class="ready">
          <h3>${data.play}</h3>
          <p>预计耗时：${data.execution_plan.estimated_duration}</p>
          ${data.signals.length > 0 ? `
            <div class="context-signals">
              <strong>检测到 ${data.signals.length} 个信号</strong>
            </div>
          ` : ''}
          ${data.recommendations.length > 0 ? `
            <div class="context-recs">
              <strong>有 ${data.recommendations.length} 个待处理建议</strong>
            </div>
          ` : ''}
          <button class="btn-start-workflow">开始执行</button>
        </div>
      `;
    }
  }

  renderError(message) {
    this.result.innerHTML = `
      <div class="error">
        <p>错误：${message}</p>
      </div>
    `;
  }
}

// ============================================================================
// 使用示例
// ============================================================================

/*
// 初始化
const apiClient = new A4ApiClient('/api/a4');

// 信号面板
const signalPanel = new SignalPanel('signal-container', apiClient);
signalPanel.render(currentAccountData);

// 建议卡片
const recCard = new RecommendationCard('recommendation-container', apiClient);
recCard.render(currentAccountId);

// Play 匹配器
const playMatcher = new PlayMatcher('play-input', 'play-result', apiClient);
playMatcher.init();
*/
