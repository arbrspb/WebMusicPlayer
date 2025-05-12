// settings.js
// Получение переменных из глобального объекта
const musicDir = window.settingsConfig.musicDir;
const playbackMode = window.settingsConfig.playbackMode;
const defaultVolume = window.settingsConfig.defaultVolume;
const soundQuality = window.settingsConfig.soundQuality;
const selectedDevice = window.settingsConfig.selectedDevice;
const devices = window.settingsConfig.devices;
const isHost = window.settingsConfig.isHost;

// --- Инициализация обработчиков и загрузка конфигураций ---
document.addEventListener("DOMContentLoaded", function() {
  // --- Модальное окно жанровых настроек ---
  var genreSettingsModal = document.getElementById("genreSettingsModal");
  if (genreSettingsModal) {
    genreSettingsModal.addEventListener('shown.bs.modal', function () {
      fetch("/custom_keywords")
        .then(response => response.json())
        .then(data => {
          var settingsStr = "";
          for (const key in data.keywords) {
            settingsStr += key + ":" + data.keywords[key] + ", ";
          }
          settingsStr = settingsStr.replace(/,\s*$/, "");
          var input = document.getElementById("genreSettingsInput");
          if (input) input.value = settingsStr;
        })
        .catch(err => console.log(err));
    });
  }

  // --- Сохранение режима сканирования ---
  var saveScanModeBtn = document.getElementById('saveScanModeBtn');
  if (saveScanModeBtn) {
    saveScanModeBtn.addEventListener('click', function(){
      var scanMode = document.getElementById('scanModeSelect').value;
      localStorage.setItem('scanMode', scanMode);

      fetch('/update_scan_config', {
        method: 'POST',
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({scan_mode: scanMode})
      })
      .then(response => response.json())
      .then(data => {
        alert("Режим сканирования сохранен: " + data.scan_mode);
        var modalInstance = bootstrap.Modal.getInstance(document.getElementById("scanSettingsModal"));
        modalInstance.hide();
      })
      .catch(err => console.log("Ошибка сохранения настроек сканирования:", err));
    });
  }

  // --- Кнопка переобучения ---
  var retrainBtn = document.getElementById("retrain-btn");
  if (retrainBtn) {
    retrainBtn.addEventListener("click", function() {
      fetch('/retrain', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
          alert("Переобучение запущено.");
          document.getElementById("progress-container").style.display = "block";
          checkProgress();
        })
        .catch(err => { alert("Ошибка: " + err); });
    });
  }

  // --- Конфиг сканирования при загрузке ---
  loadScanConfig();
});

// --- Жанровые настройки ---
function saveGenreSettings() {
  var input = document.getElementById("genreSettingsInput");
  if (!input) return alert("Элемент не найден");
  var parts = input.value.split(",");
  var newSettings = {};
  parts.forEach(function(part) {
    var pair = part.split(":");
    if (pair.length === 2)
      newSettings[pair[0].trim().toLowerCase()] = pair[1].trim();
  });
  fetch("/custom_keywords", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({keywords: newSettings})
  })
  .then(response => response.json())
  .then(result => {
    if (result.status === "saved") {
      alert("Ключевые слова сохранены");
      var modalEl = document.getElementById("genreSettingsModal");
      var modalInstance = bootstrap.Modal.getInstance(modalEl);
      if (!modalInstance) modalInstance = new bootstrap.Modal(modalEl);
      modalInstance.hide();
    } else {
      alert("Ошибка сохранения: " + (result.message || "Неизвестная ошибка"));
    }
  })
  .catch(err => alert("Ошибка сохранения: " + err));
}

// --- Сканирование библиотеки ---
function startScan() {
  var scanMode = localStorage.getItem('scanMode') || 'new';
  fetch('/start_scan?mode=' + scanMode)
    .then(response => response.json())
    .then(data => { pollScanProgress(); })
    .catch(err => console.log("Ошибка старта сканирования:", err));
}
function stopScan() {
  fetch('/stop_scan')
    .then(response => response.json())
    .then(data => {})
    .catch(err => console.log("Ошибка остановки сканирования:", err));
}
function pollScanProgress() {
  fetch('/scan_progress')
    .then(response => response.json())
    .then(data => {
      document.getElementById("scanProgress").innerText = "Сканировано " + data.scanned + " из " + data.total;
      if(data.status !== "completed" && data.status !== "stopped")
        setTimeout(pollScanProgress, 1000);
      else
        alert("Сканирование " + data.status);
    })
    .catch(err => console.log("Ошибка опроса статуса сканирования:", err));
}

// --- Прогресс переобучения ---
function checkProgress() {
  fetch('/training_status')
    .then(response => response.json())
    .then(data => {
      let progress = data.progress;
      document.getElementById("progress-bar").value = progress;
      document.getElementById("progress-text").innerText = progress + "%";
      if (progress < 100) setTimeout(checkProgress, 500);
      else alert("Обучение завершено.");
    })
    .catch(err => console.error("Ошибка получения статуса:", err));
}

// --- Загрузка конфига сканирования при загрузке страницы ---
function loadScanConfig(){
  fetch('/get_scan_config?t=' + Date.now())
    .then(response => response.json())
    .then(data => {
      document.getElementById('scanModeSelect').value = data.scan_mode || 'new';
    })
    .catch(err => console.log("Ошибка загрузки настроек сканирования:", err));
}