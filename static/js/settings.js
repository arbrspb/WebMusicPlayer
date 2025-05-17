// settings.js 15-05-25 18-25
console.log("window.settingsConfig:", window.settingsConfig); // отладка

window.settingsConfig = window.settingsConfig || {};

const musicDir      = window.settingsConfig.musicDir      || "";
const playbackMode  = window.settingsConfig.playbackMode  || "";
const defaultVolume = window.settingsConfig.defaultVolume || 100;
const soundQuality  = window.settingsConfig.soundQuality  || "";
const selectedDevice= window.settingsConfig.selectedDevice || 0;
const devices       = window.settingsConfig.devices        || [];
const isHost        = window.settingsConfig.isHost         || false;
const favoriteMode  = window.settingsConfig.favoriteMode   || "stay";

document.addEventListener("DOMContentLoaded", function() {
  // Актуализируем localStorage с актуальным значением favoriteMode из сервера
  if (window.settingsConfig && window.settingsConfig.favoriteMode !== undefined) {
    localStorage.setItem("favoriteMode", window.settingsConfig.favoriteMode);
  }

  // --- Обработка отправки формы настроек избранных треков ---
  var favSettingsForm = document.getElementById("favSettingsForm");
  if (favSettingsForm) {
    favSettingsForm.addEventListener("submit", function(event) {
      event.preventDefault();
      var formData = new FormData(favSettingsForm);
      fetch("/update_fav_settings", {
        method: "POST",
        body: formData
      })
      .then(() => {
        window.location.reload();
      })
      .catch(err => {
        console.error("Ошибка обновления настройки избранных:", err);
        alert("Ошибка обновления настроек избранных");
      });
    });
  }

    // --- Регистрация обработчика для модального окна жанровых настроек ---
  var genreSettingsModal = document.getElementById("genreSettingsModal");
  if (genreSettingsModal) {
    // Используем событие "shown.bs.modal", чтобы дождаться полной анимации открытия окна
    genreSettingsModal.addEventListener("shown.bs.modal", loadGenreSettings);
  }

  // Если localStorage не содержит favoriteMode, устанавливаем его из глобального объекта
  if (!localStorage.getItem("favoriteMode") && window.settingsConfig) {
    localStorage.setItem("favoriteMode", window.settingsConfig.favoriteMode);
  }

  // --- Кнопка переобучения ---
  var retrainBtn = document.getElementById("retrain-btn-modal");
  if (retrainBtn) {
    retrainBtn.addEventListener("click", function() {
      fetch('/retrain', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
          alert("Переобучение запущено.");

          // Закрытие модального окна
        var modalEl = document.getElementById("modelParamsModal");
        var modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) {
          modalInstance.hide();
        }
          var progressContainer = document.getElementById("progress-container");
          if (progressContainer) {
            progressContainer.style.display = "block";
          }
          checkProgress();
        })
        .catch(err => {
          console.error("Ошибка при переобучении:", err);
          alert("Ошибка: " + err);
        });
    });
  }

  // --- Обработчик для кнопки "Сохранить" в модальном окне сканирования ---
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
        var modalEl = document.getElementById("scanSettingsModal");
        var modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) {
          modalInstance.hide();
        }
      })
      .catch(err => console.log("Ошибка сохранения настроек сканирования:", err));
    });
  }

  loadScanConfig();
});

function loadScanConfig(){
  fetch('/get_scan_config?t=' + Date.now())
    .then(response => response.json())
    .then(data => {
      var scanModeSelect = document.getElementById('scanModeSelect');
      if (scanModeSelect) {
        scanModeSelect.value = data.scan_mode || 'new';
      }
    })
    .catch(err => console.log("Ошибка загрузки настроек сканирования:", err));
}

function checkProgress() {
  fetch('/training_status')
    .then(response => response.json())
    .then(data => {
      let progress = data.progress;
      var progressBar = document.getElementById("progress-bar");
      var progressText = document.getElementById("progress-text");
      if (progressBar) progressBar.value = progress;
      if (progressText) progressText.innerText = progress + "%";
      if (progress < 100) {
        setTimeout(checkProgress, 500);
      } else {
        alert("Обучение завершено.");
      }
    })
    .catch(err => console.error("Ошибка получения статуса:", err));
}
// --- Обработчик сохранения жанровых настроек ---
function saveGenreSettings() {
  var input = document.getElementById("genreSettingsInput");
  if (!input) {
    alert("Элемент не найден");
    return;
  }
  var parts = input.value.split(",");
  var newSettings = {};
  parts.forEach(function(part) {
    var pair = part.split(":");
    if (pair.length === 2) {
      newSettings[pair[0].trim().toLowerCase()] = pair[1].trim();
    }
  });

  // Проверка на пустой ввод
  if (Object.keys(newSettings).length === 0) {
    alert("Проверьте формат ввода! Пример: rock:гитара, pop:танцы");
    return;
  }

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
      if (!modalInstance) {
        modalInstance = new bootstrap.Modal(modalEl);
      }
      modalInstance.hide();
    } else {
      alert("Ошибка сохранения: " + (result.message || "Неизвестная ошибка"));
    }
  })
  .catch(err => {
    alert("Ошибка сохранения: " + err);
  });
}

function loadGenreSettings() {
  fetch("/custom_keywords")
    .then(response => response.json())
    .then(data => {
      var input = document.getElementById("genreSettingsInput");
      if (!input) return;
      // data.keywords — это объект {жанр: ключи, ...}
      // Преобразуем обратно в строку вида "rock:гитара, pop:танцы"
      let settings = [];
      for (let genre in data.keywords) {
        settings.push(`${genre}:${data.keywords[genre]}`);
      }
      input.value = settings.join(", ");
    })
    .catch(err => {
      console.error("Ошибка загрузки жанровых настроек:", err);
    });
}


