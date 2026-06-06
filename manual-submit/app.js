/* ====================================================================
   News Radar · Manual Submit Frontend
   ==================================================================== */

// === Configuration ===
var API_BASE = localStorage.getItem('news_radar_api_base') || 'http://localhost:8765';

// === State ===
var currentTab = 'url';
var imageData = null;   // base64 data for image upload

// === DOM References (cached) ===
var dom = {};

function cacheDom() {
  dom.statusDot = document.getElementById('status-dot');
  dom.statusText = document.getElementById('status-text');
  dom.submitBtn = document.getElementById('btn-submit');
  dom.submitStatus = document.getElementById('submit-status');
  dom.fileInput = document.getElementById('file-input');
  dom.uploadZone = document.getElementById('upload-zone');
  dom.imgPreview = document.getElementById('img-preview');
  dom.btnRemoveImg = document.getElementById('btn-remove-img');
  dom.historyList = document.getElementById('history-list');
  dom.historyCount = document.getElementById('history-count');
  dom.toastContainer = document.getElementById('toast-container');
}

// ====================================================================
// Tab Switching
// ====================================================================

function switchTab(tabId) {
  currentTab = tabId;

  // Update tab buttons
  document.querySelectorAll('.tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });

  // Update panes
  document.querySelectorAll('.tab-pane').forEach(function(p) {
    p.classList.toggle('active', p.id === 'pane-' + tabId);
  });
}

// ====================================================================
// Image Upload Handling
// ====================================================================

// Click upload zone → trigger file input
document.addEventListener('DOMContentLoaded', function() {
  cacheDom();

  if (dom.uploadZone) {
    dom.uploadZone.addEventListener('click', function(e) {
      if (e.target.closest('.btn-remove')) return;
      dom.fileInput.click();
    });

    // Drag and drop
    dom.uploadZone.addEventListener('dragover', function(e) {
      e.preventDefault();
      dom.uploadZone.classList.add('drag-over');
    });

    dom.uploadZone.addEventListener('dragleave', function(e) {
      e.preventDefault();
      dom.uploadZone.classList.remove('drag-over');
    });

    dom.uploadZone.addEventListener('drop', function(e) {
      e.preventDefault();
      dom.uploadZone.classList.remove('drag-over');
      if (e.dataTransfer.files.length > 0) {
        handleImageFile(e.dataTransfer.files[0]);
      }
    });
  }

  if (dom.fileInput) {
    dom.fileInput.addEventListener('change', function(e) {
      if (e.target.files.length > 0) {
        handleImageFile(e.target.files[0]);
      }
    });
  }

  // YouTube URL auto-preview
  var ytInput = document.getElementById('input-yt');
  if (ytInput) {
    ytInput.addEventListener('blur', function() { tryFetchYTPreview(this.value); });
    ytInput.addEventListener('paste', function() {
      setTimeout(function() { tryFetchYTPreview(ytInput.value); }, 100);
    });
  }

  // Text area char count
  var textInput = document.getElementById('input-text');
  if (textInput) {
    textInput.addEventListener('input', function() {
      var cc = document.getElementById('text-char-count');
      if (cc) cc.textContent = textInput.value.length + ' 字';
    });
  }

  // Platform chip click
  document.querySelectorAll('.chip[data-platform]').forEach(function(c) {
    c.addEventListener('click', function(e) {
      if (e.target.closest('#chip-all')) return;
      var cb = this.querySelector('.platform-cb');
      if (cb) {
        cb.checked = !cb.checked;
        this.classList.toggle('selected', cb.checked);
      }
    });
  });

  // Schedule chip click
  document.querySelectorAll('.chip[data-schedule]').forEach(function(c) {
    c.addEventListener('click', function(e) {
      document.querySelectorAll('.chip[data-schedule]').forEach(function(s) { s.classList.remove('selected'); });
      this.classList.add('selected');
      var rb = this.querySelector('input[type="radio"]');
      if (rb) rb.checked = true;
    });
  });

  // Initial health check
  checkHealth();
  loadHistory();
});

function handleImageFile(file) {
  var allowed = ['image/jpeg', 'image/png', 'image/heic', 'image/heif'];
  if (allowed.indexOf(file.type) === -1) {
    showToast('不支援的圖片格式：' + (file.type || file.name), 'error');
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    showToast('圖片太大（超過 20MB）', 'error');
    return;
  }

  var reader = new FileReader();
  reader.onload = function(e) {
    imageData = e.target.result;
    dom.imgPreview.src = imageData;
    dom.imgPreview.style.display = 'block';
    dom.btnRemoveImg.style.display = 'block';
    dom.uploadZone.querySelector('.upload-icon').style.display = 'none';
    dom.uploadZone.querySelector('.upload-text').textContent = file.name;
    dom.uploadZone.querySelector('.upload-text').style.color = 'var(--text)';
    dom.uploadZone.querySelector('.upload-hint').style.display = 'none';
  };
  reader.readAsDataURL(file);
}

function clearImage() {
  imageData = null;
  dom.imgPreview.style.display = 'none';
  dom.btnRemoveImg.style.display = 'none';
  dom.fileInput.value = '';
  var zone = dom.uploadZone;
  zone.querySelector('.upload-icon').style.display = 'block';
  zone.querySelector('.upload-text').textContent = '點擊選擇圖片或拖曳至此';
  zone.querySelector('.upload-text').style.color = '';
  zone.querySelector('.upload-hint').style.display = 'block';
}

// ====================================================================
// YouTube Preview (oEmbed)
// ====================================================================

function tryFetchYTPreview(url) {
  var preview = document.getElementById('yt-preview');
  if (!url || !url.match(/(youtube\.com|youtu\.be)/)) {
    preview.style.display = 'none';
    return;
  }
  var videoId = extractYTId(url);
  if (!videoId) { preview.style.display = 'none'; return; }

  // Simple oembed call (no-api approach)
  var oembedUrl = 'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=' + videoId + '&format=json';
  fetch(oembedUrl)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      document.getElementById('yt-title').textContent = data.title || 'Unknown';
      document.getElementById('yt-channel').textContent = data.author_name || '';
      document.getElementById('yt-thumb').style.backgroundImage = "url('https://img.youtube.com/vi/" + videoId + "/mqdefault.jpg')";
      preview.style.display = 'flex';
    })
    .catch(function() {
      // Still show thumbnail even if oembed fails
      document.getElementById('yt-title').textContent = 'YouTube Video';
      document.getElementById('yt-channel').textContent = '';
      document.getElementById('yt-thumb').style.backgroundImage = "url('https://img.youtube.com/vi/" + videoId + "/mqdefault.jpg')";
      preview.style.display = 'flex';
    });
}

function extractYTId(url) {
  var m = url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

// ====================================================================
// Platform toggle (all/none)
// ====================================================================

function toggleAllPlatforms() {
  var cbs = document.querySelectorAll('.platform-cb');
  var allChecked = true;
  cbs.forEach(function(cb) { if (!cb.checked) allChecked = false; });
  var newState = !allChecked;
  cbs.forEach(function(cb) {
    cb.checked = newState;
    var chip = cb.closest('.chip');
    if (chip) chip.classList.toggle('selected', newState);
  });
}

// ====================================================================
// Submit Handler
// ====================================================================

function handleSubmit() {
  var content = '';
  var type = currentTab;

  // Validate and get content
  if (type === 'url') {
    content = getVal('input-url');
    if (!content) { showToast('請輸入網址', 'error'); return; }
  } else if (type === 'text') {
    content = getVal('input-text');
    if (!content || content.length < 10) { showToast('請輸入文章內容（至少 10 字）', 'error'); return; }
  } else if (type === 'youtube') {
    content = getVal('input-yt');
    if (!content) { showToast('請輸入 YouTube 網址', 'error'); return; }
    if (!content.match(/(youtube\.com|youtu\.be)/)) { showToast('請輸入有效的 YouTube 網址', 'error'); return; }
  } else if (type === 'image') {
    if (!imageData) { showToast('請先選擇一張圖片', 'error'); return; }
    content = imageData;  // base64 data URL
  }

  // Get platforms
  var platforms = [];
  document.querySelectorAll('.platform-cb:checked').forEach(function(cb) {
    platforms.push(cb.value);
  });
  if (platforms.length === 0) { showToast('請選擇至少一個平台', 'error'); return; }

  // Get schedule
  var schedule = 'next';
  var scheduleRb = document.querySelector('input[name="schedule"]:checked');
  if (scheduleRb) schedule = scheduleRb.value;

  // Get note
  var note = getVal('input-note');

  // Disable button + show loading
  var btn = dom.submitBtn;
  btn.disabled = true;
  btn.classList.add('loading');
  btn.querySelector('.btn-text').textContent = '提交中';

  var payload = {
    type: type,
    content: content,
    platforms: platforms,
    note: note,
    schedule: schedule,
  };

  // Submit via POST /api/submit
  fetch(API_BASE + '/api/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(function(r) { return r.json().then(function(d) { return { status: r.status, data: d }; }); })
  .then(function(resp) {
    var data = resp.data;
    if (resp.status >= 200 && resp.status < 300 && data.status !== 'error') {
      var msg = '';
      if (data.status === 'created') msg = '✅ 已提交！待 pipeline 處理';
      else if (data.status === 'already_exists') msg = 'ℹ️ 此內容已存在，跳過';

      if (schedule === 'now' && data.message) {
        msg += ' 🚀 已觸即時發布';
      }
      showSubmitStatus(msg, 'success');
      showToast(msg, 'success');

      // Clear form
      clearForm(type);
      loadHistory();
    } else {
      showSubmitStatus('❌ ' + (data.detail || data.message || '提交失敗'), 'error');
      showToast('提交失敗：' + (data.detail || data.message || '未知錯誤'), 'error');
    }
  })
  .catch(function(err) {
    showSubmitStatus('❌ 無法連線到 API 伺服器 (' + API_BASE + ')\n' + err.message, 'error');
    showToast('無法連線到伺服器。請確認 API Server 有在執行。', 'error');
  })
  .finally(function() {
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.querySelector('.btn-text').textContent = '提交';
  });
}

function getVal(id) {
  var el = document.getElementById(id);
  return el ? el.value.trim() : '';
}

function clearForm(type) {
  if (type === 'url') setVal('input-url', '');
  else if (type === 'text') setVal('input-text', '');
  else if (type === 'youtube') {
    setVal('input-yt', '');
    document.getElementById('yt-preview').style.display = 'none';
  } else if (type === 'image') clearImage();
  setVal('input-note', '');
}

function setVal(id, val) {
  var el = document.getElementById(id);
  if (el) el.value = val;
}

function showSubmitStatus(msg, type) {
  var el = dom.submitStatus;
  el.textContent = msg;
  el.className = 'submit-status ' + type;
  el.style.display = 'block';

  // Auto-hide after 8s
  if (window._statusTimer) clearTimeout(window._statusTimer);
  window._statusTimer = setTimeout(function() {
    el.style.display = 'none';
  }, 8000);
}

// ====================================================================
// Health Check
// ====================================================================

function checkHealth() {
  dom.statusDot.className = 'status-dot checking';
  dom.statusText.textContent = '檢查中…';

  fetch(API_BASE + '/api/status', { signal: AbortSignal.timeout(5000) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.status === 'ok') {
        dom.statusDot.className = 'status-dot online';
        dom.statusText.textContent = '已連線';
      } else {
        throw new Error('unexpected');
      }
    })
    .catch(function() {
      dom.statusDot.className = 'status-dot offline';
      dom.statusText.textContent = '離線';
    });
}

// ====================================================================
// History
// ====================================================================

function loadHistory() {
  fetch(API_BASE + '/api/history?limit=20', { signal: AbortSignal.timeout(5000) })
    .then(function(r) { return r.json(); })
    .then(function(items) {
      dom.historyCount.textContent = items.length;
      if (items.length === 0) {
        dom.historyList.innerHTML = '<div class="history-empty">暫無提交紀錄</div>';
        return;
      }
      dom.historyList.innerHTML = items.map(function(item) {
        var iconMap = {
          url: '🔗',
          youtube: '▶️',
          text: '📝',
          image: '🖼️',
          image_base64: '🖼️',
        };
        var icon = iconMap[item.type] || '📎';
        var statusClass = item.status === 'created' ? 'created' :
                          item.status === 'already_exists' ? 'already_exists' : 'error';
        return '<div class="history-item">' +
          '<span class="history-item-icon">' + icon + '</span>' +
          '<div class="history-item-content">' +
          '<div class="history-item-title">' + escapeHtml(item.content_preview || '—') + '</div>' +
          '<div class="history-item-meta">' +
          '<span>' + item.type + '</span>' +
          '<span>' + formatTime(item.submitted_at) + '</span>' +
          (item.title ? '<span>' + escapeHtml(item.title) + '</span>' : '') +
          '</div>' +
          '</div>' +
          '<span class="history-item-status ' + statusClass + '">' + item.status + '</span>' +
          '</div>';
      }).join('');
    })
    .catch(function() {
      // Silently fail — history is helper, not critical
    });
}

function escapeHtml(str) {
  var div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatTime(iso) {
  if (!iso) return '—';
  try {
    var d = new Date(iso);
    var now = new Date();
    var diff = Math.floor((now - d) / 1000);
    if (diff < 60) return '幾秒前';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分鐘前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小時前';
    return d.toLocaleDateString('zh-TW', { month: 'short', day: 'numeric' });
  } catch (e) {
    return iso.slice(0, 10);
  }
}

// ====================================================================
// Toast
// ====================================================================

function showToast(msg, type) {
  type = type || 'info';
  var toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.textContent = msg;
  dom.toastContainer.appendChild(toast);

  setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(function() { toast.remove(); }, 300);
  }, 4000);
}
