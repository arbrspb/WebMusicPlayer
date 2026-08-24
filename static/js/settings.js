// settings.js 14-08-25 01-50
// Включить/выключить логи через переменную
window.ENABLE_LOGS = true; // или false для отключения

if (!window.ENABLE_LOGS) {
  ["log", "warn", "error", "info", "debug"].forEach(function(method) {
    console[method] = function() {};
  });
}
console.log("window.settingsConfig:", window.settingsConfig); // отладка

window.settingsConfig = window.settingsConfig || {};
console.log("window.settingsConfig.debugEnabled:", window.settingsConfig.debugEnabled);
console.log("DEBUG: window.settingsConfig:", window.settingsConfig);
console.log("DEBUG: window.settingsConfig.debugEnabled:", window.settingsConfig.debugEnabled, "typeof:", typeof window.settingsConfig.debugEnabled);

const musicDir      = window.settingsConfig.musicDir      || "";
const playbackMode  = window.settingsConfig.playbackMode  || "";
const defaultVolume = window.settingsConfig.defaultVolume || 100;
const soundQuality  = window.settingsConfig.soundQuality  || "";
const selectedDevice= window.settingsConfig.selectedDevice || 0;
const devices       = window.settingsConfig.devices        || [];
const isHost        = window.settingsConfig.isHost         || false;
const favoriteMode  = window.settingsConfig.favoriteMode   || "stay";
const advancedModeDefault = window.settingsConfig.advancedMode !== undefined ? window.settingsConfig.advancedMode : false;



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
    // 1. Проверить при загрузке, показывать ли кнопку "Логирование"



  // 2. Логика работы с модалкой для настройки логирования
  const logModal = document.getElementById('logModal');
  if (logModal) {
    logModal.addEventListener('show.bs.modal', function() {
      fetch('/log_settings')
        .then(r => r.json())
        .then(flags => {
          console.log("DEBUG log_flags from backend:", flags);
          console.log("log_settings flags:", flags);  // <-- ОТЛАДКА
          let container = document.getElementById('logFlagsContainer');
          if (!container) {
            alert("Не найден контейнер logFlagsContainer!");
            return;
          }
          container.innerHTML = '';
          for (let key in flags) {
            let checked = flags[key] ? "checked" : "";
            container.innerHTML += `
              <div class="form-check">
                <input class="form-check-input" type="checkbox" name="${key}" id="logflag_${key}" ${checked}>
                <label class="form-check-label" for="logflag_${key}">${key}</label>
              </div>`;
          }
          if (container.innerHTML.trim() === '') {
            container.innerHTML = '<div class="text-muted">Нет доступных флагов логирования</div>';
          }
        })
        .catch(err => {
          alert("Ошибка загрузки лог-флагов: " + err);
        });
    });
  }

  // --- После submit лог-флагов обновлять кнопку (или просто скрыть модалку) ---
  const logForm = document.getElementById('logForm');
  if (logForm) {
    logForm.addEventListener('submit', function(e) {
      e.preventDefault();
      let form = e.target;
      let data = new FormData(form);
      fetch(form.action, {
        method: "POST",
        body: data
      }).then(() => {
        var modal = bootstrap.Modal.getInstance(document.getElementById('logModal'));
        modal.hide();
      });
    });
  }
// --- Обработка отправки формы смены аудиоустройства с подтверждением ---
var deviceForm = document.getElementById("deviceForm");
if (deviceForm) {
  // Получаем select и запоминаем стартовое значение
  var deviceSelect = deviceForm.querySelector('select[name="device"]');
  var initialDeviceValue = deviceSelect ? deviceSelect.value : null;

  deviceForm.addEventListener("submit", function(event) {
    var currentDeviceValue = deviceSelect ? deviceSelect.value : null;

    // Если устройство не менялось — НЕ отправляем форму и НЕ останавливаем музыку!
    if (deviceSelect && initialDeviceValue !== null && currentDeviceValue === initialDeviceValue) {
      event.preventDefault();
      // Можно (по желанию) вывести alert или всплывашку "Устройство не менялось"
      return;
    }

    // Если устройство менялось — показываем модалку и только после confirm отправляем
    event.preventDefault();
    var modalEl = document.getElementById('deviceChangeModal');
    var modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    modalInstance.show();

    // Обработка кнопок
    var confirmBtn = document.getElementById('confirmDeviceChangeBtn');
    var cancelBtn = document.getElementById('cancelDeviceChangeBtn');

    // Очищаем предыдущие обработчики
    confirmBtn.onclick = null;
    cancelBtn.onclick = null;

    confirmBtn.onclick = function() {
      modalInstance.hide();
      deviceForm.submit();
    };
    cancelBtn.onclick = function() {
      modalInstance.hide();
    };
  });
}
  // --- Обработка отправки основной формы настроек ---
var settingsForm = document.getElementById("settingsForm");
if (settingsForm) {
  settingsForm.addEventListener("submit", function(event) {
    event.preventDefault();
    var formData = new FormData(settingsForm);
    fetch("/settings", {
      method: "POST",
      body: formData,
      redirect: "follow"
    })
    .then(response => {
      // После POST всегда будет редирект на /settings?settings_saved=1
      if (response.redirected) {
        localStorage.removeItem("currentVolume");
        window.location.href = response.url;
      } else {
        // fallback, если не сработал redirect
        window.location.href = "/settings?settings_saved=1";
      }
    })
    .catch(err => {
      console.error("Ошибка сохранения настроек:", err);
      alert("Ошибка сохранения настроек");
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
    // Проверка наличия модели
    fetch('/check_model')
      .then(response => response.json())
      .then(data => {
        if (data.exists) {
          showRetrainWarningModal();
        } else {
          startRetrain(false);
        }
      })
      .catch(err => {
        // Если ошибка с сервером, сразу стартуем обучение
        startRetrain(false);
      });
  });
}

function showRetrainWarningModal() {
  let existing = document.getElementById('retrainWarningModal');
  if (existing) existing.remove();
  let modalHtml = `
    <div class="modal fade" id="retrainWarningModal" tabindex="-1" aria-labelledby="retrainWarningModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-warning">
            <h5 class="modal-title" id="retrainWarningModalLabel">Переобучение модели</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>Модель уже обучена и будет перезаписана. Продолжить?</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Отмена</button>
            <button type="button" class="btn btn-primary" id="confirmRetrainBtn">Переобучить</button>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  let modalEl = document.getElementById('retrainWarningModal');
  let modalInstance = new bootstrap.Modal(modalEl);
  modalInstance.show();
  document.getElementById("confirmRetrainBtn").onclick = function() {
    modalInstance.hide();
    startRetrain(true);
  };
  modalEl.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => { modalEl.remove(); }, 200);
  });
}

function startRetrain(force=false) {
  fetch('/retrain' + (force ? '?force=1' : ''), { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.error || data.training_error) {
        showMemoryErrorModal(data.error || data.training_error);
        return;
      }
      alert("Переобучение запущено.");
      var modalEl = document.getElementById("modelParamsModal");
      var modalInstance = bootstrap.Modal.getInstance(modalEl);
      if (modalInstance) { modalInstance.hide(); }
      var progressContainer = document.getElementById("progress-container");
      if (progressContainer) { progressContainer.style.display = "block"; }
      if (typeof window.refreshTrainingProgress === "function") {
        window.refreshTrainingProgress();
      } else {
        checkProgress();
      }
    })
    .catch(err => {
      console.error("Ошибка при переобучении:", err);
      showMemoryErrorModal("Ошибка: " + err);
    });
}

function showTrainingErrorModal(message) {
  let existing = document.getElementById('trainingErrorModal');
  if (existing) existing.remove();
  let modalHtml = `
    <div class="modal fade" id="trainingErrorModal" tabindex="-1" aria-labelledby="trainingErrorModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-danger text-white">
            <h5 class="modal-title" id="trainingErrorModalLabel">Ошибка обучения модели</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>${message}</p>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  let modalEl = document.getElementById('trainingErrorModal');
  let modalInstance = new bootstrap.Modal(modalEl);
  modalInstance.show();
  modalEl.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => { modalEl.remove(); }, 200);
  });
}

  // --- Обработчик для кнопки "Сохранить" в модальном окне сканирования ---
  var saveScanModeBtn = document.getElementById('saveScanModeBtn');
  if (saveScanModeBtn) {
    saveScanModeBtn.addEventListener('click', function(){
      var scanPriority = document.getElementById('scanPrioritySelect').value;

      fetch('/update_scan_config', {
        method: 'POST',
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({scan_mode: 'continue', scan_priority: scanPriority})
      })
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        var modalEl = document.getElementById("scanSettingsModal");
        var modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) {
          modalInstance.hide();
        }
        return data;
      })
      .catch(err => showScanErrorModal("Не удалось сохранить приоритет сканирования: " + err.message));
    });
  }
  var fullRescanBtn = document.getElementById('full-rescan-start');
  if (fullRescanBtn) fullRescanBtn.addEventListener('click', startFullRescan);

  loadScanConfig();
  setTimeout(updateScanProgress, 300);
  // --- Переключатель расширенного режима ---
const advancedSwitch = document.getElementById("advancedModeSwitch");
const advancedSection = document.querySelector(".custom-btn-group"); // блок Дополнительное
if (advancedSwitch) {
  // Функция показа/скрытия блока
  function toggleAdvancedSection(on) {
    if (advancedSection) {
      advancedSection.style.display = on ? "" : "none";
    }
    // Можно скрывать/показывать и другие элементы по классу или id!
    // Например, progress-container, scanProgress и др.
    document.querySelectorAll('.hide-in-simple-mode').forEach(el => {
      el.style.display = on ? "" : "none";
    });
  }
  // Инициализация
  toggleAdvancedSection(advancedSwitch.checked);
  advancedSwitch.addEventListener("change", function() {
    toggleAdvancedSection(advancedSwitch.checked);
    // Отправляем на сервер
    fetch("/set_advanced_mode", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ advanced_mode: advancedSwitch.checked })
    }).then(() => {
      // После переключения сразу обновляем страницу (перенаправляем на главную)
      //window.location.href = "/";
    });
  });
}
     // Переключатель для принудительного переключения модели для рекомендаций
    const forceModelSwitch = document.getElementById("forceModelForRecommendSwitch");
if (forceModelSwitch) {
  forceModelSwitch.addEventListener("change", function() {
    fetch("/set_force_model_for_recommend", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ force_model_for_recommend: forceModelSwitch.checked })
    }).then(() => {
      // Можно перезагрузить страницу или обновить UI по желанию
      window.location.reload();
    });
  });
}
updateLoggingLinkVisibility();
});

// Показываем или скрываем кнопку "Логирование" по состоянию master-флага
function updateLoggingLinkVisibility() {
  let debugEnabled = window.settingsConfig && window.settingsConfig.debugEnabled;
  // Диагностика: выведем значение до преобразования
  console.log("[DEBUG] updateLoggingLinkVisibility: window.settingsConfig.debugEnabled =", debugEnabled, typeof debugEnabled);

  if (typeof debugEnabled === "string") {
    debugEnabled = debugEnabled === "true";
  }
  // Диагностика: выведем значение после преобразования
  console.log("[DEBUG] updateLoggingLinkVisibility: debugEnabled (final) =", debugEnabled, typeof debugEnabled);

  const loggingNavItem = document.getElementById("loggingNavItem");
  // Диагностика: найден ли элемент
  console.log("[DEBUG] updateLoggingLinkVisibility: loggingNavItem =", loggingNavItem);

  if (loggingNavItem) {
    if (!debugEnabled) {
      loggingNavItem.style.display = "none";
      console.log("[DEBUG] updateLoggingLinkVisibility: скрываем логирование");
    } else {
      loggingNavItem.style.display = "";
      console.log("[DEBUG] updateLoggingLinkVisibility: показываем логирование");
    }
  } else {
    console.log("[DEBUG] updateLoggingLinkVisibility: loggingNavItem не найден в DOM!");
  }
}
  // Показать модальное окно после сохранения настроек
  if (window.location.search.includes('settings_saved=1')) {
    var modal = new bootstrap.Modal(document.getElementById('settingsSavedModal'));
    modal.show();
    // Убираем параметр из адреса
    const url = new URL(window.location);
    url.searchParams.delete('settings_saved');
    window.history.replaceState({}, '', url);
  }

function loadScanConfig(){
  fetch('/get_scan_config?t=' + Date.now())
    .then(response => response.json())
    .then(data => {
      var scanPrioritySelect = document.getElementById('scanPrioritySelect');
      if (scanPrioritySelect) scanPrioritySelect.value = data.scan_priority || 'medium';
    })
    .catch(err => console.log("Ошибка загрузки настроек сканирования:", err));
}

function checkProgress() {
  fetch('/training_status')
    .then(response => response.json())
    .then(data => {
      var progressBarLoading = document.getElementById('progress-bar-loading');
      var progressBarNormal = document.getElementById('progress-bar-normal');
      var progressBar = document.getElementById("progress-bar");
      var progressText = document.getElementById("progress-text");
      var progressContainer = document.getElementById("progress-container");

      // --- Унифицированная обработка ошибок ---
      if (data.error || data.training_error) {
        let msg = data.error || data.training_error;
        if (/модель жанров не найден|обучите модель/i.test(msg)) {
          showScanErrorModal(msg); // Модель не найдена
        } else if (/MemoryError|Unable to allocate|OutOfMemory|Недостаточно памяти|memory/i.test(msg)) {
          showMemoryErrorModal(msg); // Ошибка памяти
        } else {
          showTrainingErrorModal(msg); // Прочие ошибки обучения
        }
        if (progressContainer) progressContainer.style.display = "none";
        return; // Не показываем обычные статусы, если есть MemoryError
      }

      if (progressContainer) progressContainer.style.display = "block";

      let progress = data.progress;

      if (progress === 0) {
        // Этап инициализации (фильтрация дублей, подготовка)
        if (progressBarLoading) progressBarLoading.style.display = "";
        if (progressBarNormal) progressBarNormal.style.display = "none";
        if (progressText) progressText.textContent = "Инициализация...";
      } else if (progress < 100) {
        // Само обучение — обычный прогресс-бар, отображение процента
        if (progressBarLoading) progressBarLoading.style.display = "none";
        if (progressBarNormal) progressBarNormal.style.display = "";
        if (progressBar) {
          progressBar.style.width = progress + "%";
          progressBar.setAttribute("aria-valuenow", progress);
        }
        if (progressText) progressText.textContent = `Обучение модели… ${progress}%`;
      } else {
        // Обучение завершено
        if (progressBarLoading) progressBarLoading.style.display = "none";
        if (progressBarNormal) progressBarNormal.style.display = "";
        if (progressBar) {
          progressBar.style.width = "100%";
          progressBar.setAttribute("aria-valuenow", 100);
        }
        if (progressText) progressText.textContent = "Обучение завершено!";
        alert("Обучение завершено.");
      }
      // Следующее обновление, пока не завершено
      if (progress < 100) {
        setTimeout(checkProgress, 3000);
      }
    })
    .catch(err => console.error("Ошибка получения статуса:", err));
}

let currentGenreSettings = {};
let trainableGenresSet = new Set();

// Подсказка о необходимых папках
function showGenreFoldersHint() {
  const hintDiv = document.getElementById('genreFolderHint');
  if (!hintDiv) return;
  const genres = Array.from(trainableGenresSet);
  if (!genres.length) {
    hintDiv.classList.add('d-none');
    hintDiv.innerHTML = '';
    return;
  }
  let html = '<b>Убедитесь, что созданы папки для обучения:</b><br>';
  html += genres.map(g => 'samples/' + g).join('<br>');
  hintDiv.innerHTML = html;
  hintDiv.classList.remove('d-none');
}

// Загрузка и отрисовка жанров
function loadGenreSettings() {
  fetch("/custom_keywords")
    .then(response => response.json())
    .then(data => {
      currentGenreSettings = data.keywords || {};
      // Заполняем поле ввода
      var input = document.getElementById("genreSettingsInput");
      if (input) {
        let settings = [];
        for (let key in currentGenreSettings) {
          let val = currentGenreSettings[key];
          if (typeof val === "string") {
            settings.push(`${key}:${val}`);
          } else if (val && typeof val === "object") {
            settings.push(`${key}:${val.genre}`);
          }
        }
        input.value = settings.join(", ");
      }
      // Собираем список уникальных жанров
      let genres = {};
      for (let k in currentGenreSettings) {
        let v = currentGenreSettings[k];
        let g = (typeof v === "string") ? v : v.genre;
        if (!g) continue;
        if (!(g in genres)) genres[g] = [];
        genres[g].push(k);
      }
      // Определяем обучаемые жанры
      trainableGenresSet = new Set();
      for (let k in currentGenreSettings) {
        let v = currentGenreSettings[k];
        if (typeof v === "object" && v.is_trainable) {
          trainableGenresSet.add(v.genre);
        }
        if (typeof v === "string") {
          trainableGenresSet.add(v);
        }
      }
      // Рендерим кнопки жанров
      let genreListBlock = document.getElementById("genreTrainableList");
      if (genreListBlock) {
        genreListBlock.innerHTML = "";
        Object.keys(genres).forEach(genre => {
          let btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn btn-sm " + (trainableGenresSet.has(genre) ? "btn-primary" : "btn-outline-secondary");
          btn.textContent = genre;
          btn.dataset.genre = genre;
          btn.onclick = function() {
            if (trainableGenresSet.has(genre)) {
              trainableGenresSet.delete(genre);
              btn.classList.remove("btn-primary");
              btn.classList.add("btn-outline-secondary");
            } else {
              trainableGenresSet.add(genre);
              btn.classList.remove("btn-outline-secondary");
              btn.classList.add("btn-primary");
            }
            showGenreFoldersHint();
          };
          genreListBlock.appendChild(btn);
        });
        showGenreFoldersHint();
      }
    });
}

// При сохранении жанров — сохраняем is_trainable по выбранным жанрам
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
      let key = pair[0].trim().toLowerCase();
      let genre = pair[1].trim();
      // Сохраняем is_trainable для всех синонимов этого жанра
      let isTrainable = trainableGenresSet.has(genre);
      newSettings[key] = { genre: genre, is_trainable: isTrainable };
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

function updateScanProgressLegacy() {
  fetch('/scan_progress')
    .then(response => response.json())
    .then(data => {
      var progressBarLoading = document.getElementById('progress-bar-loading');
      var progressBarNormal = document.getElementById('progress-bar-normal');
      var progressBar = document.getElementById('progress-bar');
      var progressText = document.getElementById('progress-text');
      var progressContainer = document.getElementById('progress-container');

      // --- Унифицированная обработка ошибок ---
      if (data.status === "error" && data.error_message) {
        let msg = data.error_message;
        if (/модель жанров не найден|обучите модель/i.test(msg)) {
          showScanErrorModal(msg); // Модель не найдена
        } else if (/MemoryError|Unable to allocate|OutOfMemory|Недостаточно памяти|memory/i.test(msg)) {
          showMemoryErrorModal(msg); // Ошибка памяти
        } else {
          showScanErrorModal(msg); // Прочие ошибки сканирования
        }
        if (progressContainer) progressContainer.style.display = "none";
        return; // Не показываем обычные статусы, если есть ошибка
      }

      if (progressContainer) progressContainer.style.display = "block";

      if (data.total === 0) {
        // Инициализация: показываем анимацию загрузки
        if (progressBarLoading) progressBarLoading.style.display = "";
        if (progressBarNormal) progressBarNormal.style.display = "none";
        if (progressText) progressText.textContent = "Инициализация и подсчет файлов...";
      } else {
        // Обычный прогресс-бар
        let percent = Math.round(100 * data.scanned / data.total);
        if (progressBarLoading) progressBarLoading.style.display = "none";
        if (progressBarNormal) progressBarNormal.style.display = "";
        if (progressText) progressText.textContent = `${percent}% (${data.scanned} из ${data.total})`;
        if (progressBar) {
          progressBar.style.width = percent + "%";
          progressBar.setAttribute("aria-valuenow", percent);
        }
      }

      // Управление автообновлением и статусами
      if (data.status === "in_progress") {
        setTimeout(updateScanProgress, 1000);
      } else if (data.status === "completed") {
        showScanStatusModal("Сканирование завершено!", {scanned: data.scanned, total: data.total,error_count: data.error_count || 0,error_tracks: data.error_tracks || []});
        if (progressContainer) progressContainer.style.display = "none";
      } else if (data.status === "stopped") {
        showScanStatusModal("Сканирование остановлено!", {scanned: data.scanned, total: data.total});
        // Скрываем прогресс-бар
        if (progressContainer) progressContainer.style.display = "none";
      } else if (data.status === "error") {
        // Этот else if нужен для случаев, когда error_message не был передан выше.
        // Например, если статус error, но нет подробного сообщения.
        showScanErrorModal(data.error_message || "Ошибка сканирования.");
        if (progressContainer) progressContainer.style.display = "none";
      }
    })
    .catch(err => {
      console.error("Ошибка получения прогресса сканирования:", err);
      // В случае сбоя сети или ошибки backend тоже скрываем прогресс-бар
      var progressContainer = document.getElementById('progress-container');
      if (progressContainer) progressContainer.style.display = "none";
    });
}

// Модальное окно ошибки при сканировании (например, отсутствует модель)
let scanProgressSample = null;
let scanProgressStatus = null;
let smoothedScanSpeedPerHour = null;
let scanCompletionNotified = false;
let lastScanErrorShown = null;
let deepIndexSnapshot = null;
let scanStopPending = false;
let scanStartPending = false;

function setControlText(button, text) {
  if (button) button.textContent = text;
}

function deriveEffectiveStageStatus({
  serverStatus = '', runtimeStatus = 'idle', running = false, enabled = true,
  pending = 0, processing = 0, failed = 0, completed = 0, total = 0,
  persistentCompleted = false,
} = {}) {
  const accepted = new Set(['disabled', 'in_progress', 'error', 'stopped', 'completed', 'idle']);
  if (accepted.has(String(serverStatus || ''))) return String(serverStatus);
  const runtime = String(runtimeStatus || 'idle').toLowerCase();
  const activeStatuses = new Set(['queued', 'preparing', 'in_progress', 'indexing', 'downloading', 'queued_download']);
  if (!enabled || runtime === 'disabled') return 'disabled';
  if (running || Number(processing || 0) > 0 || activeStatuses.has(runtime)) return 'in_progress';
  if (Number(failed || 0) > 0 || runtime === 'error') return 'error';
  if (Number(pending || 0) > 0) return 'stopped';
  if (
    persistentCompleted || runtime === 'completed'
    || (Number(total || 0) > 0 && Number(completed || 0) >= Number(total || 0))
  ) return 'completed';
  return runtime === 'stopped' ? 'stopped' : 'idle';
}

function deriveEffectiveDeepState(data = {}) {
  const stats = data.stats || {};
  const progress = data.progress || {};
  const total = Number(stats.total || progress.total || 0);
  const completed = Number(stats.completed || progress.processed || 0);
  const pending = Number(stats.pending || 0);
  const failed = Number(stats.errors || progress.errors || 0);
  const enabled = Boolean((data.settings || {}).effnet_enabled);
  const status = deriveEffectiveStageStatus({
    serverStatus: data.effective_status,
    runtimeStatus: progress.status || 'idle',
    running: Boolean(data.running),
    enabled,
    pending,
    processing: data.running ? 1 : 0,
    failed,
    completed,
    total,
  });
  return {
    ...data, stats, progress, total, completed, pending, failed, enabled, status,
    running: status === 'in_progress',
    ready: status === 'completed',
    processed: completed,
  };
}

function deriveEffectiveProcessingState(data) {
  const runtimeMainStatus = String(data.status || 'stopped');
  const savedMain = data.saved_scan_state || {};
  const savedMainCompleted = savedMain.status === 'completed' && Number(savedMain.scan_tracks || 0) > 0;
  const mainStatus = deriveEffectiveStageStatus({
    serverStatus: (data.effective_state || {}).main,
    runtimeStatus: runtimeMainStatus,
    running: runtimeMainStatus === 'in_progress',
    failed: runtimeMainStatus === 'error' ? 1 : 0,
    persistentCompleted: savedMainCompleted,
  });
  const resolvedScanned = Number(data.scanned || 0) || Number(savedMain.scan_tracks || 0);
  const resolvedTotal = mainStatus === 'completed'
    ? resolvedScanned
    : Number(data.total || 0) || Number(savedMain.total || 0) || resolvedScanned;
  const main = {
    ...data,
    status: mainStatus,
    scanned: resolvedScanned,
    total: resolvedTotal,
    persisted: savedMainCompleted,
  };

  const language = {...(data.language_enrichment || {})};
  const pending = Number(language.pending || 0);
  const processing = Number(language.processing || 0);
  const failed = Number(language.failed || 0);
  const completed = Number(language.completed || 0);
  const notNeeded = Number(language.not_needed || 0);
  const queueTotal = pending + processing + failed + completed + notNeeded;
  const runtimeLanguageStatus = String(language.status || 'idle');
  const disabled = runtimeLanguageStatus === 'disabled' || language.enabled === false;
  const queueCompleted = Boolean(queueTotal && !pending && !processing && !failed);
  language.status = deriveEffectiveStageStatus({
    serverStatus: (data.effective_state || {}).language,
    runtimeStatus: runtimeLanguageStatus,
    running: Boolean(language.running),
    enabled: !disabled,
    pending,
    processing,
    failed,
    completed: completed + notNeeded,
    total: queueTotal,
  });
  language.running = language.status === 'in_progress';
  language.processed = completed + notNeeded;
  language.total = queueTotal;
  language.queue_completed = queueCompleted;

  return {main, language, deep: deriveEffectiveDeepState(deepIndexSnapshot || {})};
}

function renderMainIndexControls(data) {
  const start = document.getElementById('main-index-start');
  const stop = document.getElementById('main-index-stop');
  const fullRescan = document.getElementById('full-rescan-start');
  const compact = document.getElementById('main-index-card-status');
  const status = String(data.status || 'stopped');
  const scanned = Number(data.scanned || 0);
  const total = Number(data.total || 0);
  if (scanStopPending && status === 'in_progress') {
    if (start) start.disabled = true;
    if (stop) stop.disabled = true;
    if (fullRescan) fullRescan.disabled = true;
    setControlText(stop, 'Останавливаем…');
    if (compact) compact.textContent = 'Останавливаем после текущей задачи…';
    return;
  }
  scanStopPending = false;
  if (fullRescan) fullRescan.disabled = status === 'in_progress';
  if (stop) {
    stop.disabled = status !== 'in_progress';
    setControlText(stop, 'Приостановить');
  }
  if (start) {
    start.disabled = status === 'in_progress';
    start.dataset.forceContinue = status === 'completed' ? 'true' : 'false';
    setControlText(start, status === 'in_progress' ? 'Индекс выполняется'
      : status === 'completed' ? 'Проверить новые треки'
      : status === 'error' ? 'Повторить / продолжить'
      : 'Продолжить индекс');
  }
  if (!compact) return;
  if (status === 'in_progress') {
    compact.textContent = total
      ? `Выполняется · ${scanned.toLocaleString('ru-RU')} из ${total.toLocaleString('ru-RU')}`
      : 'Выполняется · подсчитываем файлы';
  } else if (status === 'completed') {
    compact.textContent = `Готово · обработано ${scanned.toLocaleString('ru-RU')}`;
  } else if (status === 'error') {
    compact.textContent = 'Ошибка · можно безопасно продолжить';
  } else if (scanned) {
    compact.textContent = `Приостановлено · обработано ${scanned.toLocaleString('ru-RU')}`;
  } else {
    compact.textContent = 'Готов к безопасному запуску';
  }
}

function renderLanguageControls(language) {
  const starts = ['language-index-start', 'language-index-start-compact'].map((id) => document.getElementById(id)).filter(Boolean);
  const stops = ['language-index-stop', 'language-index-stop-compact'].map((id) => document.getElementById(id)).filter(Boolean);
  const retries = ['language-index-retry-compact'].map((id) => document.getElementById(id)).filter(Boolean);
  const compact = document.getElementById('language-card-status');
  const status = String(language.status || 'idle');
  const running = Boolean(language.running) || ['queued', 'preparing', 'in_progress'].includes(status);
  const disabled = status === 'disabled' || language.enabled === false;
  const completed = status === 'completed';
  starts.forEach((start) => {
    start.disabled = running || completed || disabled || status === 'error';
    setControlText(start, running ? 'Уточнение выполняется' : completed ? 'Язык готов' : disabled ? 'Этап отключён' : status === 'error' ? 'Есть ошибки' : 'Продолжить');
  });
  stops.forEach((stop) => { stop.disabled = !running; });
  retries.forEach((retry) => { retry.disabled = running || disabled || Number(language.failed || 0) === 0; });
  if (!compact) return;
  const processed = Number(language.processed || 0);
  const total = Number(language.total || 0);
  compact.textContent = running
    ? `Выполняется · ${processed.toLocaleString('ru-RU')} / ${total.toLocaleString('ru-RU')}`
    : completed ? 'Готово'
    : disabled ? 'Отключено в настройках'
    : status === 'error' ? `Ошибка · не обработано ${Number(language.failed || 0).toLocaleString('ru-RU')}`
    : status === 'stopped' ? `Приостановлено · обработано ${processed.toLocaleString('ru-RU')}`
    : 'Ожидание основного индекса';
}

function updateScanProgress() {
  fetch('/scan_progress?t=' + Date.now(), {cache: 'no-store'})
    .then(response => response.json())
    .then(data => {
      const effective = deriveEffectiveProcessingState(data);
      const mainState = effective.main;
      const container = document.getElementById('progress-container');
      const loading = document.getElementById('progress-bar-loading');
      const normal = document.getElementById('progress-bar-normal');
      const bar = document.getElementById('progress-bar');
      const text = document.getElementById('progress-text');
      const speedText = document.getElementById('scan-speed-text');
      const language = effective.language;
      const mainIndexEngine = data.main_index_engine || 'RF';
      const mainIndexEngineLabel = document.getElementById('main-index-engine');
      const languageBar = document.getElementById('language-progress-bar');
      const languageText = document.getElementById('language-progress-text');
      const languageDetail = document.getElementById('language-progress-detail');
      const summaryText = document.getElementById('scan-summary-text');
      const summaryBadge = document.getElementById('scan-summary-badge');
      const summaryProgress = document.getElementById('scan-summary-progress');

      if (data.status === 'error') {
        const errorMessage = data.error_message || 'Ошибка основного сканирования.';
        if (lastScanErrorShown !== errorMessage) {
          lastScanErrorShown = errorMessage;
          showScanErrorModal(errorMessage);
        }
      } else {
        // Новое сканирование сможет показать новую ошибку, даже если текст совпадёт.
        lastScanErrorShown = null;
      }
      if (mainIndexEngineLabel) mainIndexEngineLabel.textContent = mainIndexEngine;
      if (container) container.style.display = 'block';
      renderMainIndexControls(mainState);
      renderLanguageControls(language);

      const scanned = Number(mainState.scanned || 0);
      const total = Number(mainState.total || 0);
      if (!total && mainState.status === 'in_progress') {
        if (loading) loading.style.display = '';
        if (normal) normal.style.display = 'none';
        if (text) text.textContent = 'Подсчёт файлов…';
      } else if (!total) {
        if (loading) loading.style.display = 'none';
        if (normal) normal.style.display = 'none';
        if (text) text.textContent = 'Ожидание запуска';
      } else {
        const percent = total ? Math.min(100, Math.round(100 * scanned / total)) : 0;
        if (loading) loading.style.display = 'none';
        if (normal) normal.style.display = '';
        if (bar) {
          bar.style.width = percent + '%';
          bar.setAttribute('aria-valuenow', percent);
          bar.classList.toggle('progress-bar-striped', mainState.status === 'in_progress');
          bar.classList.toggle('progress-bar-animated', mainState.status === 'in_progress');
        }
        if (text) text.textContent = `${percent}% (${scanned.toLocaleString('ru-RU')} из ${total.toLocaleString('ru-RU')})`;
      }

      const now = Date.now();
      const scanIsRunning = data.status === 'in_progress';
      const scanStatusChanged = scanProgressStatus !== data.status;
      scanProgressStatus = data.status;

      if (!scanIsRunning) {
        // После паузы/завершения старый снимок нельзя переносить в следующий
        // запуск: в continue счётчик сразу начинается с размера существующей БД.
        scanProgressSample = null;
        smoothedScanSpeedPerHour = null;
        if (speedText) speedText.textContent = '';
      } else if (
        scanStatusChanged ||
        !scanProgressSample ||
        scanned < scanProgressSample.scanned ||
        total !== scanProgressSample.total
      ) {
        scanProgressSample = {scanned, total, time: now};
        smoothedScanSpeedPerHour = null;
        if (speedText) speedText.textContent = 'Измерение скорости…';
      } else if (speedText && now - scanProgressSample.time >= 15000) {
        const elapsedHours = (now - scanProgressSample.time) / 3600000;
        const processedSinceSample = Math.max(0, scanned - scanProgressSample.scanned);
        const intervalSpeed = elapsedHours > 0 ? processedSinceSample / elapsedHours : 0;

        if (processedSinceSample > 0 && intervalSpeed > 0) {
          smoothedScanSpeedPerHour = smoothedScanSpeedPerHour == null
            ? intervalSpeed
            : smoothedScanSpeedPerHour * 0.65 + intervalSpeed * 0.35;
          const remainingHours = Math.max(0, total - scanned) / smoothedScanSpeedPerHour;
          speedText.textContent =
            `Скорость: ${Math.round(smoothedScanSpeedPerHour).toLocaleString('ru-RU')} треков/час` +
            (remainingHours
              ? `. Осталось примерно ${remainingHours < 24 ? Math.ceil(remainingHours) + ' ч' : (remainingHours / 24).toFixed(1) + ' суток'}.`
              : '');
          // Следующий замер использует только новые результаты этого запуска,
          // а сглаживание не даёт показателю резко прыгать каждые 5 секунд.
          scanProgressSample = {scanned, total, time: now};
        }
      }

      const languageTotal = Number(language.total || 0);
      const languageProcessed = Number(language.processed || 0);
      const languagePercent = languageTotal ? Math.min(100, Math.round(100 * languageProcessed / languageTotal)) : 0;
      if (languageBar) {
        languageBar.style.width = languagePercent + '%';
        languageBar.setAttribute('aria-valuenow', languagePercent);
        languageBar.classList.toggle('progress-bar-striped', Boolean(language.running));
        languageBar.classList.toggle('progress-bar-animated', Boolean(language.running));
      }
      if (languageText) {
        const labels = {
          idle: 'Ожидание', queued: 'Запуск…', preparing: 'Подготовка очереди…',
          in_progress: 'Выполняется', completed: 'Готово', stopped: 'Приостановлено',
          disabled: 'Отключено', error: 'Ошибка'
        };
        languageText.textContent = `${labels[language.status] || language.status || 'Ожидание'} · ${languagePercent}%`;
      }
      if (languageDetail) {
        const runtime = language.runtime || {};
        const device = runtime.device ? runtime.device.toUpperCase() : '';
        const fallback = runtime.fallback_to_cpu ? ' (CUDA недоступна, используется CPU)' : '';
        const current = language.current_track ? ` Сейчас: ${language.current_track}` : '';
        languageDetail.textContent = languageTotal
          ? `Обработано ${languageProcessed.toLocaleString('ru-RU')} из ${languageTotal.toLocaleString('ru-RU')}; в очереди ${Number(language.pending || 0).toLocaleString('ru-RU')}; ошибок ${Number(language.failed || 0).toLocaleString('ru-RU')}. ${device}${fallback}.${current}`
          : 'Очередь будет заполнена результатами основного индекса.';
      }

      if (summaryText && summaryBadge && summaryProgress) {
        const mainPercent = total ? Math.min(100, Math.round(100 * scanned / total)) : 0;
        const mainRunning = mainState.status === 'in_progress';
        const languageRunning = Boolean(language.running) || ['queued', 'preparing', 'in_progress'].includes(language.status);
        const deep = deriveEffectiveDeepState(deepIndexSnapshot || {});
        const deepEnabled = deep.enabled;
        const deepRunning = deep.running;
        const deepTotal = deep.total;
        const deepProcessed = deep.processed;
        const deepPercent = deepTotal ? Math.min(100, Math.round(100 * deepProcessed / deepTotal)) : 0;
        const deepReady = !deepEnabled || deep.ready;
        const deepEffectiveStatus = deep.status;
        const hasError = mainState.status === 'error' || language.status === 'error' || deepEffectiveStatus === 'error';
        const isStopped = mainState.status === 'stopped' || language.status === 'stopped' || deepEffectiveStatus === 'stopped';
        const allReady = mainState.status === 'completed' && (!language.enabled || language.status === 'completed') && deepReady;

        summaryBadge.className = 'scan-monitor-badge';
        if (hasError) {
          summaryText.textContent = 'Требуется внимание: один из этапов завершился с ошибкой';
          summaryBadge.textContent = 'Ошибка';
          summaryBadge.classList.add('is-error');
        } else if (mainRunning) {
          summaryText.textContent = total
            ? `${mainIndexEngine}: ${scanned.toLocaleString('ru-RU')} из ${total.toLocaleString('ru-RU')} треков`
            : `${mainIndexEngine}: подсчитываем файлы…`;
          summaryBadge.textContent = total ? `${mainPercent}%` : 'Запуск';
          summaryBadge.classList.add('is-running');
        } else if (languageRunning) {
          summaryText.textContent = languageTotal
            ? `Whisper: ${languageProcessed.toLocaleString('ru-RU')} из ${languageTotal.toLocaleString('ru-RU')} треков`
            : 'Whisper: подготавливаем очередь…';
          summaryBadge.textContent = languageTotal ? `${languagePercent}%` : 'Запуск';
          summaryBadge.classList.add('is-running');
        } else if (deepRunning) {
          summaryText.textContent = deepTotal
            ? `Multi-EffNet: ${deepProcessed.toLocaleString('ru-RU')} из ${deepTotal.toLocaleString('ru-RU')} треков`
            : 'Multi-EffNet: подготавливаем очередь…';
          summaryBadge.textContent = deepTotal ? `${deepPercent}%` : 'Запуск';
          summaryBadge.classList.add('is-running');
        } else if (allReady) {
          summaryText.textContent = 'Все включённые этапы обработки завершены';
          summaryBadge.textContent = 'Готово';
          summaryBadge.classList.add('is-ready');
        } else if (isStopped) {
          summaryText.textContent = 'Обработка приостановлена — её можно продолжить';
          summaryBadge.textContent = 'Пауза';
          summaryBadge.classList.add('is-warning');
        } else if (mainState.status === 'completed') {
          const pendingStages = [];
          if (language.enabled && !['completed', 'disabled'].includes(language.status)) pendingStages.push('Whisper');
          if (deepEnabled && !deepReady) pendingStages.push('Multi-EffNet');
          summaryText.textContent = pendingStages.length
            ? `${mainIndexEngine} готов · ожидают: ${pendingStages.join(', ')}`
            : `${mainIndexEngine} готов`;
          summaryBadge.textContent = pendingStages.length ? 'Ожидание' : 'Готово';
          summaryBadge.classList.add('is-warning');
        } else {
          summaryText.textContent = `${mainIndexEngine} и дополнительные индексы готовы к запуску`;
          summaryBadge.textContent = 'Ожидание';
        }

        const visiblePercent = mainRunning || mainState.status !== 'completed'
          ? mainPercent
          : (languageRunning ? languagePercent : (deepRunning ? deepPercent : Math.max(languagePercent, deepPercent)));
        summaryProgress.style.width = `${visiblePercent}%`;
        summaryProgress.classList.toggle('progress-bar-striped', mainRunning || languageRunning || deepRunning);
        summaryProgress.classList.toggle('progress-bar-animated', mainRunning || languageRunning || deepRunning);
      }

      if (data.status === 'completed' && !scanCompletionNotified) {
        scanCompletionNotified = true;
        showScanStatusModal('Основной индекс готов. Уточнение языка продолжится отдельно.', {
          scanned, total, error_count: data.error_count || 0, error_tracks: data.error_tracks || []
        });
      }
      if (data.status === 'in_progress') scanCompletionNotified = false;

      const shouldPoll = data.status === 'in_progress' || Boolean(language.running)
        || ['queued', 'preparing', 'in_progress'].includes(language.status)
        || Boolean((deepIndexSnapshot || {}).running);
      // На паузе тоже периодически обновляем карточку: настройки RF/YAMNet
      // могут быть изменены в другой вкладке без перезагрузки этой страницы.
      setTimeout(updateScanProgress, shouldPoll ? 1500 : 5000);
    })
    .catch(error => console.error('Ошибка получения двухэтапного прогресса:', error));
}

function startLanguageEnrichment(retryFailed) {
  fetch('/start_language_enrichment', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({retry_failed: Boolean(retryFailed)}),
  })
    .then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const container = document.getElementById('progress-container');
      if (container) container.style.display = 'block';
      updateScanProgress();
    })
    .catch(error => showScanErrorModal('Не удалось запустить уточнение языка: ' + error.message));
}

function stopLanguageEnrichment() {
  fetch('/stop_language_enrichment', {method: 'POST'})
    .then(() => updateScanProgress())
    .catch(error => showScanErrorModal('Не удалось остановить уточнение языка: ' + error.message));
}

function showScanErrorModal(message) {
  // Удаляем старое окно, если есть
  let existing = document.getElementById('scanErrorModal');
  if (existing) existing.remove();

    // Кнопку "Обучить модель" показываем ТОЛЬКО при отсутствии genre_model.pkl,
  // а при ошибке YAMNet показываем ссылку на загрузку модели.
  const showTrainBtn = /модель жанров не найден|обучите модель|genre_model\.pkl/i.test(message);
  const isYamnetMissing = /yamnet|yamnet\.onnx/i.test(message);

  const trainBtnHtml = showTrainBtn
    ? `<button type="button" class="btn btn-success" id="openTrainModalBtn">Обучить модель</button>`
    : "";

  const yamnetHelpHtml = isYamnetMissing
    ? `<div class="mt-2">
         <a href="https://huggingface.co/qualcomm/YamNet/blob/main/YamNet.onnx" target="_blank" rel="noopener">
           Скачать YAMNet.onnx
         </a>
       </div>`
    : "";

  let modalHtml = `
    <div class="modal fade" id="scanErrorModal" tabindex="-1" aria-labelledby="scanErrorModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-danger text-white">
            <h5 class="modal-title" id="scanErrorModalLabel">Ошибка сканирования</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>${message}</p>
            ${yamnetHelpHtml}
            ${trainBtnHtml}
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  let modalEl = document.getElementById('scanErrorModal');
  let modalInstance = new bootstrap.Modal(modalEl);
  modalInstance.show();

  // Обработка кнопки "Обучить модель" (безопасно, если кнопки нет)
  const trainBtn = document.getElementById("openTrainModalBtn");
  if (trainBtn) {
    trainBtn.onclick = function() {
      modalInstance.hide();
      // Открываем модальное окно для обучения модели
      let modelParamsModal = document.getElementById('modelParamsModal');
      if (modelParamsModal) {
        let trainModalInstance = new bootstrap.Modal(modelParamsModal);
        trainModalInstance.show();
      }
    };
  }
  // Удаляем модалку после закрытия
  modalEl.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => {
      modalEl.remove();
    }, 200);
  });
}

async function launchLibraryScan(forceNew = false) {
  if (scanStartPending) return;
  scanStartPending = true;
  const startButton = document.getElementById('main-index-start');
  const fullRescanButton = document.getElementById('full-rescan-start');
  if (startButton) {
    startButton.disabled = true;
    startButton.textContent = 'Запускаем…';
  }
  if (fullRescanButton) fullRescanButton.disabled = true;
  try {
    const forceContinue = !forceNew;
    const scanMode = forceNew ? 'new' : 'continue';
    const response = await fetch('/start_scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        confirm_reset: forceNew,
        force_continue: forceContinue,
        force_new: forceNew,
      }),
    });
    const data = await response.json();
    if (!response.ok || data.error || data.status === "error" || data.error_message) {
      const msg = data.error || data.error_message || `Ошибка запуска сканирования (HTTP ${response.status})`;
      if (/модель жанров не найден|обучите модель/i.test(msg)) {
        showScanErrorModal(msg);
      } else if (/MemoryError|Недостаточно памяти|Unable to allocate|OutOfMemory|memory/i.test(msg)) {
        showMemoryErrorModal(msg);
      } else {
        showScanErrorModal(msg);
      }
      const progressContainer = document.getElementById("progress-container");
      if (progressContainer) progressContainer.style.display = "none";
      scanStartPending = false;
      if (fullRescanButton) fullRescanButton.disabled = false;
      updateScanProgress();
      return;
    }

    if (data.status === 'already scanning') {
      const progressContainer = document.getElementById('progress-container');
      if (progressContainer) progressContainer.style.display = 'block';
      renderMainIndexControls({status: 'in_progress'});
      scanStartPending = false;
      if (fullRescanButton) fullRescanButton.disabled = true;
      updateScanProgress();
      return;
    }

    const modeLabel = scanMode === 'new' ? 'Новый' : 'Продолжить';
    showScanStatusModal('Сканирование запущено! Режим: <b>' + modeLabel + '</b>');
    const progressContainer = document.getElementById("progress-container");
    if (progressContainer) progressContainer.style.display = "block";
    scanStartPending = false;
    if (fullRescanButton) fullRescanButton.disabled = true;
    updateScanProgress();
  } catch (error) {
    scanStartPending = false;
    if (fullRescanButton) fullRescanButton.disabled = false;
    updateScanProgress();
    showScanStatusModal('Ошибка запуска сканирования: ' + error);
  }
}

// Обычный запуск всегда безопасно дополняет существующий индекс.
async function startScan() {
  return launchLibraryScan(false);
}

// Полный reset — отдельная разовая команда, которая никогда не берётся из config.json.
async function startFullRescan() {
  if (scanStartPending) return;
  const confirmed = window.confirm(
    'Полное пересканирование создаст резервную копию текущей непустой базы, затем удалит основной и связанные производные индексы из scan_results.db и начнёт анализ всей библиотеки заново. Модель и музыкальные файлы не удаляются. Продолжить?'
  );
  if (!confirmed) return;
  const phrase = window.prompt('Для подтверждения полного сброса введите слово СБРОС');
  if (String(phrase || '').trim().toUpperCase() !== 'СБРОС') {
    showScanStatusModal('Полное пересканирование отменено: контрольное слово не совпало.');
    return;
  }
  const modalElement = document.getElementById('scanSettingsModal');
  bootstrap.Modal.getInstance(modalElement)?.hide();
  return launchLibraryScan(true);
}

// Функция Стоп сканирование библиотеки
function stopScan() {
    scanStopPending = true;
    renderMainIndexControls({status: 'in_progress'});
    fetch('/stop_scan')
        .then(response => response.json())
        .then(data => { // Просто отправили запрос на остановку, дальше updateScanProgress сам все обработает
        })
        .catch(error => {
            scanStopPending = false;
            updateScanProgress();
            showScanStatusModal('Ошибка остановки сканирования: ' + error);
        });
}

// --- Модальное окно статуса сканирования ---
function showScanStatusModal(message, stats) {
  // message: основной текст
  // stats: объект вида {scanned: 123, total: 456} или undefined
  let modalId = "scanStatusModal";
  let existing = document.getElementById(modalId);
  if (existing) existing.remove();
  let host = window.location.host;
  let statsHtml = "";
  if (stats && typeof stats.scanned === "number" && typeof stats.total === "number") {
    statsHtml = `<div class="mt-2">Отсканировано: <b>${stats.scanned}</b> из <b>${stats.total}</b> файлов</div>`;
    if (typeof stats.error_count === "number") {
      statsHtml += `<div class="mt-2 text-danger">Ошибочных файлов: <b>${stats.error_count}</b></div>`;
      if (stats.error_tracks && stats.error_tracks.length > 0) {
        statsHtml += `<details class="mt-2"><summary>Список ошибок (первые 10)</summary><ul style="max-height:120px;overflow:auto">`
          + stats.error_tracks.slice(0,10).map(f => `<li>${f}</li>`).join('')
          + `</ul></details>`;
      }
    }
  }
  // Показываем кнопку "Обучить модель" ТОЛЬКО если отсутствует genre_model.pkl
  // Признаки: в сообщении есть "модель жанров не найден", "обучите модель" или "genre_model.pkl"
  const showTrainBtn = /модель жанров не найден|обучите модель|genre_model\.pkl/i.test(message);
  const trainBtnHtml = showTrainBtn ? `<button type="button" class="btn btn-success" id="openTrainModalBtn">Обучить модель</button>` : "";

  let modalHtml = `
    <div class="modal fade" id="scanStatusModal" tabindex="-1" aria-labelledby="scanStatusModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-primary text-white">
            <h5 class="modal-title" id="scanStatusModalLabel">Статус сканирования</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>${message}</p>
            ${statsHtml}
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  let modalEl = document.getElementById(modalId); // modalId уже = "scanStatusModal" выше
  let modalInstance = new bootstrap.Modal(modalEl);
  modalInstance.show();
  modalEl.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => { modalEl.remove(); }, 200);
  });
}

// Модальное окно ошибки памяти
function showMemoryErrorModal(message) {
  // Установить текст ошибки
  var errorTextEl = document.getElementById('memoryErrorText');
  if (errorTextEl) errorTextEl.textContent = message;

  // Показать модалку
  var modalEl = document.getElementById('memoryErrorModal');
  if (modalEl) {
    var modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    modalInstance.show();
    // После закрытия очищаем текст
    modalEl.addEventListener('hidden.bs.modal', function () {
      errorTextEl.textContent = '';
    }, { once: true });
  }
}

// Этап 4: обучение отдельной модели личного рейтинга.
(() => {
  const statusNode = document.getElementById("personalization-status");
  const metricsNode = document.getElementById("personalization-metrics");
  const trainButton = document.getElementById("train-personalization");
  const progressWrap = document.getElementById("personalization-progress-wrap");
  const progressBar = document.getElementById("personalization-progress");
  if (!statusNode || !trainButton) return;

  let timer = null;
  const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`;

  async function loadPersonalizationStatus() {
    const response = await fetch("/api/personalization/status");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    const progress = data.progress || {};
    const total = Number(progress.total || 0);
    const processed = Number(progress.processed || 0);
    const ratio = total ? Math.min(1, processed / total) : 0;
    trainButton.disabled = Boolean(data.running);
    progressWrap.classList.toggle("d-none", !data.running);
    progressBar.style.width = percent(ratio);
    progressBar.textContent = percent(ratio);

    if (data.running) {
      statusNode.textContent = `Обучение: ${progress.status || "подготовка"}. Обработано ${processed.toLocaleString("ru-RU")} из ${total.toLocaleString("ru-RU")}.`;
      if (!timer) timer = setInterval(() => loadPersonalizationStatus().catch(showError), 1500);
    } else {
      if (timer) { clearInterval(timer); timer = null; }
      const state = data.state || {};
      statusNode.textContent = data.model_exists
        ? `Модель готова: реальных оценок ${Number(data.rated_tracks || 0).toLocaleString("ru-RU")}, прогнозов ${Number(data.predicted_tracks || 0).toLocaleString("ru-RU")}.`
        : "Модель ещё не обучена.";
      const metrics = state.metrics || {};
      metricsNode.textContent = state.status === "error"
        ? `Последняя ошибка: ${state.error || "неизвестно"}`
        : (metrics.mae_stars != null
          ? `Проверка: MAE ${Number(metrics.mae_stars).toFixed(2)}★; точность 4–5★ ${percent(metrics.high_rating_precision)}; recall ${percent(metrics.high_rating_recall)}.`
          : "");
    }
  }

  function showError(error) {
    statusNode.textContent = `Ошибка: ${error.message}`;
    statusNode.classList.add("text-danger");
  }

  trainButton.addEventListener("click", async () => {
    try {
      statusNode.classList.remove("text-danger");
      const response = await fetch("/api/personalization/train/start", {method: "POST"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      await loadPersonalizationStatus();
    } catch (error) { showError(error); }
  });
  loadPersonalizationStatus().catch(showError);
})();

// Сводная готовность коллекции и одно рекомендуемое следующее действие.
(() => {
  const root = document.getElementById("collection-health");
  if (!root) return;
  let snapshot = null;
  let refreshTimer = null;

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  function stageLabel(status) {
    return ({
      ready: "Готово", running: "В работе", pending: "Ожидает",
      check: "Проверить", disabled: "Отключено", error: "Ошибка",
    })[status] || status || "Ожидает";
  }

  function renderHealth(data) {
    snapshot = data;
    const readiness = Math.round(Number(data.readiness || 0) * 100);
    const gauge = document.getElementById("collection-health-gauge");
    gauge?.style.setProperty("--health-value", `${readiness * 3.6}deg`);
    gauge?.setAttribute("aria-valuenow", String(readiness));
    const percent = document.getElementById("collection-health-percent");
    if (percent) percent.textContent = `${readiness}%`;

    const badge = document.getElementById("collection-health-badge");
    const labels = {ready: "Готово", running: "Выполняется", attention: "Нужен следующий шаг", error: "Есть ошибка"};
    const badgeClasses = {ready: "text-bg-success", running: "text-bg-info", attention: "text-bg-warning", error: "text-bg-danger"};
    if (badge) {
      badge.className = `badge ${badgeClasses[data.status] || "text-bg-secondary"}`;
      badge.textContent = labels[data.status] || "Проверено";
    }
    const summary = document.getElementById("collection-health-summary");
    if (summary) summary.textContent = data.status === "ready"
      ? "Коллекция готова к точному подбору и персональным рекомендациям."
      : `В основном индексе ${Number(data.scan_tracks || 0).toLocaleString("ru-RU")} треков. Интерфейс подскажет безопасный порядок действий.`;

    const stages = document.getElementById("collection-health-stages");
    if (stages) stages.innerHTML = (data.stages || []).map((stage) => {
      const value = Math.round(Number(stage.coverage || 0) * 100);
      return `<article class="health-stage" data-status="${escapeHtml(stage.status)}" title="${escapeHtml(stage.detail)}">
        <div class="health-stage-head"><span class="health-stage-title">${escapeHtml(stage.title)}</span><span class="health-stage-value">${stage.status === "disabled" ? "—" : `${value}%`}</span></div>
        <div class="health-stage-bar"><span style="width:${stage.status === "disabled" ? 100 : value}%"></span></div>
        <div class="health-stage-detail">${escapeHtml(stageLabel(stage.status))} · ${escapeHtml(stage.detail)}</div>
      </article>`;
    }).join("");

    const action = data.next_action || {};
    const title = document.getElementById("collection-next-title");
    const detail = document.getElementById("collection-next-detail");
    const button = document.getElementById("collection-next-action");
    if (title) title.textContent = "Рекомендуемый следующий шаг";
    if (detail) detail.textContent = action.detail || "";
    if (button) {
      button.textContent = action.label || "Продолжить";
      button.dataset.action = action.id || "";
      button.disabled = !action.id;
    }
  }

  async function loadHealth() {
    const response = await fetch(`/api/collection-health?t=${Date.now()}`, {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    renderHealth(data);
    clearTimeout(refreshTimer);
    const running = data.status === "running";
    refreshTimer = setTimeout(() => loadHealth().catch(console.error), running ? 1600 : 6000);
  }

  async function runHealthAction(action) {
    if (action === "continue_main") return window.startScan?.();
    if (action === "monitor") {
      document.getElementById("settings-library")?.scrollIntoView({behavior: "smooth", block: "start"});
      bootstrap.Collapse.getOrCreateInstance(document.getElementById("scan-progress-details"), {toggle: false}).show();
      return;
    }
    if (action === "sync_catalog") {
      const response = await fetch("/api/intelligence/sync/start", {method: "POST"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    } else if (action === "continue_language") {
      return window.startLanguageEnrichment?.(false);
    } else if (action === "retry_language") {
      return window.startLanguageEnrichment?.(true);
    } else if (action === "continue_deep") {
      document.getElementById("deep-index-start-compact")?.click();
      return;
    } else if (action === "retry_deep") {
      document.getElementById("deep-index-retry-compact")?.click();
      return;
    } else if (action === "open_ratings") {
      window.location.href = "/?open=favorites";
      return;
    } else if (action === "train_personal") {
      document.getElementById("train-personalization")?.click();
      return;
    } else if (action === "open_catalog") {
      window.location.href = "/intelligence";
      return;
    }
    await loadHealth();
  }

  document.getElementById("collection-next-action")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await runHealthAction(button.dataset.action);
      setTimeout(() => loadHealth().catch(console.error), 500);
    } catch (error) {
      if (typeof showScanErrorModal === "function") showScanErrorModal(error.message);
      else console.error(error);
    } finally {
      button.disabled = false;
    }
  });
  loadHealth().catch(console.error);
})();

// Универсальный центр моделей: независимые этапы, источники вкуса и runtime.
(() => {
  const byId = (id) => document.getElementById(id);
  const modal = byId("modelCenterModal");
  if (!modal) return;

  const controls = {
    yamnetEnabled: byId("pipeline-yamnet-enabled"),
    yamnetCuda: byId("pipeline-yamnet-cuda"),
    whisperEnabled: byId("pipeline-whisper-enabled"),
    whisperDevice: byId("pipeline-whisper-device"),
    effnetEnabled: byId("pipeline-effnet-enabled"),
    effnetDevice: byId("pipeline-effnet-device"),
    effnetWorkers: byId("pipeline-effnet-workers"),
    effnetOffsets: byId("pipeline-effnet-offsets"),
    effnetGenreFusion: byId("pipeline-effnet-genre-fusion"),
    rekordboxEnabled: byId("pipeline-rekordbox-enabled"),
    playerRatings: byId("pipeline-player-ratings"),
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function pipelinePayload() {
    return {
      yamnet_enabled: controls.yamnetEnabled.checked,
      yamnet_use_cuda: controls.yamnetCuda.checked,
      vocal_language_enabled: controls.whisperEnabled.checked,
      vocal_language_device: controls.whisperDevice.value,
      effnet_enabled: controls.effnetEnabled.checked,
      effnet_device: controls.effnetDevice.value,
      effnet_preprocess_workers: Number(controls.effnetWorkers.value || 0),
      effnet_segment_offsets: controls.effnetOffsets.value,
      effnet_genre_fusion_enabled: controls.effnetGenreFusion.checked,
      rekordbox_enabled: controls.rekordboxEnabled.checked,
      player_ratings_enabled: controls.playerRatings.checked,
    };
  }

  function renderRuntime(data) {
    const grid = byId("pipeline-engine-grid");
    if (!grid) return;
    grid.innerHTML = (data.engines || []).map((engine) => `
      <article class="runtime-card">
        <div class="runtime-card-title">${engine.title}</div>
        <div class="runtime-card-device">${engine.device}</div>
        <div class="small text-muted">${engine.optional ? "Можно отключить" : "Базовый этап"}</div>
      </article>
    `).join("");
  }

  function applyPipeline(data) {
    const pipeline = data.pipeline || {};
    const analysis = data.analysis || {};
    controls.yamnetEnabled.checked = Boolean(analysis.yamnet_enabled);
    controls.yamnetCuda.checked = Boolean(analysis.yamnet_use_cuda);
    controls.whisperEnabled.checked = Boolean(analysis.vocal_language_enabled);
    controls.whisperDevice.value = analysis.vocal_language_device || "auto";
    controls.effnetEnabled.checked = Boolean(pipeline.effnet_enabled);
    controls.effnetDevice.value = pipeline.effnet_device || "auto";
    controls.effnetWorkers.value = String(Number(pipeline.effnet_preprocess_workers || 0));
    controls.effnetOffsets.value = (pipeline.effnet_segment_offsets || [30, 60, 90]).join(", ");
    controls.effnetGenreFusion.checked = pipeline.effnet_genre_fusion_enabled !== false;
    controls.rekordboxEnabled.checked = Boolean(pipeline.rekordbox_enabled);
    controls.playerRatings.checked = pipeline.player_ratings_enabled !== false;
    renderRuntime(data);
  }

  async function loadPipeline() {
    const data = await api("/api/model-pipeline/settings?t=" + Date.now());
    applyPipeline(data);
    return data;
  }

  async function savePipeline(showMessage = true) {
    const status = byId("pipeline-save-status");
    if (status) status.textContent = "Сохраняем…";
    const data = await api("/api/model-pipeline/settings", {
      method: "POST",
      body: JSON.stringify(pipelinePayload()),
    });
    applyPipeline(data);
    if (status && showMessage) status.textContent = "Настройки применены";
    return data;
  }

  function etaText(seconds) {
    const value = Number(seconds || 0);
    if (!value) return "";
    if (value < 3600) return `${Math.ceil(value / 60)} мин`;
    if (value < 86400) return `${(value / 3600).toFixed(1)} ч`;
    return `${(value / 86400).toFixed(1)} суток`;
  }

  function renderDeepStatus(data) {
    deepIndexSnapshot = data;
    const deep = deriveEffectiveDeepState(data);
    const stats = deep.stats;
    const progress = deep.progress;
    const runtime = data.runtime || {};
    const total = deep.total;
    const completed = deep.completed;
    const pending = deep.pending;
    const deepEnabled = deep.enabled;
    const percent = total ? Math.min(100, Math.round(completed * 100 / total)) : 0;
    const deepReady = deep.ready;
    const effectiveStatus = deep.status;
    const statusLabels = {
      idle: "Ожидание", queued: "Запуск…", preparing: "Подготовка…",
      in_progress: "Выполняется", indexing: "Выполняется", completed: "Готово", stopped: "Приостановлено",
      disabled: "Отключено", error: "Ошибка", downloading: "Загрузка модели", queued_download: "Запуск загрузки…",
    };
    const text = byId("deep-progress-text");
    const bar = byId("deep-progress-bar");
    const detail = byId("deep-progress-detail");
    if (text) text.textContent = `${statusLabels[effectiveStatus] || effectiveStatus || "Ожидание"} · ${percent}%`;
    if (bar) {
      bar.style.width = `${percent}%`;
      bar.setAttribute("aria-valuenow", percent);
      bar.classList.toggle("progress-bar-striped", deep.running);
      bar.classList.toggle("progress-bar-animated", deep.running);
      bar.classList.toggle("bg-danger", effectiveStatus === "error");
    }
    if (detail) {
      const provider = progress.provider || (runtime.provider_plan || ["CPUExecutionProvider"])[0];
      const speed = Number(progress.tracks_per_hour || 0);
      const speedPart = speed ? ` · ${Math.round(speed).toLocaleString("ru-RU")} треков/час` : "";
      const eta = etaText(progress.eta_seconds);
      detail.textContent = effectiveStatus === "error"
        ? `Ошибка: ${progress.error || "неизвестно"}`
        : `Готово ${completed.toLocaleString("ru-RU")} из ${total.toLocaleString("ru-RU")}; ошибок ${Number(stats.errors || 0).toLocaleString("ru-RU")} · ${provider}${speedPart}${eta ? ` · осталось ${eta}` : ""}`;
    }
    const modelStatus = byId("pipeline-effnet-model-status");
    if (modelStatus) {
      modelStatus.textContent = runtime.model_exists
        ? `Модель готова · ${(runtime.provider_plan || ["CPUExecutionProvider"])[0]}${runtime.fallback_to_cpu ? " · CUDA недоступна, будет CPU" : ""}`
        : "Модель не загружена. Её можно скачать отдельно, остальные функции продолжат работать.";
    }
    const download = byId("deep-model-download");
    if (download) download.classList.toggle("d-none", Boolean(runtime.model_exists));
    const startLabel = !deepEnabled ? "Этап отключён" : deep.running ? "Индекс выполняется"
      : deepReady ? "Индекс готов" : effectiveStatus === "error" ? "Есть ошибки" : "Продолжить индекс";
    ["deep-index-start", "deep-index-start-compact"].map(byId).filter(Boolean).forEach((button) => {
      button.disabled = !deepEnabled || deep.running || deepReady || effectiveStatus === "error";
      button.textContent = startLabel;
    });
    ["deep-index-retry", "deep-index-retry-compact"].map(byId).filter(Boolean)
      .forEach((button) => { button.disabled = !deepEnabled || deep.running || deep.failed === 0; });
    ["deep-index-stop", "deep-index-stop-compact"].map(byId).filter(Boolean)
      .forEach((button) => { button.disabled = !deep.running; });
    const compact = byId("deep-index-card-status");
    if (compact) {
      compact.textContent = !deepEnabled ? "Отключено в настройках" : deep.running
        ? `Выполняется · ${completed.toLocaleString("ru-RU")} / ${total.toLocaleString("ru-RU")}`
        : deepReady ? "Готово"
        : effectiveStatus === "stopped" ? `Приостановлено · обработано ${completed.toLocaleString("ru-RU")}`
        : effectiveStatus === "error" ? "Ошибка · доступен повтор ошибок"
        : `Ожидание · осталось ${pending.toLocaleString("ru-RU")}`;
    }
  }

  async function loadDeepStatus() {
    const data = await api("/api/deep-index/status?t=" + Date.now());
    renderDeepStatus(data);
    setTimeout(() => loadDeepStatus().catch(console.error), data.running ? 1500 : 5000);
  }

  async function startDeep(retryFailed = false) {
    try {
      await savePipeline(false);
      await api("/api/deep-index/start", {
        method: "POST",
        body: JSON.stringify({retry_failed: retryFailed}),
      });
      await loadDeepStatusOnce();
    } catch (error) {
      showScanErrorModal(`Не удалось запустить Multi-EffNet: ${error.message}`);
    }
  }

  async function loadDeepStatusOnce() {
    const data = await api("/api/deep-index/status?t=" + Date.now());
    renderDeepStatus(data);
  }

  function bind(id, handler) {
    const node = byId(id);
    if (node) node.addEventListener("click", handler);
  }

  bind("pipeline-save", () => savePipeline().catch((error) => {
    const status = byId("pipeline-save-status");
    if (status) status.textContent = `Ошибка: ${error.message}`;
  }));
  bind("deep-index-start", () => startDeep(false));
  bind("deep-index-start-compact", () => startDeep(false));
  bind("deep-index-retry", () => startDeep(true));
  bind("deep-index-retry-compact", () => startDeep(true));
  bind("deep-index-stop", () => api("/api/deep-index/stop", {method: "POST"}).then(loadDeepStatusOnce).catch(console.error));
  bind("deep-index-stop-compact", () => api("/api/deep-index/stop", {method: "POST"}).then(loadDeepStatusOnce).catch(console.error));
  bind("deep-model-download", () => {
    const accepted = window.confirm(
      "Discogs Multi-EffNet загружается напрямую с сайта Essentia. " +
      "Модель предназначена для некоммерческого использования по CC BY-NC-SA 4.0. " +
      "Продолжая, вы подтверждаете, что ознакомились с условиями лицензии."
    );
    if (!accepted) return;
    api("/api/deep-index/model/download", {
      method: "POST",
      body: JSON.stringify({accept_license: true}),
    }).then(loadDeepStatusOnce).catch((error) => showScanErrorModal(error.message));
  });
  bind("train-personalization-center", () => byId("train-personalization")?.click());
  modal.addEventListener("show.bs.modal", () => loadPipeline().catch(console.error));
  loadPipeline().catch(console.error);
  loadDeepStatus().catch(console.error);
})();

// Конструктор обучающей выборки: источники, предварительная разметка и review.
(() => {
  const modal = document.getElementById("trainingDatasetModal");
  if (!modal) return;

  const state = {
    summary: null, labels: null, plan: null, offset: 0, limit: 50, total: 0,
    selected: new Set(), pollTimer: null, trainingTimer: null, trainingClockTimer: null,
    quickQualityTimer: null,
    planRequest: null, planController: null,
    lastQuickQuality: null,
    trainingWasRunning: false, lastTrainingStatus: null,
    problemItems: [], problemFilter: "all", problemSort: "risk",
    problemStyle: "all", problemConfusedWith: "all",
    folderStyle: "all", folderTrackRange: "all",
    folderSort: "path", folderSortDirection: "asc", folderSortExplicit: false,
    reviewMode: "folders", disputedItems: [], disputedOffset: 0,
    disputedLimit: 30, disputedTotal: 0, disputedFolderId: "",
    disputedStyle: "all", disputedConfusedWith: "all", disputedStatus: "all",
    disputedSelected: new Set(), disputedFilteredSelection: false,
    previewTrackId: null, previewNeedsInitialSeek: false,
    previewPlaying: false,
    previewSeekActive: false, previewSeekDragging: false,
    previewSeekWasMuted: false, previewSeekRestoreTimer: null,
    reviewPreview: {mode: "percent", percent: 30, seconds: 60},
    editingTaxonomy: false,
    assistantPreview: null, assistantConfirmed: false,
  };
  const el = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const number = (value) => Number(value || 0).toLocaleString("ru-RU");

  function parseTrainingTime(value) {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatTrainingTime(value) {
    const parsed = parseTrainingTime(value);
    if (!parsed) return "";
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(parsed);
  }

  function formatTrainingDuration(milliseconds) {
    const totalSeconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const parts = [];
    if (hours) parts.push(`${hours} ч`);
    if (hours || minutes) parts.push(`${minutes} мин`);
    parts.push(`${seconds} сек`);
    return parts.join(" ");
  }

  function renderTrainingTiming(data = {}, active = false) {
    window.clearTimeout(state.trainingClockTimer);
    const node = el("training-rf-progress-timing");
    if (!node) return;
    const detail = data.detail || {};
    const startedAt = parseTrainingTime(data.started_at || detail.started_at);
    const finishedAt = parseTrainingTime(data.finished_at || detail.finished_at);
    if (!startedAt) {
      node.textContent = "";
      node.classList.add("d-none");
      return;
    }
    const endTime = finishedAt || new Date();
    const parts = [
      `Начало: ${formatTrainingTime(startedAt)}`,
      `${finishedAt ? "Длительность" : "Прошло"}: ${formatTrainingDuration(endTime - startedAt)}`,
    ];
    if (finishedAt) parts.push(`Окончание: ${formatTrainingTime(finishedAt)}`);
    node.textContent = parts.join(" · ");
    node.classList.remove("d-none");
    if (active) {
      state.trainingClockTimer = window.setTimeout(() => {
        renderTrainingTiming(state.lastTrainingStatus || data, true);
      }, 1000);
    }
  }

  function renderTrainingPreflight(preflight = {}) {
    const node = el("training-rf-preflight");
    if (!node) return;
    const rows = preflight.rows || [];
    if (!rows.length) {
      node.classList.add("d-none");
      node.innerHTML = "";
      return;
    }
    const tableRows = rows.filter((row) => row.effective || row.preview_selected).map((row) => `<tr>
      <td>${escapeHtml(row.style)}</td>
      <td>${number(row.dataset_builder)}</td>
      <td>${number(row.samples)}</td>
      <td>${number(row.rekordbox)}</td>
      <td>${number(row.combined)}</td>
      <td><strong>${number(row.after_cap)}</strong></td>
    </tr>`).join("");
    const issues = (preflight.issues || []).map((issue) => `<li>${escapeHtml(issue)}</li>`).join("");
    node.classList.remove("d-none");
    node.innerHTML = `<details ${preflight.passed ? "" : "open"}>
      <summary class="small ${preflight.passed ? "text-success" : "text-danger"}">
        Preflight выборки: ${preflight.passed ? "согласован" : `обнаружено проблем: ${number((preflight.issues || []).length)}`}
      </summary>
      ${issues ? `<ul class="small text-danger mt-2 mb-2">${issues}</ul>` : ""}
      <div class="table-responsive"><table class="table table-sm mb-0"><thead><tr><th>Стиль</th><th>Builder</th><th>Samples</th><th>Rekordbox</th><th>Всего</th><th>После cap</th></tr></thead><tbody>${tableRows}</tbody></table></div>
    </details>`;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function alertMessage(message, type = "info") {
    const box = el("training-dataset-alert");
    box.className = `alert alert-${type}`;
    box.textContent = message;
    box.classList.remove("d-none");
    window.setTimeout(() => box.classList.add("d-none"), 6000);
  }

  const trainingPhaseLabels = {
    idle: "Ожидание",
    preparing: "Подготовка выборки",
    features: "Извлечение аудиопризнаков",
    memory_pause: "Ожидание свободной памяти",
    preparing_model: "Подготовка модели",
    tuning: "Автоподбор параметров RF",
    validation: "Калибровка и validation",
    language: "Проверка языка",
    quality_gate: "Проверка качества",
    final_model: "Финальное обучение",
    saving: "Сохранение модели",
    stopping: "Остановка",
    stopped: "Остановлено",
    rejected: "Кандидат отклонён",
    completed: "Готово",
    error: "Ошибка",
  };

  function renderTrainingStatus(data = {}) {
    const detail = data.detail || {};
    const progress = Math.max(0, Math.min(100, Number(data.progress ?? detail.progress ?? 0) || 0));
    const running = Boolean(data.running);
    const error = data.error || "";
    const status = data.status || detail.status || (running ? "running" : error ? "error" : progress >= 100 ? "completed" : "idle");
    const phase = data.phase || detail.phase || (running ? "preparing" : status);
    const message = data.message || detail.message || (running
      ? (progress ? "Обучение выполняется" : "Подготовка обучающей выборки")
      : error || "Обучение ещё не запускалось");
    const processed = Number(data.processed ?? detail.processed ?? 0) || 0;
    const total = Number(data.total ?? detail.total ?? 0) || 0;
    const stopping = status === "stopping";
    const active = running || stopping;
    const supportsStop = Boolean(data.detail && typeof data.detail === "object");
    const hasRun = active || progress > 0 || ["completed", "stopped", "rejected", "error"].includes(status);
    const indeterminate = active && progress <= 0;

    state.lastTrainingStatus = data;
    const card = el("training-rf-progress-card");
    if (card) card.classList.toggle("d-none", !hasRun);
    const title = el("training-rf-progress-title");
    if (title) title.textContent = `Обучение модели · ${trainingPhaseLabels[phase] || phase}`;
    const messageNode = el("training-rf-progress-message");
    if (messageNode) {
      messageNode.textContent = error && !active ? `Ошибка: ${error}` : message;
      messageNode.classList.toggle("text-danger", Boolean(error && !active));
      messageNode.classList.toggle("text-muted", !error || active);
    }
    const percentNode = el("training-rf-progress-percent");
    if (percentNode) {
      percentNode.textContent = indeterminate ? "Подготовка" : `${Math.round(progress)}%`;
      percentNode.className = `badge ${error && !active ? "text-bg-danger" : status === "stopped" ? "text-bg-secondary" : status === "completed" ? "text-bg-success" : "text-bg-primary"}`;
    }
    const bar = el("training-rf-progress-bar");
    if (bar) {
      bar.style.width = indeterminate ? "100%" : `${progress}%`;
      bar.setAttribute("aria-valuenow", String(progress));
      bar.classList.toggle("progress-bar-striped", active);
      bar.classList.toggle("progress-bar-animated", active);
      bar.classList.toggle("bg-danger", Boolean(error && !active));
      bar.classList.toggle("bg-success", status === "completed");
      bar.classList.toggle("bg-secondary", status === "stopped");
    }
    const details = [];
    if (total > 0) details.push(`Обработано ${number(processed)} из ${number(total)} треков`);
    if (phase === "error" && (data.preflight?.rows || []).length) {
      details.push("Фактический preflight остановил запуск до extraction; подробности записаны в лог");
    }
    if (stopping) details.push("Завершается текущая безопасная операция");
    const catalog = data.catalog_refresh || {};
    if (!active && catalog.status === "running") details.push(`Обновление каталога: ${number(catalog.processed)} из ${number(catalog.total)}`);
    const detailNode = el("training-rf-progress-detail");
    if (detailNode) detailNode.textContent = details.join(" · ") || (active ? "Статус обновляется автоматически" : "");
    renderTrainingTiming(data, active);
    renderTrainingPreflight(data.preflight || {});

    const stopButton = el("training-rf-stop");
    if (stopButton) {
      stopButton.disabled = !running || stopping || !supportsStop;
      stopButton.title = !supportsStop && running
        ? "Безопасная остановка станет доступна после перезапуска приложения"
        : "";
      stopButton.innerHTML = stopping
        ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Останавливается'
        : '<i class="bi bi-stop-circle me-1"></i>Остановить';
    }
    const retrainButton = el("training-dataset-retrain");
    if (retrainButton) retrainButton.disabled = active;

    const pageStatus = el("training-page-status");
    if (pageStatus) {
      pageStatus.classList.toggle("d-none", !active);
      pageStatus.textContent = active
        ? `RF: ${trainingPhaseLabels[phase] || phase}${indeterminate ? "" : ` · ${Math.round(progress)}%`}. ${message}`
        : "";
    }
    const pageBadge = el("training-page-badge");
    if (pageBadge) {
      pageBadge.classList.toggle("d-none", !active);
      pageBadge.textContent = indeterminate ? "…" : `${Math.round(progress)}%`;
    }

    return {active, running, stopping, status, progress};
  }

  async function pollTrainingStatus() {
    window.clearTimeout(state.trainingTimer);
    try {
      const data = await requestJson("/training_status?t=" + Date.now());
      const snapshot = renderTrainingStatus(data);
      if (snapshot.active) {
        state.trainingWasRunning = true;
        state.trainingTimer = window.setTimeout(pollTrainingStatus, 3000);
      } else if (state.trainingWasRunning) {
        state.trainingWasRunning = false;
        if (modal.classList.contains("show")) await loadPlan();
      }
    } catch (error) {
      console.error("Не удалось получить статус обучения:", error);
      if (state.trainingWasRunning) state.trainingTimer = window.setTimeout(pollTrainingStatus, 6000);
    }
  }

  function fillSelect(target, values, placeholder) {
    if (!target || target.dataset.ready === "1") return;
    target.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` +
      (values || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
    target.dataset.ready = "1";
  }

  function renderSummary(summary) {
    state.summary = summary;
    el("training-source-count").textContent = number((summary.sources || []).length);
    el("training-folder-count").textContent = number(summary.folder_count);
    el("training-track-count").textContent = number(summary.track_count);
    el("training-confirmed-count").textContent = number(summary.confirmed_tracks);
    const sources = el("training-source-list");
    sources.innerHTML = (summary.sources || []).length
      ? summary.sources.map((source) => `
        <div class="training-source-item">
          <i class="bi bi-folder2-open text-primary"></i>
          <div><strong>${escapeHtml(source.name)}</strong><small class="text-muted" title="${escapeHtml(source.path)}">${escapeHtml(source.relative_path || source.path)}</small></div>
          <button type="button" class="btn btn-sm training-source-remove" data-source-id="${escapeHtml(source.id)}" title="Убрать папку из обучающей выборки" aria-label="Убрать ${escapeHtml(source.name)} из обучающей выборки"><i class="bi bi-dash-lg" aria-hidden="true"></i></button>
        </div>`).join("")
      : '<div class="small text-muted py-2">Источники ещё не добавлены.</div>';
    sources.querySelectorAll(".training-source-remove").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("Убрать эту папку из обучающей выборки вместе с её предварительной разметкой? Музыкальные файлы останутся на месте.")) return;
        try {
          const data = await requestJson(`/api/training-dataset/sources/${encodeURIComponent(button.dataset.sourceId)}`, {method: "DELETE"});
          renderSummary(data.summary);
          await loadFolders(true);
          await loadPlan();
        } catch (error) { alertMessage(error.message, "danger"); }
      });
    });
  }

  function renderLastReport(report) {
    const target = el("training-last-report");
    if (!target) return;
    if (!report || !report.status) {
      target.classList.add("d-none");
      target.innerHTML = "";
      return;
    }
    const accepted = report.status === "accepted";
    const beforeKnown = report.active_before_known !== false;
    const added = accepted
      ? (beforeKnown ? (report.added_styles || []) : (report.activated_styles || []))
      : (beforeKnown ? (report.candidate_new_styles || []) : (report.candidate_styles || []));
    const reasons = report.quality_gate?.reasons || [];
    const changeLabels = {
      added: "Добавлен", retained: "Сохранён", removed: "Удалён",
      candidate_new: "Не активирован", candidate: "Кандидат отклонён", activated: "Активирован",
      kept_old_model: "Сохранён в рабочей модели", skipped: "Пропущен",
    };
    const rows = (report.rows || []).map((row) => `<tr>
      <td><strong>${escapeHtml(row.style)}</strong></td>
      <td>${escapeHtml(changeLabels[row.change] || row.change || "—")}</td>
      <td>${row.validation_support ? `${(Number(row.f1 || 0) * 100).toFixed(1)}%` : "—"}</td>
      <td>${row.accepted_tracks ? `${(Number(row.accepted_precision || 0) * 100).toFixed(1)}% / ${number(row.accepted_tracks)}` : row.threshold_status === "precision_unreachable" ? '<span class="text-danger">Целевая precision недостижима</span>' : "—"}</td>
    </tr>`).join("");
    const hierarchy = report.hierarchy_validation?.selection || {};
    target.className = `training-last-report mt-3 ${accepted ? "is-accepted" : "is-rejected"}`;
    target.innerHTML = `
      <div class="d-flex flex-wrap justify-content-between gap-2 align-items-center">
        <strong>${accepted ? "Последнее обучение принято" : "Последний кандидат отклонён"}</strong>
        <span class="badge text-bg-${accepted ? "success" : "danger"}">${accepted ? "Рабочая модель обновлена" : "Рабочая модель не изменена"}</span>
      </div>
      <div class="small mt-1">${beforeKnown ? "Новые стили" : (accepted ? "Активные стили после обучения" : "Стили кандидата")}: ${added.length ? added.map(escapeHtml).join(", ") : "нет"}. Сохранены: ${(report.retained_styles || []).length ? report.retained_styles.map(escapeHtml).join(", ") : "нет"}.</div>
      ${Number.isFinite(Number(hierarchy.family_validation_macro_f1)) ? `<div class="small mt-1">Точность семейств (macro F1): <strong>${(Number(hierarchy.family_validation_macro_f1) * 100).toFixed(1)}%</strong>; вес иерархии: ${Number(hierarchy.selected_weight || 0).toFixed(2)}.</div>` : ""}
      ${reasons.length ? `<div class="small text-danger mt-1">${reasons.map(escapeHtml).join("; ")}</div>` : ""}
      <details class="mt-2"><summary class="small">Метрики по стилям</summary>
        <div class="table-responsive mt-2"><table class="table table-sm mb-0"><thead><tr><th>Стиль</th><th>Итог</th><th>F1 класса</th><th>Точность принятых / треки</th></tr></thead><tbody>${rows || '<tr><td colspan="4">Нет данных</td></tr>'}</tbody></table></div>
      </details>`;
  }

  function renderQuickQuality(data = {}, remember = true) {
    if (remember) state.lastQuickQuality = data;
    const target = el("training-quick-quality-result");
    const running = Boolean(data.running || data.status === "running");
    const result = data.result || (data.diagnostic_only ? data : null);
    const buttonIds = ["training-quick-quality", "training-dataset-quick-quality"];
    buttonIds.forEach((id) => {
      const button = el(id);
      if (!button) return;
      button.disabled = running;
      button.innerHTML = running
        ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Оцениваю…'
        : '<i class="bi bi-speedometer2 me-1"></i>Быстрая оценка';
    });
    if (!target) return;
    if (running) {
      const progress = Math.max(0, Math.min(100, Number(data.progress || 0)));
      target.className = "training-last-report mt-3";
      target.innerHTML = `
        <div class="d-flex justify-content-between gap-2"><strong>Быстрая диагностическая оценка</strong><span>${Math.round(progress)}%</span></div>
        <div class="progress mt-2" style="height:8px"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width:${progress}%"></div></div>
        <div class="small text-muted mt-2">${escapeHtml(data.message || "Подготовка")}</div>`;
      return;
    }
    if (!result && !data.error) {
      target.classList.add("d-none");
      target.innerHTML = "";
      return;
    }
    if (data.error) {
      target.className = "training-last-report mt-3 is-rejected";
      target.innerHTML = `<strong>Быстрая оценка не выполнена</strong><div class="small text-danger mt-1">${escapeHtml(data.error)}</div>`;
      return;
    }
    if (result.status === "cache_insufficient") {
      const rows = (result.cache?.rows || []).map((row) => `<tr>
        <td>${escapeHtml(row.style)}</td><td>${number(row.cached)} / ${number(row.selected)}</td>
        <td>${(Number(row.coverage || 0) * 100).toFixed(1)}%</td>
      </tr>`).join("");
      target.className = "training-last-report mt-3 is-rejected";
      target.innerHTML = `
        <strong>Недостаточно кэшированных 134D-признаков</strong>
        <div class="small mt-1">${escapeHtml(result.message || "Тяжёлая extraction не запускалась.")}</div>
        <details class="mt-2"><summary class="small">Покрытие кэша по стилям</summary>
          <div class="table-responsive mt-2"><table class="table table-sm mb-0"><thead><tr><th>Стиль</th><th>В кэше / pool</th><th>Покрытие</th></tr></thead><tbody>${rows}</tbody></table></div>
        </details>`;
      return;
    }
    const fullCandidate = state.plan?.last_run || {};
    const fullRows = new Map((fullCandidate.rows || []).map((row) => [String(row.style), row]));
    const comparisonRows = (result.per_class || []).map((row) => {
      const comparison = Number.isFinite(Number(row.active_recall))
        ? `${(Number(row.active_recall) * 100).toFixed(1)}% (${Number(row.recall_delta) >= 0 ? "+" : ""}${(Number(row.recall_delta) * 100).toFixed(1)} п.п.)`
        : "—";
      const quickWeak = Number(row.f1 || 0) < 0.60 || Number(row.recall || 0) < 0.55;
      const full = fullRows.get(String(row.style));
      const fullAvailable = Boolean(full && Number(full.validation_support || 0) > 0);
      const fullGood = fullAvailable
        && Number(full.f1 || 0) >= 0.60
        && Number(full.recall || 0) >= 0.55;
      const fullWeak = fullAvailable && !fullGood;
      let verdict = '<span class="badge text-bg-success">Стабильно</span>';
      let rowClass = "";
      let category = "stable";
      if (quickWeak && fullGood) {
        verdict = '<span class="badge text-bg-info">Оценки расходятся · проверить</span>';
        rowClass = "table-info";
        category = "disagreement";
      } else if (quickWeak && fullWeak) {
        verdict = '<span class="badge text-bg-warning">Сигнал подтверждён двумя оценками</span>';
        rowClass = "table-warning";
        category = "confirmed_weak";
      } else if (quickWeak) {
        verdict = '<span class="badge text-bg-secondary">Предварительный сигнал</span>';
        rowClass = "table-light";
        category = "quick_only";
      } else if (fullWeak) {
        verdict = '<span class="badge text-bg-info">Quick лучше полного · проверить</span>';
        rowClass = "table-info";
        category = "disagreement";
      }
      const fullMetrics = fullAvailable
        ? `${(Number(full.f1 || 0) * 100).toFixed(1)}% / ${(Number(full.recall || 0) * 100).toFixed(1)}%`
        : "—";
      return {category, html: `<tr class="${rowClass}">
        <td><button class="training-quick-style-link" type="button" data-problem-style="${escapeHtml(row.style)}" title="Показать проблемные папки для этого стиля">${escapeHtml(row.style)} <i class="bi bi-folder2-open" aria-hidden="true"></i></button>${row.protected ? ' <span class="badge text-bg-secondary">protected</span>' : ""}</td>
        <td>${(Number(row.f1 || 0) * 100).toFixed(1)}%</td>
        <td>${(Number(row.recall || 0) * 100).toFixed(1)}%</td>
        <td>${number(row.support)}</td><td>${fullMetrics}</td><td>${comparison}</td><td>${verdict}</td>
      </tr>`};
    });
    const rows = comparisonRows.map((row) => row.html).join("");
    const confirmedWeak = comparisonRows.filter((row) => row.category === "confirmed_weak").length;
    const disagreements = comparisonRows.filter((row) => row.category === "disagreement").length;
    const quickOnly = comparisonRows.filter((row) => row.category === "quick_only").length;
    const interpretation = [
      confirmedWeak ? `Сигнал подтверждён quick и полным кандидатом: ${number(confirmedWeak)}.` : "",
      disagreements ? `Оценки расходятся: ${number(disagreements)} — эти стили не считаются однозначно слабыми.` : "",
      quickOnly ? `Есть только предварительный quick-сигнал: ${number(quickOnly)}.` : "",
    ].filter(Boolean).join(" ");
    const fullCandidateLabel = fullCandidate.completed_at
      ? `Сравнение с полным кандидатом от ${escapeHtml(formatTrainingTime(fullCandidate.completed_at))}.`
      : "Последний полный кандидат для сравнения ещё не найден.";
    target.className = "training-last-report mt-3 is-accepted";
    target.innerHTML = `
      <div class="d-flex flex-wrap justify-content-between gap-2"><strong>Быстрая оценка завершена</strong><span class="badge text-bg-info">только диагностика</span></div>
      <div class="small mt-1">Macro F1: <strong>${(Number(result.macro_f1 || 0) * 100).toFixed(1)}%</strong> · validation: ${number(result.split?.validation_tracks)} · ${Number(result.duration_seconds || 0).toFixed(1)} сек.</div>
      <div class="small text-muted mt-1">${fullCandidateLabel}</div>
      ${interpretation ? `<div class="small mt-1">${interpretation}</div>` : '<div class="small text-success mt-1">Обе оценки не показали явно слабых классов.</div>'}
      <div class="small text-muted mt-1">Рабочая модель, metadata кандидата и quality gate не изменены.</div>
      <details class="mt-2" open><summary class="small">Метрики по стилям</summary>
        <div class="table-responsive mt-2"><table class="table table-sm mb-0"><thead><tr><th>Стиль</th><th>Quick F1</th><th>Quick recall</th><th>Validation</th><th>Полный F1 / recall</th><th>Recall активной</th><th>Вывод</th></tr></thead><tbody>${rows}</tbody></table></div>
      </details>`;
    target.querySelectorAll(".training-quick-style-link").forEach((button) => {
      button.addEventListener("click", () => {
        showProblemFolders(button.dataset.problemStyle || "all").catch((error) => alertMessage(error.message, "danger"));
      });
    });
  }

  async function pollQuickQuality() {
    window.clearTimeout(state.quickQualityTimer);
    try {
      const data = await requestJson("/api/training-dataset/quick-quality?t=" + Date.now());
      renderQuickQuality(data);
      if (data.running || data.status === "running") {
        state.quickQualityTimer = window.setTimeout(pollQuickQuality, 1200);
      }
    } catch (error) {
      renderQuickQuality({error: error.message});
    }
  }

  async function startQuickQuality() {
    const readyStyles = (state.plan?.rows || []).filter((row) => row.readiness === "ready");
    if (readyStyles.length < 2) {
      return alertMessage("Для оценки нужны минимум два готовых стиля.", "warning");
    }
    try {
      const data = await requestJson("/api/training-dataset/quick-quality", {
        method: "POST", body: "{}",
      });
      renderQuickQuality(data);
      state.quickQualityTimer = window.setTimeout(pollQuickQuality, 500);
    } catch (error) {
      alertMessage(error.message, "danger");
    }
  }

  function renderPlan(plan) {
    state.plan = plan || {};
    const limit = el("training-style-limit");
    if (limit && plan?.max_tracks_per_style) limit.value = String(plan.max_tracks_per_style);
    const sourceSettings = plan?.source_settings || {};
    el("training-use-builder").checked = sourceSettings.dataset_builder_enabled !== false;
    el("training-use-samples").checked = sourceSettings.reference_samples_enabled !== false;
    el("training-use-rekordbox").checked = Boolean(sourceSettings.rekordbox_enabled);
    const samplesInput = el("training-samples-path");
    if (document.activeElement !== samplesInput) {
      samplesInput.value = state.summary?.settings?.reference_samples_path || "";
    }
    el("training-samples-path-status").textContent = sourceSettings.reference_samples_exists
      ? `Доступна: ${sourceSettings.reference_samples_path || "путь по умолчанию"}`
      : `Папка недоступна: ${sourceSettings.reference_samples_path || "путь по умолчанию"}`;
    samplesInput.disabled = !el("training-use-samples").checked;
    el("training-samples-path-save").disabled = !el("training-use-samples").checked;
    el("training-plan-minimum").textContent = `Минимум: ${number(plan.minimum_tracks_per_style || 200)} треков на стиль`;
    const readyRows = (plan?.rows || []).filter((row) => row.readiness === "ready");
    el("training-plan-summary").textContent = readyRows.length
      ? `В RF войдут ${number(readyRows.length)} стилей: ${readyRows.map((row) => row.style).join(", ")}. До анализа аудио будет выбрано примерно ${number(plan.selected_total)} треков.`
      : "Нет минимум двух стилей с достаточным количеством подтверждённых треков.";
    const body = el("training-plan-rows");
    body.innerHTML = (plan?.rows || []).length ? plan.rows.map((row) => {
      const ready = row.readiness === "ready";
      const status = row.readiness === "disabled"
        ? `<span class="badge text-bg-secondary">${row.fallback_only ? "Семейство / fallback" : row.mandatory_excluded ? "Служебный признак" : "Отключён"}</span>`
        : ready
        ? (row.class_change === "new"
          ? '<span class="badge text-bg-primary">Новый кандидат</span>'
          : row.class_change === "retained"
            ? '<span class="badge text-bg-success">Готов · в модели</span>'
            : '<span class="badge text-bg-info">Кандидат</span>')
        : row.readiness === "insufficient"
          ? `<span class="badge text-bg-warning">Мало: ${number(row.candidate_tracks)} / ${number(row.minimum_required)}</span>`
          : '<span class="badge text-bg-secondary">Нет данных</span>';
      const sources = [
        row.builder_tracks ? `выборка ${number(row.builder_tracks)}` : "",
        row.samples_tracks ? `Samples ${number(row.samples_tracks)}` : "",
        row.rekordbox_tracks ? `Rekordbox ${number(row.rekordbox_tracks)}` : "",
      ].filter(Boolean).join(" · ");
      return `<tr class="${row.readiness === "disabled" ? "training-style-disabled" : ""}">
        <td><input class="form-check-input training-style-toggle" type="checkbox" data-style="${escapeHtml(row.style)}" ${row.enabled ? "checked" : ""} ${row.mandatory_excluded ? "disabled" : ""} aria-label="Включить ${escapeHtml(row.style)}"></td>
        <td><strong>${escapeHtml(row.style)}</strong><small class="d-block text-muted">${escapeHtml(sources || "только рабочая модель")}</small></td>
        <td>${number(row.confirmed_folders)}</td>
        <td>${number(row.available_tracks)}</td>
        <td><strong>${number(row.selected_tracks)}</strong></td>
        <td>${status}</td>
      </tr>`;
    }).join("") : '<tr><td colspan="6" class="text-muted text-center py-3">Подтверждённых стилей пока нет.</td></tr>';
    body.querySelectorAll(".training-style-toggle").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const excluded = new Set(state.plan?.excluded_styles || []);
        if (checkbox.checked) excluded.delete(checkbox.dataset.style);
        else excluded.add(checkbox.dataset.style);
        try {
          await saveStyleSelection(excluded, `${checkbox.dataset.style}: ${checkbox.checked ? "включён" : "отключён"}.`);
        } catch (error) {
          checkbox.checked = !checkbox.checked;
          alertMessage(error.message, "danger");
        }
      });
    });
    renderLastReport(plan?.last_run);
    if (state.lastQuickQuality) renderQuickQuality(state.lastQuickQuality, false);
  }

  async function loadPlan(force = false) {
    if (state.planRequest && !force) return state.planRequest;
    if (force && state.planController) state.planController.abort();

    const body = el("training-plan-rows");
    const summary = el("training-plan-summary");
    if (body) body.innerHTML = '<tr><td colspan="6" class="text-muted text-center py-3">План рассчитывается…</td></tr>';
    if (summary) summary.textContent = "Считаем состав источников без анализа аудио.";

    const controller = new AbortController();
    state.planController = controller;
    let timedOut = false;
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 15000);
    const request = requestJson("/api/training-dataset/plan?t=" + Date.now(), {
      signal: controller.signal,
    }).then((plan) => {
      renderPlan(plan);
      return plan;
    }).catch((error) => {
      if (error?.name === "AbortError" && !timedOut) return state.plan;
      const message = timedOut
        ? "План не ответил за 15 секунд. Проверьте доступность Rekordbox JSON и повторите расчёт кнопкой ↻."
        : `Не удалось загрузить план: ${error.message}`;
      if (summary) summary.textContent = message;
      if (body) body.innerHTML = `<tr><td colspan="6" class="text-danger text-center py-3">${escapeHtml(message)}</td></tr>`;
      throw new Error(message);
    }).finally(() => {
      window.clearTimeout(timeoutId);
      if (state.planRequest === request) state.planRequest = null;
      if (state.planController === controller) state.planController = null;
    });
    state.planRequest = request;
    return request;
  }

  async function saveStyleSelection(excludedStyles, successMessage = "Выбор стилей сохранён.") {
    const data = await requestJson("/api/training-dataset/settings", {
      method: "PATCH",
      body: JSON.stringify({excluded_styles: [...excludedStyles].sort()}),
    });
    renderPlan(data.plan);
    if (successMessage) alertMessage(successMessage, "success");
    return data.plan;
  }

  async function saveTrainingSources(values, successMessage) {
    const data = await requestJson("/api/training-dataset/settings", {
      method: "PATCH",
      body: JSON.stringify(values),
    });
    if (data.settings && state.summary) {
      state.summary.settings = data.settings;
    }
    renderPlan(data.plan);
    if (successMessage) alertMessage(successMessage, "success");
    return data.plan;
  }

  async function loadDataset() {
    const data = await requestJson("/api/training-dataset?t=" + Date.now());
    state.labels = data.labels || {};
    fillSelect(el("training-bulk-style"), state.labels.base_styles, "Стиль без изменения");
    fillSelect(el("training-bulk-language"), state.labels.languages, "Язык без изменения");
    fillSelect(el("training-bulk-version"), state.labels.version_types, "Версия без изменения");
    state.reviewPreview = {
      mode: data.settings?.review_preview_mode || "percent",
      percent: Number(data.settings?.review_preview_percent ?? 30),
      seconds: Number(data.settings?.review_preview_seconds ?? 60),
    };
    syncReviewPreviewSettings();
    renderSummary(data);
    renderProgress(data.progress || {});
    await Promise.all([loadFolders(true), loadPlan()]);
  }

  function renderPreparationAssistant(data) {
    state.assistantPreview = data;
    state.assistantConfirmed = false;
    el("training-assistant-result").classList.remove("d-none");
    const summary = data.summary || {};
    el("training-assistant-totals").innerHTML = [
      ["В следующем обучении", number(summary.tracks_before), "треков после штатных лимитов"],
      ["Оставить", number(summary.recommended_leave_folders), "папок"],
      ["Рекомендуется исключить", number(summary.recommended_exclude_folders), `безопасно применить: ${number(summary.safe_to_apply_folders)}`],
      ["Нужна проверка", number(summary.needs_review_folders), "папок"],
    ].map(([label, value, note]) => `<div><small>${label}</small><strong>${value}</strong><span>${note}</span></div>`).join("");
    const automatic = data.pipeline_automatic || {};
    el("training-assistant-pipeline").innerHTML = `<strong>Pipeline уже не допускает автоматически:</strong> неподтверждённые ${number(automatic.unconfirmed_tracks)}, вручную исключённые ${number(automatic.excluded_tracks)}, dedup последнего запуска ${number(automatic.dedup_tracks_last_run)}, fingerprint-конфликты ${number(automatic.fingerprint_conflicts_last_run)}, ошибки обработки ${number(automatic.processing_errors_last_run)}.`;
    el("training-assistant-styles").innerHTML = (data.styles || []).filter((row) => row.enabled && row.training_tracks_before).map((row) => `<div class="training-assistant-style ${row.warning ? "has-warning" : ""}">
      <div><strong>${escapeHtml(row.style)}</strong><span>${number(row.training_tracks_before)} → ${number(row.training_tracks_after)} треков</span></div>
      <small>Чистые источники: ${number(row.clean_folders)} · подозрительные: ${number(row.problem_folders)} · review/errors: ${number(row.review_errors)} · исключить: ${number(row.recommended_exclusions)} · проверить: ${number(row.needs_review)}</small>
      ${row.warning ? `<div class="text-danger"><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(row.warning)}</div>` : ""}
    </div>`).join("") || '<div class="small text-muted p-2">Нет включённых стилей с подтверждёнными треками.</div>';
    const safe = (data.recommendations || []).filter((row) => row.safe_to_apply);
    const blocked = (data.recommendations || []).filter((row) => row.recommendation === "recommend_exclude" && !row.safe_to_apply);
    const preview = el("training-assistant-preview");
    preview.classList.remove("d-none");
    preview.innerHTML = `<strong>Preview изменений</strong><div class="small mt-1">Будут исключены только перечисленные папки (${number(safe.length)}):</div>
      <ul>${safe.map((row) => `<li><strong>${escapeHtml(row.style)}</strong> · ${escapeHtml(row.relative_path || row.path)} · ${number(row.tracks)} треков</li>`).join("") || "<li>Нет безопасных исключений — текущую выборку лучше оставить.</li>"}</ul>
      ${blocked.length ? `<div class="text-warning"><i class="bi bi-shield-exclamation me-1"></i>${number(blocked.length)} рекомендаций заблокировано защитой размера класса.</div>` : ""}
      <label class="form-check mt-2"><input id="training-assistant-confirm" class="form-check-input" type="checkbox" ${safe.length ? "" : "disabled"}><span class="form-check-label">Подтверждаю исключение только перечисленных папок; музыкальные файлы не удаляются.</span></label>`;
    const confirm = el("training-assistant-confirm");
    if (confirm) confirm.addEventListener("change", () => {
      state.assistantConfirmed = confirm.checked;
      el("training-assistant-apply").disabled = !confirm.checked || !safe.length;
    });
    el("training-assistant-apply").disabled = true;
  }

  async function loadPreparationAssistant() {
    el("training-assistant-analyze").disabled = true;
    try {
      renderPreparationAssistant(await requestJson("/api/training-dataset/preparation-assistant?t=" + Date.now()));
    } finally {
      el("training-assistant-analyze").disabled = false;
    }
  }

  function statusBadge(status) {
    const values = {
      confirmed: ["success", "Подтверждено"], suggested: ["primary", "Предложено"],
      ambiguous: ["warning", "Конфликт"], unmapped: ["secondary", "Без стиля"],
      excluded: ["danger", "Исключено"],
    };
    const item = values[status] || ["secondary", status || "—"];
    return `<span class="badge text-bg-${item[0]}">${item[1]}</span>`;
  }

  function selectedIds() {
    return [...state.selected];
  }

  function updateSelectedCount() {
    el("training-selected-count").textContent = state.selected.size ? `Выбрано: ${state.selected.size}` : "Не выбрано";
  }

  function updateFolderActions(problematic = el("training-folder-status").value === "problematic") {
    const status = el("training-folder-status").value;
    const confirmButton = el("training-bulk-confirm");
    const hint = el("training-action-hint");
    const selected = state.selected.size;
    if (status === "excluded") {
      confirmButton.innerHTML = '<i class="bi bi-arrow-counterclockwise me-1"></i>Вернуть в обучение';
      hint.textContent = selected ? "Папки снова получат статус «Подтверждено» и войдут в следующее обучение." : "Выберите исключённые папки, которые нужно вернуть в обучение.";
    } else if (problematic) {
      confirmButton.innerHTML = '<i class="bi bi-check2-circle me-1"></i>Оставить';
      hint.textContent = selected ? "Разметка выбранных папок будет подтверждена; диагностический риск при этом не удаляется." : "Проверьте рекомендацию и подробности, затем выберите действие.";
    } else {
      confirmButton.textContent = "Подтвердить и включить";
      hint.textContent = selected ? "Выбранные папки получат статус «Подтверждено» и войдут в следующее обучение." : "Выберите одну или несколько папок.";
    }
    el("training-bulk-exclude").classList.toggle("d-none", status === "excluded");
  }

  function setTaxonomyEditing(enabled) {
    state.editingTaxonomy = Boolean(enabled);
    if (!state.editingTaxonomy) {
      el("training-bulk-style").value = "";
      el("training-bulk-language").value = "";
      el("training-bulk-version").value = "";
    }
    el("training-taxonomy-fields").classList.toggle("d-none", !state.editingTaxonomy);
    el("training-edit-taxonomy").classList.toggle("active", state.editingTaxonomy);
    el("training-edit-taxonomy").innerHTML = state.editingTaxonomy
      ? '<i class="bi bi-x-lg me-1"></i>Скрыть разметку'
      : '<i class="bi bi-pencil me-1"></i>Изменить разметку';
  }

  function problemIssueCount(row) {
    return Number(row.review_queue_tracks || 0) + Number(row.validation_errors || 0);
  }

  function problemHasConflicts(row) {
    return Number(row.label_conflicts || 0) > 0
      || Number(row.fingerprint_duplicates || 0) > 0
      || Number(row.group_duplicate_tracks || 0) > 0;
  }

  function problemPairs(row) {
    return Array.isArray(row.confusion_pairs) ? row.confusion_pairs : [];
  }

  function problemStyleContribution(row) {
    return problemIssueCount(row);
  }

  function replaceProblemSelectOptions(select, rows, selected, allLabel) {
    if (!select) return;
    select.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "all";
    allOption.textContent = allLabel;
    select.appendChild(allOption);
    rows.forEach(({value, label}) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    });
    select.value = selected;
  }

  function syncFolderStyleFilter(styles, selected = state.folderStyle) {
    const select = el("training-folder-style");
    if (!select) return;
    const values = [...new Set((styles || []).map((value) => String(value || "").trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, "ru"));
    if (selected !== "all" && !values.includes(selected)) selected = "all";
    state.folderStyle = selected;
    replaceProblemSelectOptions(
      select,
      values.map((value) => ({value, label: value})),
      selected,
      "Все",
    );
  }

  function syncProblemDiagnosticFilters(items) {
    const confusedSelect = el("training-problem-confused-with");
    const styles = [...new Set(items.map((row) => String(row.base_style || "").trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, "ru"));
    if (state.problemStyle !== "all" && !styles.includes(state.problemStyle)) state.problemStyle = "all";
    state.folderStyle = state.problemStyle;
    syncFolderStyleFilter(styles, state.problemStyle);

    const targets = new Map();
    if (state.problemStyle !== "all") {
      items.forEach((row) => {
        if (String(row.base_style || "") !== state.problemStyle) return;
        problemPairs(row).forEach((pair) => {
          const trueStyle = String(pair.true_style || row.base_style || "");
          const predictedStyle = String(pair.predicted_style || "").trim();
          if (trueStyle !== state.problemStyle || !predictedStyle) return;
          targets.set(predictedStyle, (targets.get(predictedStyle) || 0) + Number(pair.count || 0));
        });
      });
    }
    const targetRows = [...targets.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ru"))
      .map(([value, count]) => ({value, label: `${value} (${number(count)})`}));
    if (state.problemConfusedWith !== "all" && !targets.has(state.problemConfusedWith)) state.problemConfusedWith = "all";
    replaceProblemSelectOptions(
      confusedSelect,
      targetRows,
      state.problemConfusedWith,
      state.problemStyle === "all" ? "Сначала выберите стиль" : "Все пары",
    );
    if (confusedSelect) confusedSelect.disabled = state.problemStyle === "all" || targetRows.length === 0;
  }

  function updateProblemFilterButtons() {
    document.querySelectorAll(".training-problem-filter").forEach((item) => {
      const active = (item.dataset.problemFilter || "all") === state.problemFilter;
      item.classList.toggle("btn-primary", active);
      item.classList.toggle("btn-outline-danger", !active && item.dataset.problemFilter === "high");
      item.classList.toggle("btn-outline-warning", !active && item.dataset.problemFilter === "medium");
      item.classList.toggle("btn-outline-success", !active && item.dataset.problemFilter === "reviewed");
      item.classList.toggle("btn-outline-secondary", !active && !["high", "medium", "reviewed"].includes(item.dataset.problemFilter));
      if (active) item.classList.remove("btn-outline-danger", "btn-outline-warning", "btn-outline-success", "btn-outline-secondary");
    });
  }

  function sortProblemItems(items) {
    const riskRank = {high: 3, medium: 2, low: 1};
    const compareDefault = (a, b) =>
      (riskRank[b.risk] || 0) - (riskRank[a.risk] || 0)
      || problemIssueCount(b) - problemIssueCount(a)
      || Number(b.disputed_percent || 0) - Number(a.disputed_percent || 0)
      || String(a.relative_path || a.path || "").localeCompare(String(b.relative_path || b.path || ""), "ru");
    const compareContribution = (a, b) =>
      problemStyleContribution(b) - problemStyleContribution(a)
      || Number(b.disputed_percent || 0) - Number(a.disputed_percent || 0)
      || (riskRank[b.risk] || 0) - (riskRank[a.risk] || 0)
      || String(a.relative_path || a.path || "").localeCompare(String(b.relative_path || b.path || ""), "ru");
    const comparators = {
      risk: compareDefault,
      contribution: compareContribution,
      issues: (a, b) => problemIssueCount(b) - problemIssueCount(a) || compareDefault(a, b),
      disputed: (a, b) => Number(b.disputed_percent || 0) - Number(a.disputed_percent || 0) || compareDefault(a, b),
      tracks: (a, b) => Number(b.training_tracks || 0) - Number(a.training_tracks || 0) || compareDefault(a, b),
      style: (a, b) => String(a.base_style || "").localeCompare(String(b.base_style || ""), "ru") || compareDefault(a, b),
    };
    return [...items].sort(comparators[state.problemSort] || compareDefault);
  }

  function filterProblemItems(items) {
    const filter = state.problemFilter;
    return items.filter((row) => {
      const rowStyle = String(row.base_style || "");
      if (state.problemStyle !== "all" && rowStyle !== state.problemStyle) return false;
      if (state.problemConfusedWith !== "all") {
        const pairFound = problemPairs(row).some((pair) => {
          const trueStyle = String(pair.true_style || rowStyle);
          return trueStyle === state.problemStyle
            && String(pair.predicted_style || "") === state.problemConfusedWith;
        });
        if (!pairFound) return false;
      }
      if (["high", "medium", "low"].includes(filter)) return !row.review_complete && row.risk === filter;
      if (filter === "conflicts") return !row.review_complete && problemHasConflicts(row);
      if (filter === "reviewed") return Boolean(row.review_complete);
      if (filter === "attention") return !row.review_complete;
      return true;
    });
  }

  function folderTrackRangeMatches(row, problematic = false) {
    const count = Number(problematic ? row.training_tracks : row.track_count) || 0;
    const range = state.folderTrackRange;
    if (range === "lt20") return count < 20;
    if (range === "20-49") return count >= 20 && count <= 49;
    if (range === "50-99") return count >= 50 && count <= 99;
    if (range === "100plus") return count >= 100;
    return true;
  }

  function sortFolderItems(items, problematic = false) {
    const direction = state.folderSortDirection === "desc" ? -1 : 1;
    const path = (row) => String(row.relative_path || row.path || "");
    const value = (row) => {
      if (state.folderSort === "tracks") return Number(problematic ? row.training_tracks : row.track_count) || 0;
      if (state.folderSort === "style") return String(row.base_style || "");
      if (state.folderSort === "status") return String(row.status || "");
      return path(row);
    };
    return [...items].sort((a, b) => {
      const left = value(a);
      const right = value(b);
      const compared = typeof left === "number"
        ? left - right
        : String(left).localeCompare(String(right), "ru", {numeric: true, sensitivity: "base"});
      return compared * direction || path(a).localeCompare(path(b), "ru", {numeric: true, sensitivity: "base"});
    });
  }

  function folderSortHeading(key, label) {
    const active = state.folderSort === key;
    const indicator = active ? (state.folderSortDirection === "asc" ? "▲" : "▼") : "";
    return `<button class="training-folder-sort" type="button" data-folder-sort="${key}" aria-label="Сортировать: ${label}">${label}<span class="training-folder-sort-indicator">${indicator}</span></button>`;
  }

  function confusionPairButton(pair, compact = false) {
    const trueStyle = String(pair.true_style || "");
    const predictedStyle = String(pair.predicted_style || "");
    const separator = compact ? " " : ": ";
    return `<button class="training-confusion-pair" type="button" data-true-style="${escapeHtml(trueStyle)}" data-predicted-style="${escapeHtml(predictedStyle)}" title="Показать папки: ${escapeHtml(trueStyle)} путается с ${escapeHtml(predictedStyle)}">${escapeHtml(trueStyle)} → ${escapeHtml(predictedStyle)}${separator}<strong>${number(pair.count)}</strong></button>`;
  }

  function problemRecommendation(row) {
    if ((row.name_warnings || []).length || Number(row.disputed_percent || 0) >= 25) return "Возможно смешанная папка";
    if (Number(row.pending_disputed_tracks || 0) > 0) return "Проверить отдельные треки";
    if (Number(row.disputed_tracks || 0) > 0) return "Спорные треки обработаны";
    return "Оставить папку";
  }

  function renderFolderRow(row, problematic = false) {
    const path = row.relative_path || row.path || "—";
    const trackCount = problematic ? row.training_tracks : row.track_count;
    const regularMeta = [
      row.source_name || "",
      row.language && row.language !== "Unknown" ? row.language : "",
      row.version_type && row.version_type !== "Unknown" ? row.version_type : "",
      row.confidence != null ? `${Math.round(Number(row.confidence || 0) * 100)}%` : "",
    ].filter(Boolean).join(" · ");
    let diagnosticSummary = "";
    let diagnosticDetails = "";
    let rowClass = "training-folder-row";

    if (problematic) {
      const pairs = row.confusion_pairs || [];
      const compactPair = pairs.slice(0, 1).map((pair) => confusionPairButton(pair, true)).join("") || "Нет основной пары";
      const allPairs = pairs.map((pair) => confusionPairButton(pair)).join('<span class="text-muted">; </span>') || "—";
      const warnings = (row.name_warnings || []).map((value) => `<span class="badge text-bg-warning">${escapeHtml(value)}</span>`).join("");
      const riskLabels = {high: "Высокий риск", medium: "Средний риск", low: "Низкий риск"};
      const riskClasses = {high: "danger", medium: "warning", low: "secondary"};
      const risk = row.risk || "low";
      const pendingTracks = Number(row.pending_disputed_tracks || 0);
      const manuallyResolvedTracks = Number(row.reviewed_disputed_tracks || 0) + Number(row.excluded_disputed_tracks || 0);
      const automaticTracks = Number(row.automatic_disputed_tracks || 0);
      const reviewButtonLabel = pendingTracks
        ? `Показать ${number(pendingTracks)} ожидающих проверки`
        : `Посмотреть ${number(row.disputed_tracks)} решений`;
      const riskBadge = row.review_complete
        ? `<span class="badge text-bg-success"><i class="bi bi-check2 me-1"></i>Проверено</span><span class="badge text-bg-secondary">Был ${escapeHtml((riskLabels[risk] || risk).toLocaleLowerCase("ru"))}</span>`
        : `<span class="badge text-bg-${riskClasses[risk] || "secondary"}">${riskLabels[risk] || risk}</span>`;
      rowClass += ` training-problem-row training-problem-${escapeHtml(risk)}`;
      diagnosticSummary = `<div class="training-problem-inline">
        ${riskBadge}
        <span class="training-problem-disputed">Спорных <strong>${Number(row.disputed_percent || 0).toFixed(1)}%</strong></span>
        <span class="training-problem-main-pair">${compactPair}</span>
        <span><strong>${escapeHtml(problemRecommendation(row))}</strong></span>
        ${Number(row.disputed_tracks || 0) ? `<button class="btn btn-sm btn-outline-primary training-problem-review-link" type="button" data-review-folder-id="${escapeHtml(row.id)}" data-review-style="${escapeHtml(row.base_style || "all")}" data-review-status="${pendingTracks ? "pending" : "all"}"><i class="bi bi-music-note-list"></i>${reviewButtonLabel}</button>` : ""}
      </div>`;
      diagnosticDetails = `<details class="training-problem-details">
        <summary>Подробнее</summary>
        <div class="training-problem-recommendation"><strong>Рекомендация:</strong> ${escapeHtml(problemRecommendation(row))}</div>
        <div><strong>Прогресс решений:</strong> обработано ${number(manuallyResolvedTracks + automaticTracks)} из ${number(row.disputed_tracks)} · осталось проверить ${number(pendingTracks)}</div>
        <div><strong>Review / ошибки:</strong> ${number(row.review_queue_tracks)} / ${number(row.validation_errors)} · <strong>validation:</strong> ${number(row.validation_tracks)}</div>
        <div><strong>Все пары:</strong> ${allPairs}</div>
        <div><strong>Конфликты:</strong> метки ${number(row.label_conflicts)} · fingerprint-дубли ${number(row.fingerprint_duplicates)} · группы версий ${number(row.group_duplicate_tracks)}</div>
        ${(row.problem_reasons || []).length ? `<div><strong>Причины:</strong> ${escapeHtml(row.problem_reasons.join("; "))}</div>` : ""}
        ${warnings ? `<div class="training-problem-warnings">${warnings}</div>` : ""}
        <div class="training-problem-full-path"><strong>Полный путь:</strong> ${escapeHtml(row.path || path)}</div>
      </details>`;
    }

    return `<tr class="${rowClass}">
      <td><input class="form-check-input training-folder-check" type="checkbox" data-folder-id="${escapeHtml(row.id)}" ${state.selected.has(row.id) ? "checked" : ""} aria-label="Выбрать папку"></td>
      <td><span class="training-folder-path" title="${escapeHtml(row.path)}">${escapeHtml(path)}</span>${problematic ? diagnosticSummary + diagnosticDetails : (regularMeta ? `<small class="training-folder-meta">${escapeHtml(regularMeta)}</small>` : "")}</td>
      <td><strong>${number(trackCount)}</strong></td>
      <td><strong>${escapeHtml(row.base_style || "—")}</strong>${!problematic && row.content_genre ? `<small class="training-folder-meta">контент: ${escapeHtml(row.content_genre)}</small>` : ""}</td>
      <td>${statusBadge(row.status)}</td>
    </tr>`;
  }

  async function loadFolders(reset = false, preserveSelection = false) {
    if (reset) {
      state.offset = 0;
      if (!preserveSelection) state.selected.clear();
    }
    const status = el("training-folder-status").value;
    const problematic = status === "problematic";
    const query = el("training-folder-search").value.trim();
    state.folderStyle = el("training-folder-style")?.value || state.folderStyle || "all";
    state.folderTrackRange = el("training-folder-tracks")?.value || state.folderTrackRange || "all";
    if (problematic) state.problemStyle = state.folderStyle;
    const params = new URLSearchParams({
      offset: problematic ? 0 : state.offset,
      limit: problematic ? 2000 : state.limit,
      status,
      q: query,
      style: state.folderStyle,
      tracks: state.folderTrackRange,
      sort: state.folderSort,
      direction: state.folderSortDirection,
    });
    const endpoint = problematic
      ? "/api/training-dataset/problem-folders"
      : "/api/training-dataset/folders";
    const data = await requestJson(`${endpoint}?${params}`);
    let visibleItems = data.items || [];
    if (problematic) {
      state.problemItems = visibleItems;
      syncProblemDiagnosticFilters(state.problemItems);
      visibleItems = filterProblemItems(visibleItems).filter((row) => folderTrackRangeMatches(row, true));
      visibleItems = state.folderSortExplicit
        ? sortFolderItems(visibleItems, true)
        : sortProblemItems(visibleItems);
      state.total = visibleItems.length;
      visibleItems = visibleItems.slice(state.offset, state.offset + state.limit);
    } else {
      state.problemItems = [];
      state.total = Number(data.total || 0);
      syncFolderStyleFilter(data.available_styles || [], state.folderStyle);
    }
    const head = el("training-folder-head");
    if (head) {
      head.innerHTML = `<th><input id="training-select-page" class="form-check-input" type="checkbox"></th><th>${folderSortHeading("path", "Папка")}</th><th>${folderSortHeading("tracks", "Треки")}</th><th>${folderSortHeading("style", "Стиль")}</th><th>${folderSortHeading("status", "Статус")}</th>`;
      head.querySelectorAll(".training-folder-sort").forEach((button) => {
        button.addEventListener("click", () => {
          const key = button.dataset.folderSort || "path";
          if (state.folderSort === key) state.folderSortDirection = state.folderSortDirection === "asc" ? "desc" : "asc";
          else {
            state.folderSort = key;
            state.folderSortDirection = key === "tracks" ? "desc" : "asc";
          }
          state.folderSortExplicit = true;
          loadFolders(true, true).catch((error) => alertMessage(error.message, "danger"));
        });
      });
    }
    const problemSummary = el("training-problem-summary");
    if (problemSummary) problemSummary.classList.toggle("d-none", !problematic);
    if (problematic && problemSummary) {
      const summary = data.summary || {};
      problemSummary.textContent = summary.report_available
        ? `Требуют внимания: ${number(summary.attention_folders)}; проверено: ${number(summary.reviewed_problem_folders)}; исторически проблемных: ${number(summary.problem_folders)}. Старые risk/confusion сохранены, ничего не исключается автоматически.`
        : "Отчёты последнего обучения ещё не найдены.";
    }
    const problemTools = el("training-problem-tools");
    if (problemTools) problemTools.classList.toggle("d-none", !problematic);
    const body = el("training-folder-rows");
    body.innerHTML = visibleItems.length
      ? visibleItems.map((row) => renderFolderRow(row, problematic)).join("")
      : '<tr><td colspan="5" class="text-muted text-center py-4">Нет папок для выбранного фильтра.</td></tr>';
    body.querySelectorAll(".training-folder-check").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selected.add(checkbox.dataset.folderId);
        else state.selected.delete(checkbox.dataset.folderId);
        updateSelectedCount();
        updateFolderActions();
      });
    });
    body.querySelectorAll(".training-confusion-pair").forEach((button) => {
      button.addEventListener("click", () => {
        showProblemFolders(
          button.dataset.trueStyle || "all",
          button.dataset.predictedStyle || "all",
        ).catch((error) => alertMessage(error.message, "danger"));
      });
    });
    body.querySelectorAll(".training-problem-review-link").forEach((button) => {
      button.addEventListener("click", () => {
        showDisputedTracks(button.dataset.reviewFolderId, button.dataset.reviewStyle, "all", button.dataset.reviewStatus || "all")
          .catch((error) => alertMessage(error.message, "danger"));
      });
    });
    const selectPage = el("training-select-page");
    if (selectPage) {
      selectPage.checked = false;
      selectPage.addEventListener("change", handleSelectPage);
    }
    const start = state.total ? state.offset + 1 : 0;
    const end = Math.min(state.offset + visibleItems.length, state.total);
    el("training-page-text").textContent = `${number(start)}–${number(end)} из ${number(state.total)}`;
    el("training-page-prev").disabled = state.offset <= 0;
    el("training-page-next").disabled = state.offset + state.limit >= state.total;
    updateSelectedCount();
    updateFolderActions(problematic);
  }

  async function showProblemFolders(style = "all", confusedWith = "all") {
    setReviewMode("folders");
    const requestedStyle = String(style || "all");
    state.problemStyle = requestedStyle;
    state.folderStyle = requestedStyle;
    state.problemConfusedWith = String(confusedWith || "all");
    state.problemFilter = "attention";
    state.problemSort = "contribution";
    state.folderSortExplicit = false;
    el("training-folder-status").value = "problematic";
    if (el("training-folder-style")) el("training-folder-style").value = requestedStyle;
    el("training-problem-sort").value = "contribution";
    updateProblemFilterButtons();
    await loadFolders(true, true);
    el("training-problem-tools").scrollIntoView({behavior: "smooth", block: "start"});
    if (requestedStyle !== "all" && state.problemStyle === "all") {
      alertMessage(`Для стиля «${requestedStyle}» в последней диагностике нет проблемных папок.`, "info");
    }
  }

  function syncReviewPreviewSettings() {
    const mode = state.reviewPreview.mode === "time" ? "time" : "percent";
    el("training-preview-mode").value = mode;
    el("training-preview-percent").value = String(state.reviewPreview.percent);
    el("training-preview-seconds").value = String(state.reviewPreview.seconds);
    el("training-preview-percent-wrap").classList.toggle("d-none", mode !== "percent");
    el("training-preview-seconds-wrap").classList.toggle("d-none", mode !== "time");
  }

  function setReviewMode(mode) {
    state.reviewMode = mode === "tracks" ? "tracks" : "folders";
    const tracks = state.reviewMode === "tracks";
    el("training-folder-review-pane").classList.toggle("d-none", tracks);
    el("training-track-review-pane").classList.toggle("d-none", !tracks);
    const folderTab = el("training-review-folders-tab");
    const trackTab = el("training-review-tracks-tab");
    folderTab.classList.toggle("btn-primary", !tracks);
    folderTab.classList.toggle("btn-outline-primary", tracks);
    folderTab.setAttribute("aria-selected", String(!tracks));
    trackTab.classList.toggle("btn-primary", tracks);
    trackTab.classList.toggle("btn-outline-primary", !tracks);
    trackTab.setAttribute("aria-selected", String(tracks));
    if (!tracks) stopReviewPreview();
  }

  function reviewReasonLabel(reason) {
    return ({
      validation_error: "ошибка validation",
      review_queue: "очередь review",
      fingerprint_label_conflict: "конфликт fingerprint-меток",
      strict_duplicate: "строгий дубликат",
    })[reason] || reason;
  }

  function reviewStatusBadge(row) {
    if (row.review_status === "excluded") return '<span class="badge text-bg-danger">Исключён из обучения</span>';
    if (row.review_status === "reviewed") return '<span class="badge text-bg-success">Проверен</span>';
    if (row.review_status === "automatic") return '<span class="badge text-bg-warning">Pipeline уже не использует</span>';
    return '<span class="badge text-bg-secondary">Ожидает проверки</span>';
  }

  function formatReviewTime(seconds) {
    if (!Number.isFinite(Number(seconds))) return "0:00";
    const value = Math.max(0, Math.floor(Number(seconds)));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
  }

  function renderDisputedTrack(row) {
    const active = state.previewTrackId === row.id;
    const confidence = row.confidence == null ? "" : `уверенность ${(Number(row.confidence) * 100).toFixed(1)}%`;
    const margin = row.margin == null ? "" : `отрыв ${(Number(row.margin) * 100).toFixed(1)} п.п.`;
    const reasons = (row.reasons || []).map((value) => reviewReasonLabel(value)).join(" · ");
    const savedOverride = String(row.override?.style_override || "");
    const styles = (state.labels?.base_styles || []).map((style) => `<option value="${escapeHtml(style)}" ${style === savedOverride ? "selected" : ""}>${escapeHtml(style)}</option>`).join("");
    const objective = row.objective_excluded
      ? '<div class="training-review-objective-note"><i class="bi bi-shield-check me-1"></i>Этот трек уже объективно не участвует по существующему pipeline; ручное исключение не требуется.</div>' : "";
    return `<article class="training-track-review-item ${active ? "is-playing" : ""}" data-track-id="${escapeHtml(row.id)}">
      <input class="form-check-input training-track-review-select" type="checkbox" data-review-select="${escapeHtml(row.id)}" ${state.disputedSelected.has(row.id) ? "checked" : ""} ${row.objective_excluded || row.review_status === "excluded" ? "disabled" : ""} aria-label="Выбрать трек">
      <button class="btn btn-sm btn-outline-primary training-track-preview-button" type="button" data-review-play="${escapeHtml(row.id)}" title="${active ? "Пауза или продолжение" : "Предпрослушать"}"><i class="bi bi-${active && state.previewPlaying ? "pause-fill" : "play-fill"}"></i></button>
      <div class="training-track-review-main">
        <div class="training-track-review-heading"><div class="min-w-0"><div class="training-track-review-name" title="${escapeHtml(row.filename)}">${escapeHtml(row.filename)}</div><div class="training-track-review-path" title="${escapeHtml(row.folder_path)}">${escapeHtml(row.folder_relative_path)}</div></div>${reviewStatusBadge(row)}</div>
        <div class="training-track-review-metrics"><span class="training-track-review-style-comparison"><span class="training-track-review-style-token"><strong>${escapeHtml(row.true_style || "—")}</strong><small>текущий стиль</small></span><span class="training-track-review-style-arrow" aria-hidden="true">→</span><span class="training-track-review-style-token"><strong>${escapeHtml(row.predicted_style || "—")}</strong><small>модель</small></span></span>${confidence ? `<span>${confidence}</span>` : ""}${margin ? `<span>${margin}</span>` : ""}<span>${escapeHtml(reasons)}</span></div>
        ${objective}
        ${row.objective_excluded ? "" : `<div class="training-track-review-actions">
          <button class="btn btn-sm btn-outline-success" type="button" data-review-action="keep" data-track-id="${escapeHtml(row.id)}">${row.review_status === "excluded" ? "Вернуть в обучение / подтвердить текущий стиль" : "Текущий стиль верен"}</button>
          <select class="form-select form-select-sm" data-review-style-select="${escapeHtml(row.id)}" aria-label="Новый стиль"><option value="" ${savedOverride ? "" : "selected"}>Другой стиль…</option>${styles}</select>
          <button class="btn btn-sm btn-outline-primary" type="button" data-review-action="style" data-track-id="${escapeHtml(row.id)}">Изменить стиль</button>
          <button class="btn btn-sm btn-outline-danger" type="button" data-review-action="exclude" data-track-id="${escapeHtml(row.id)}">Исключить только из обучения</button>
        </div>`}
        ${active ? `<div class="training-track-seek"><button class="btn btn-sm btn-outline-secondary" type="button" data-review-from-start="${escapeHtml(row.id)}">С начала</button><input id="training-review-seek" type="range" min="0" max="100" value="0" step="0.1" aria-label="Позиция предпрослушивания"><span id="training-review-seek-time" class="training-track-seek-time">0:00 / 0:00</span></div>` : ""}
      </div>
    </article>`;
  }

  function syncDisputedFilterOptions(data) {
    const styles = data.summary?.styles || [];
    const styleSelect = el("training-track-style");
    replaceProblemSelectOptions(styleSelect, styles.map((style) => ({value: style, label: style})), state.disputedStyle, "Все стили");
    const targets = data.summary?.confused_styles || [];
    if (state.disputedConfusedWith !== "all" && !targets.includes(state.disputedConfusedWith)) state.disputedConfusedWith = "all";
    replaceProblemSelectOptions(el("training-track-confused-with"), targets.map((value) => ({value, label: value})), state.disputedConfusedWith, "Все пары");
  }

  async function loadDisputedTracks(reset = false) {
    if (reset) state.disputedOffset = 0;
    const params = new URLSearchParams({
      offset: state.disputedOffset, limit: state.disputedLimit,
      style: state.disputedStyle, confused_with: state.disputedConfusedWith,
      status: state.disputedStatus, q: el("training-track-search").value.trim(),
    });
    if (state.disputedFolderId) params.set("folder_id", state.disputedFolderId);
    const data = await requestJson(`/api/training-dataset/disputed-tracks?${params}`);
    state.disputedItems = data.items || [];
    state.disputedTotal = Number(data.total || 0);
    if (data.settings) {
      state.reviewPreview = {
        mode: data.settings.review_preview_mode || "percent",
        percent: Number(data.settings.review_preview_percent ?? 30),
        seconds: Number(data.settings.review_preview_seconds ?? 60),
      };
      syncReviewPreviewSettings();
    }
    syncDisputedFilterOptions(data);
    el("training-review-track-count").textContent = number(data.summary?.total || state.disputedTotal);
    el("training-track-review-summary").textContent = `Найдено: ${number(state.disputedTotal)}. Ожидают проверки: ${number(data.summary?.pending)}; проверено: ${number(data.summary?.reviewed)}; исключено вручную: ${number(data.summary?.excluded)}; pipeline уже не использует: ${number(data.summary?.automatic)}.`;
    el("training-track-review-list").innerHTML = state.disputedItems.length
      ? state.disputedItems.map(renderDisputedTrack).join("")
      : '<div class="text-muted text-center py-4">Для выбранных фильтров спорных треков нет.</div>';
    bindDisputedTrackActions();
    updateDisputedSelectionUi();
    const start = state.disputedTotal ? state.disputedOffset + 1 : 0;
    const end = Math.min(state.disputedOffset + state.disputedItems.length, state.disputedTotal);
    el("training-track-page-text").textContent = `${number(start)}–${number(end)} из ${number(state.disputedTotal)}`;
    el("training-track-prev").disabled = state.disputedOffset <= 0;
    el("training-track-next").disabled = state.disputedOffset + state.disputedLimit >= state.disputedTotal;
  }

  async function showDisputedTracks(folderId = "", style = "all", confusedWith = "all", status = "all") {
    clearDisputedSelection();
    state.disputedFolderId = String(folderId || "");
    state.disputedStyle = String(style || "all");
    state.disputedConfusedWith = String(confusedWith || "all");
    state.disputedStatus = String(status || "all");
    el("training-track-status").value = state.disputedStatus;
    setReviewMode("tracks");
    await loadDisputedTracks(true);
    el("training-review-section").scrollIntoView({behavior: "smooth", block: "start"});
  }

  function stopReviewPreview() {
    const audio = el("training-review-audio");
    if (!audio) return;
    window.clearTimeout(state.previewSeekRestoreTimer);
    audio.pause();
    if (state.previewSeekActive) audio.muted = state.previewSeekWasMuted;
    audio.removeAttribute("src");
    audio.load();
    const previous = state.previewTrackId;
    state.previewTrackId = null;
    state.previewPlaying = false;
    state.previewNeedsInitialSeek = false;
    state.previewSeekActive = false;
    state.previewSeekDragging = false;
    if (previous && state.reviewMode === "tracks") loadDisputedTracks().catch(console.error);
  }

  function muteReviewPreviewForSeek(audio) {
    if (!audio || !state.previewTrackId) return;
    window.clearTimeout(state.previewSeekRestoreTimer);
    if (!state.previewSeekActive) state.previewSeekWasMuted = audio.muted;
    state.previewSeekActive = true;
    audio.muted = true;
  }

  function restoreReviewPreviewAfterSeek(audio, delay = 90) {
    if (!audio || !state.previewSeekActive || state.previewSeekDragging) return;
    window.clearTimeout(state.previewSeekRestoreTimer);
    state.previewSeekRestoreTimer = window.setTimeout(() => {
      if (audio.seeking || state.previewSeekDragging) {
        restoreReviewPreviewAfterSeek(audio, 90);
        return;
      }
      audio.muted = state.previewSeekWasMuted;
      state.previewSeekActive = false;
    }, delay);
  }

  function seekReviewPreview(audio, seconds, playAfter = false) {
    if (!audio || !Number.isFinite(Number(seconds))) return;
    muteReviewPreviewForSeek(audio);
    const maximum = Number.isFinite(audio.duration) ? audio.duration : Number(seconds);
    audio.currentTime = Math.max(0, Math.min(Number(seconds), maximum));
    if (playAfter) audio.play().catch((error) => alertMessage(`Предпрослушивание: ${error.message}`, "warning"));
    restoreReviewPreviewAfterSeek(audio, 140);
  }

  function toggleReviewPreview(trackId) {
    const row = state.disputedItems.find((item) => item.id === trackId);
    if (!row) return;
    const audio = el("training-review-audio");
    if (state.previewTrackId === trackId) {
      if (audio.paused) audio.play().catch((error) => alertMessage(error.message, "warning"));
      else audio.pause();
      return;
    }
    audio.pause();
    state.previewTrackId = trackId;
    state.previewPlaying = false;
    state.previewNeedsInitialSeek = true;
    audio.src = `/api/training-dataset/disputed-tracks/${encodeURIComponent(row.id)}/stream`;
    audio.load();
    el("training-track-review-list").innerHTML = state.disputedItems.map(renderDisputedTrack).join("");
    bindDisputedTrackActions();
    updateDisputedSelectionUi();
    updateReviewPreviewUi();
  }

  function currentDisputedFilterParams() {
    const params = new URLSearchParams({
      style: state.disputedStyle, confused_with: state.disputedConfusedWith,
      status: state.disputedStatus, q: el("training-track-search").value.trim(),
    });
    if (state.disputedFolderId) params.set("folder_id", state.disputedFolderId);
    return params;
  }

  function updateDisputedSelectionUi() {
    const count = state.disputedSelected.size;
    el("training-track-selected-count").textContent = `Выбрано: ${number(count)}${state.disputedFilteredSelection ? " по текущему фильтру" : ""}`;
    el("training-track-bulk-exclude").disabled = count === 0;
    const visible = state.disputedItems.filter((row) => !row.objective_excluded && row.review_status !== "excluded");
    const allVisible = visible.length > 0 && visible.every((row) => state.disputedSelected.has(row.id));
    el("training-track-select-page-check").checked = allVisible;
    document.querySelectorAll("[data-review-select]").forEach((input) => { input.checked = state.disputedSelected.has(input.dataset.reviewSelect); });
  }

  function clearDisputedSelection() {
    state.disputedSelected.clear();
    state.disputedFilteredSelection = false;
    updateDisputedSelectionUi();
  }

  function selectVisibleDisputed() {
    state.disputedItems.forEach((row) => {
      if (!row.objective_excluded && row.review_status !== "excluded") state.disputedSelected.add(row.id);
    });
    state.disputedFilteredSelection = false;
    updateDisputedSelectionUi();
  }

  async function selectFilteredDisputed() {
    const data = await requestJson(`/api/training-dataset/disputed-tracks/filtered-ids?${currentDisputedFilterParams()}`);
    state.disputedSelected = new Set(data.track_ids || []);
    state.disputedFilteredSelection = true;
    updateDisputedSelectionUi();
  }

  function exclusionImpactText(impact) {
    return (impact?.class_impact || []).map((row) => `${row.style}: было ${row.total}, исключается ${row.excluded}, останется ${Math.max(0, row.total - row.excluded)} (${row.percent}%)`).join("\n");
  }

  async function bulkExcludeDisputed() {
    const ids = [...state.disputedSelected];
    if (!ids.length) return;
    const impact = await requestJson("/api/training-dataset/disputed-tracks/exclusion-preview", {
      method: "POST", body: JSON.stringify({track_ids: ids}),
    });
    if (!window.confirm(`Исключить только из будущего обучения ${ids.length} треков?\n\n${exclusionImpactText(impact)}\n\nMP3 не удаляются и не перемещаются.`)) return;
    let result = await requestJson("/api/training-dataset/disputed-tracks/bulk-exclude", {
      method: "POST", body: JSON.stringify({track_ids: ids}),
    });
    if (result.confirmation_required) {
      if (!window.confirm(`${result.impact.message}\n\n${exclusionImpactText(result.impact)}\n\nПодтвердить значительное изменение?`)) return;
      result = await requestJson("/api/training-dataset/disputed-tracks/bulk-exclude", {
        method: "POST", body: JSON.stringify({track_ids: ids, confirm_large_change: true}),
      });
    }
    clearDisputedSelection();
    await refreshTrainingDatasetState({refreshTracks: true, refreshAssistant: true});
    alertMessage(`Исключено только из обучения: ${number(result.changed)}. Музыкальные файлы не изменены.`, "success");
  }

  async function refreshTrainingDatasetState({refreshTracks = false, refreshAssistant = false} = {}) {
    const tasks = [loadPlan(true)];
    if (state.reviewMode === "folders") tasks.push(loadFolders(true, true));
    else if (refreshTracks) tasks.push(loadDisputedTracks());
    if (!el("training-assistant-result").classList.contains("d-none")) tasks.push(loadPreparationAssistant());
    await Promise.all(tasks);
  }

  async function updateReviewTrack(trackId, action) {
    const currentItem = state.disputedItems.find((item) => item.id === trackId);
    const payload = {action};
    if (action === "style") {
      payload.style_override = document.querySelector(`[data-review-style-select="${CSS.escape(trackId)}"]`)?.value || "";
      if (!payload.style_override) return alertMessage("Выберите новый стиль.", "warning");
    }
    let result = await requestJson(`/api/training-dataset/disputed-tracks/${encodeURIComponent(trackId)}`, {
      method: "PATCH", body: JSON.stringify(payload),
    });
    if (result.confirmation_required) {
      const classRows = (result.impact?.class_impact || []).map((row) => `${row.style}: ${row.excluded} из ${row.total} (${row.percent}%)`).join("; ");
      if (!window.confirm(`${result.impact.message}\n${classRows}\nПродолжить?`)) return;
      result = await requestJson(`/api/training-dataset/disputed-tracks/${encodeURIComponent(trackId)}`, {
        method: "PATCH", body: JSON.stringify({...payload, confirm_large_change: true}),
      });
    }
    const impact = result.impact;
    const safeText = impact && !impact.requires_confirmation
      ? ` ${impact.folder_impact?.map((row) => `Будет исключено ${row.excluded} из ${row.total} треков папки — безопасно.`).join(" ")}` : "";
    alertMessage((action === "exclude" ? "Трек исключён только из обучения." : action === "style" ? "Стиль трека изменён для следующего обучения." : "Текущий стиль подтверждён.") + safeText, "success");
    state.disputedSelected.delete(trackId);
    await refreshTrainingDatasetState({refreshTracks: true, refreshAssistant: true});
    if (state.disputedFolderId && currentItem) {
      const folderQuery = `limit=1&folder_id=${encodeURIComponent(state.disputedFolderId)}`;
      const [allRows, pendingRows] = await Promise.all([
        requestJson(`/api/training-dataset/disputed-tracks?${folderQuery}&status=all`),
        requestJson(`/api/training-dataset/disputed-tracks?${folderQuery}&status=pending`),
      ]);
      const total = Number(allRows.total || 0);
      const remaining = Number(pendingRows.total || 0);
      alertMessage(`По этой папке обработано ${number(Math.max(0, total - remaining))} из ${number(total)}; осталось проверить ${number(remaining)}.`, "info");
    }
  }

  function bindDisputedTrackActions() {
    document.querySelectorAll("[data-review-play]").forEach((button) => button.addEventListener("click", () => toggleReviewPreview(button.dataset.reviewPlay)));
    document.querySelectorAll("[data-review-action]").forEach((button) => button.addEventListener("click", () => updateReviewTrack(button.dataset.trackId, button.dataset.reviewAction).catch((error) => alertMessage(error.message, "danger"))));
    document.querySelectorAll("[data-review-select]").forEach((input) => input.addEventListener("change", () => {
      if (input.checked) state.disputedSelected.add(input.dataset.reviewSelect); else state.disputedSelected.delete(input.dataset.reviewSelect);
      state.disputedFilteredSelection = false;
      updateDisputedSelectionUi();
    }));
    document.querySelectorAll("[data-review-from-start]").forEach((button) => button.addEventListener("click", () => {
      state.previewSeekDragging = false;
      seekReviewPreview(el("training-review-audio"), 0, true);
    }));
    const seek = el("training-review-seek");
    if (seek) {
      const audio = el("training-review-audio");
      const beginDragging = () => {
        state.previewSeekDragging = true;
        muteReviewPreviewForSeek(audio);
      };
      const finishDragging = () => {
        state.previewSeekDragging = false;
        restoreReviewPreviewAfterSeek(audio, 140);
      };
      seek.addEventListener("pointerdown", beginDragging);
      seek.addEventListener("input", () => {
        muteReviewPreviewForSeek(audio);
        if (Number.isFinite(audio.duration)) audio.currentTime = audio.duration * Number(seek.value) / 100;
      });
      seek.addEventListener("change", finishDragging);
      seek.addEventListener("pointerup", finishDragging);
      seek.addEventListener("pointercancel", finishDragging);
      seek.addEventListener("blur", finishDragging);
    }
  }

  function updateReviewPreviewUi() {
    const audio = el("training-review-audio");
    state.previewPlaying = Boolean(state.previewTrackId && !audio.paused);
    document.querySelectorAll("[data-review-play]").forEach((button) => {
      const active = button.dataset.reviewPlay === state.previewTrackId;
      button.innerHTML = `<i class="bi bi-${active && state.previewPlaying ? "pause-fill" : "play-fill"}"></i>`;
    });
    const seek = el("training-review-seek");
    const time = el("training-review-seek-time");
    if (seek && Number.isFinite(audio.duration) && audio.duration > 0) {
      seek.value = String((audio.currentTime / audio.duration) * 100);
    }
    if (time) time.textContent = `${formatReviewTime(audio.currentTime)} / ${formatReviewTime(audio.duration)}`;
  }

  function handleSelectPage(event) {
    document.querySelectorAll(".training-folder-check").forEach((checkbox) => {
      checkbox.checked = event.currentTarget.checked;
      if (checkbox.checked) state.selected.add(checkbox.dataset.folderId); else state.selected.delete(checkbox.dataset.folderId);
    });
    updateSelectedCount();
    updateFolderActions();
  }

  function treeRow(node) {
    const wrapper = document.createElement("div");
    const row = document.createElement("div");
    row.className = "training-tree-row";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn btn-sm btn-link p-0";
    toggle.innerHTML = node.children ? '<i class="bi bi-chevron-right"></i>' : '<i class="bi bi-dot"></i>';
    toggle.disabled = !node.children;
    const name = document.createElement("span");
    name.className = "training-tree-name";
    name.textContent = node.text;
    name.title = node.id;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "btn btn-sm btn-outline-primary py-0 px-1";
    add.title = "Добавить эту папку";
    add.innerHTML = '<i class="bi bi-plus-lg"></i>';
    const children = document.createElement("div");
    children.className = "training-tree-children d-none";
    row.append(toggle, name, add);
    wrapper.append(row, children);
    add.addEventListener("click", () => addSource(node.id));
    toggle.addEventListener("click", async () => {
      const opening = children.classList.contains("d-none");
      children.classList.toggle("d-none", !opening);
      toggle.innerHTML = `<i class="bi bi-chevron-${opening ? "down" : "right"}"></i>`;
      if (opening && !children.dataset.loaded) {
        children.innerHTML = '<div class="small text-muted p-2">Загрузка…</div>';
        try { await loadTree(node.id, children); } catch (error) { children.textContent = error.message; }
      }
    });
    return wrapper;
  }

  async function loadTree(parentId = "#", target = el("training-folder-tree")) {
    const data = await requestJson(`/get_directories?id=${encodeURIComponent(parentId)}`);
    target.innerHTML = "";
    if (!data.length) target.innerHTML = '<div class="small text-muted p-2">Вложенные папки не найдены.</div>';
    data.forEach((node) => target.appendChild(treeRow(node)));
    target.dataset.loaded = "1";
  }

  async function addSource(path) {
    if (!String(path || "").trim()) return alertMessage("Укажите папку.", "warning");
    try {
      const data = await requestJson("/api/training-dataset/sources", {
        method: "POST", body: JSON.stringify({path, recursive: true}),
      });
      renderSummary(data.summary);
      el("training-source-path").value = "";
      alertMessage("Источник добавлен. Теперь запустите предварительную разметку.", "success");
    } catch (error) { alertMessage(error.message, "danger"); }
  }

  function renderProgress(progress) {
    const running = ["starting", "running"].includes(progress.status);
    const bar = el("training-preview-progress");
    bar.style.width = running ? "100%" : (progress.status === "completed" ? "100%" : "0%");
    bar.classList.toggle("progress-bar-striped", running);
    bar.classList.toggle("progress-bar-animated", running);
    bar.classList.toggle("bg-danger", progress.status === "error");
    el("training-preview-text").textContent = progress.status === "error"
      ? `Ошибка: ${progress.error || "неизвестно"}`
      : running
        ? `Просмотрено папок: ${number(progress.processed)}; найдено папок с MP3: ${number(progress.folders)}; треков: ${number(progress.tracks)}`
        : progress.status === "completed"
          ? `Готово: ${number(progress.folders)} папок, ${number(progress.tracks)} MP3. Проверьте конфликты и подтвердите метки.`
          : (state.summary?.sources || []).length && !Number(state.summary?.folder_count || 0)
            ? "Источники сохранены, но кэш разметки пуст. Проверьте доступ к папкам и нажмите «Разметить папки»."
            : "Добавьте источники и запустите анализ структуры папок.";
    el("training-preview-start").disabled = running;
  }

  async function pollPreview() {
    window.clearTimeout(state.pollTimer);
    const data = await requestJson("/api/training-dataset/preview/status?t=" + Date.now());
    renderProgress(data.progress || {});
    renderSummary(data.summary || state.summary);
    if (["starting", "running"].includes(data.progress?.status)) {
      state.pollTimer = window.setTimeout(() => pollPreview().catch((error) => alertMessage(error.message, "danger")), 1000);
    } else {
      await loadFolders(true);
      await loadPlan();
    }
  }

  async function updateSelected(status) {
    const ids = selectedIds();
    if (!ids.length) return alertMessage("Сначала выберите папки в таблице.", "warning");
    const currentFilter = el("training-folder-status").value;
    if (status === "excluded" && !window.confirm(`Исключить выбранные папки (${ids.length}) из обучающей выборки? Файлы на диске не удаляются.`)) return;
    const taxonomy = {};
    const style = el("training-bulk-style").value;
    const language = el("training-bulk-language").value;
    const version = el("training-bulk-version").value;
    if (style) taxonomy.base_style = style;
    if (language) taxonomy.language = language;
    if (version) taxonomy.version_type = version;
    try {
      const data = await requestJson("/api/training-dataset/folders", {
        method: "PATCH", body: JSON.stringify({ids, status, taxonomy}),
      });
      renderSummary(data.summary);
      state.selected.clear();
      setTaxonomyEditing(false);
      let switchedToConfirmed = false;
      if (status === "confirmed" && !["all", "confirmed", "problematic"].includes(currentFilter)) {
        el("training-folder-status").value = "confirmed";
        state.offset = 0;
        switchedToConfirmed = true;
      }
      await refreshTrainingDatasetState({refreshAssistant: true});
      const message = status === "excluded"
        ? "Выбранные папки исключены из следующего обучения. Музыкальные файлы не удалены."
        : currentFilter === "excluded"
          ? "Выбранные папки возвращены в обучающую выборку со статусом «Подтверждено»."
          : `Выбранные папки подтверждены и будут участвовать в следующем обучении.${switchedToConfirmed ? " Открыт список подтверждённых — папки остаются видимыми." : ""}`;
      alertMessage(message, "success");
    } catch (error) { alertMessage(error.message, "danger"); }
  }

  let searchTimer = null;
  modal.addEventListener("show.bs.modal", () => {
    loadDataset().catch((error) => alertMessage(error.message, "danger"));
    loadTree().catch((error) => alertMessage(error.message, "danger"));
    pollTrainingStatus();
    pollQuickQuality();
  });
  modal.addEventListener("hidden.bs.modal", () => {
    window.clearTimeout(state.pollTimer);
    window.clearTimeout(state.quickQualityTimer);
    if (state.planController) state.planController.abort();
    stopReviewPreview();
  });
  el("training-review-folders-tab").addEventListener("click", () => {
    setReviewMode("folders");
    loadFolders(true, true).catch((error) => alertMessage(error.message, "danger"));
  });
  el("training-review-tracks-tab").addEventListener("click", () => {
    showDisputedTracks("", "all", "all").catch((error) => alertMessage(error.message, "danger"));
  });
  el("training-track-style").addEventListener("change", (event) => {
    state.disputedStyle = event.currentTarget.value || "all";
    state.disputedFolderId = "";
    clearDisputedSelection();
    loadDisputedTracks(true).catch((error) => alertMessage(error.message, "danger"));
  });
  el("training-track-confused-with").addEventListener("change", (event) => {
    state.disputedConfusedWith = event.currentTarget.value || "all";
    state.disputedFolderId = "";
    clearDisputedSelection();
    loadDisputedTracks(true).catch((error) => alertMessage(error.message, "danger"));
  });
  el("training-track-status").addEventListener("change", (event) => {
    state.disputedStatus = event.currentTarget.value || "all";
    clearDisputedSelection();
    loadDisputedTracks(true).catch((error) => alertMessage(error.message, "danger"));
  });
  let trackSearchTimer = null;
  el("training-track-search").addEventListener("input", () => {
    clearDisputedSelection();
    window.clearTimeout(trackSearchTimer);
    trackSearchTimer = window.setTimeout(() => loadDisputedTracks(true).catch((error) => alertMessage(error.message, "danger")), 300);
  });
  el("training-track-refresh").addEventListener("click", () => loadDisputedTracks().catch((error) => alertMessage(error.message, "danger")));
  el("training-track-select-page-check").addEventListener("change", (event) => {
    if (event.currentTarget.checked) selectVisibleDisputed(); else clearDisputedSelection();
  });
  el("training-track-select-visible").addEventListener("click", selectVisibleDisputed);
  el("training-track-select-filtered").addEventListener("click", () => selectFilteredDisputed().catch((error) => alertMessage(error.message, "danger")));
  el("training-track-clear-selection").addEventListener("click", clearDisputedSelection);
  el("training-track-bulk-exclude").addEventListener("click", () => bulkExcludeDisputed().catch((error) => alertMessage(error.message, "danger")));
  el("training-track-prev").addEventListener("click", () => {
    state.disputedOffset = Math.max(0, state.disputedOffset - state.disputedLimit);
    stopReviewPreview();
    loadDisputedTracks().catch(console.error);
  });
  el("training-track-next").addEventListener("click", () => {
    state.disputedOffset += state.disputedLimit;
    stopReviewPreview();
    loadDisputedTracks().catch(console.error);
  });
  el("training-preview-mode").addEventListener("change", (event) => {
    state.reviewPreview.mode = event.currentTarget.value === "time" ? "time" : "percent";
    syncReviewPreviewSettings();
  });
  el("training-preview-save").addEventListener("click", async () => {
    state.reviewPreview = {
      mode: el("training-preview-mode").value === "time" ? "time" : "percent",
      percent: Math.max(0, Math.min(95, Number(el("training-preview-percent").value || 30))),
      seconds: Math.max(0, Math.min(3600, Number(el("training-preview-seconds").value || 60))),
    };
    try {
      await requestJson("/api/training-dataset/settings?plan=0", {
        method: "PATCH",
        body: JSON.stringify({
          review_preview_mode: state.reviewPreview.mode,
          review_preview_percent: state.reviewPreview.percent,
          review_preview_seconds: state.reviewPreview.seconds,
        }),
      });
      syncReviewPreviewSettings();
      alertMessage("Позиция предпрослушивания сохранена.", "success");
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  const reviewAudio = el("training-review-audio");
  reviewAudio.addEventListener("loadedmetadata", () => {
    if (state.previewNeedsInitialSeek && Number.isFinite(reviewAudio.duration)) {
      const initialTime = state.reviewPreview.mode === "time"
        ? Math.min(state.reviewPreview.seconds, Math.max(0, reviewAudio.duration - 0.25))
        : reviewAudio.duration * state.reviewPreview.percent / 100;
      state.previewNeedsInitialSeek = false;
      seekReviewPreview(reviewAudio, initialTime, true);
    }
    updateReviewPreviewUi();
  });
  reviewAudio.addEventListener("seeked", () => restoreReviewPreviewAfterSeek(reviewAudio));
  reviewAudio.addEventListener("play", updateReviewPreviewUi);
  reviewAudio.addEventListener("pause", updateReviewPreviewUi);
  reviewAudio.addEventListener("timeupdate", updateReviewPreviewUi);
  reviewAudio.addEventListener("durationchange", updateReviewPreviewUi);
  reviewAudio.addEventListener("ended", updateReviewPreviewUi);
  reviewAudio.addEventListener("error", () => {
    if (state.previewTrackId) alertMessage("Не удалось открыть аудио для предпрослушивания.", "warning");
  });
  el("training-tree-reset").addEventListener("click", () => loadTree().catch((error) => alertMessage(error.message, "danger")));
  el("training-source-add-path").addEventListener("click", () => addSource(el("training-source-path").value));
  el("training-source-path").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addSource(event.currentTarget.value); } });
  el("training-preview-start").addEventListener("click", async () => {
    try {
      await requestJson("/api/training-dataset/preview", {method: "POST", body: "{}"});
      await pollPreview();
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-confirm-high").addEventListener("click", async () => {
    if (!window.confirm("Подтвердить только однозначные предложения с уверенностью не ниже 85%? Конфликты и общие папки останутся на проверке.")) return;
    try {
      const data = await requestJson("/api/training-dataset/confirm-high", {method: "POST", body: JSON.stringify({min_confidence: 0.85})});
      renderSummary(data.summary);
      const switchedToConfirmed = el("training-folder-status").value === "suggested";
      if (switchedToConfirmed) el("training-folder-status").value = "confirmed";
      await loadFolders(true);
      await loadPlan();
      alertMessage(`Подтверждено папок: ${number(data.changed)}.${switchedToConfirmed ? " Открыт список подтверждённых." : ""}`, "success");
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-assistant-analyze").addEventListener("click", () => loadPreparationAssistant().catch((error) => alertMessage(error.message, "danger")));
  el("training-assistant-show-problems").addEventListener("click", () => {
    showProblemFolders().catch((error) => alertMessage(error.message, "danger"));
  });
  el("training-assistant-keep").addEventListener("click", () => {
    state.assistantPreview = null;
    state.assistantConfirmed = false;
    el("training-assistant-result").classList.add("d-none");
    alertMessage("Текущая выборка оставлена без изменений.", "info");
  });
  el("training-assistant-apply").addEventListener("click", async () => {
    const preview = state.assistantPreview;
    if (!preview || !state.assistantConfirmed) return alertMessage("Сначала сформируйте и подтвердите preview.", "warning");
    const ids = preview.safe_folder_ids || [];
    if (!ids.length) return alertMessage("В плане нет безопасных папок для исключения.", "info");
    if (!window.confirm(`Исключить из следующего обучения ${ids.length} перечисленных папок? Музыкальные файлы не удаляются.`)) return;
    try {
      const result = await requestJson("/api/training-dataset/preparation-assistant/apply", {
        method: "POST",
        body: JSON.stringify({folder_ids: ids, preview_token: preview.preview_token}),
      });
      renderSummary(result.summary || state.summary);
      await Promise.all([loadFolders(true), loadPlan(), loadPreparationAssistant()]);
      alertMessage(result.message || "Рекомендации применены.", "success");
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-folder-status").addEventListener("change", () => {
    setTaxonomyEditing(false);
    if (el("training-folder-status").value === "problematic") {
      state.problemFilter = "attention";
      updateProblemFilterButtons();
    }
    loadFolders(true).catch((error) => alertMessage(error.message, "danger"));
  });
  document.querySelectorAll(".training-problem-filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.problemFilter = button.dataset.problemFilter || "all";
      updateProblemFilterButtons();
      state.offset = 0;
      loadFolders().catch((error) => alertMessage(error.message, "danger"));
    });
  });
  el("training-problem-sort").addEventListener("change", (event) => {
    state.problemSort = event.currentTarget.value || "risk";
    state.folderSortExplicit = false;
    state.offset = 0;
    loadFolders().catch((error) => alertMessage(error.message, "danger"));
  });
  const folderStyleFilter = el("training-folder-style");
  if (folderStyleFilter) folderStyleFilter.addEventListener("change", (event) => {
      state.folderStyle = event.currentTarget.value || "all";
      if (el("training-folder-status").value === "problematic") {
        state.problemStyle = state.folderStyle;
        state.problemConfusedWith = "all";
      }
      state.offset = 0;
      loadFolders().catch((error) => alertMessage(error.message, "danger"));
    });
  const folderTracksFilter = el("training-folder-tracks");
  if (folderTracksFilter) folderTracksFilter.addEventListener("change", (event) => {
      state.folderTrackRange = event.currentTarget.value || "all";
      state.offset = 0;
      loadFolders().catch((error) => alertMessage(error.message, "danger"));
    });
  el("training-problem-confused-with").addEventListener("change", (event) => {
    state.problemConfusedWith = event.currentTarget.value || "all";
    state.offset = 0;
    loadFolders().catch((error) => alertMessage(error.message, "danger"));
  });
  el("training-select-high-risk").addEventListener("click", () => {
    state.problemItems.filter((row) => row.risk === "high").forEach((row) => state.selected.add(row.id));
    document.querySelectorAll(".training-folder-check").forEach((checkbox) => {
      checkbox.checked = state.selected.has(checkbox.dataset.folderId);
    });
    updateSelectedCount();
    updateFolderActions(true);
  });
  el("training-folder-search").addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadFolders(true).catch((error) => alertMessage(error.message, "danger")), 300);
  });
  el("training-folder-refresh").addEventListener("click", () => loadFolders(true).catch((error) => alertMessage(error.message, "danger")));
  el("training-plan-refresh").addEventListener("click", () => loadPlan(true).catch((error) => alertMessage(error.message, "danger")));
  el("training-use-builder").addEventListener("change", async (event) => {
    try {
      await saveTrainingSources(
        {use_dataset_builder: event.currentTarget.checked},
        `Подтверждённые папки ${event.currentTarget.checked ? "включены" : "отключены"}.`,
      );
    } catch (error) {
      event.currentTarget.checked = !event.currentTarget.checked;
      alertMessage(error.message, "danger");
    }
  });
  el("training-use-samples").addEventListener("change", async (event) => {
    try {
      await saveTrainingSources(
        {use_reference_samples: event.currentTarget.checked},
        `Эталонная выборка ${event.currentTarget.checked ? "включена" : "отключена"}.`,
      );
    } catch (error) {
      event.currentTarget.checked = !event.currentTarget.checked;
      alertMessage(error.message, "danger");
    }
  });
  el("training-use-rekordbox").addEventListener("change", async (event) => {
    try {
      await saveTrainingSources(
        {use_rekordbox_training: event.currentTarget.checked},
        `Rekordbox ${event.currentTarget.checked ? "включён" : "отключён"} для жанрового обучения.`,
      );
    } catch (error) {
      event.currentTarget.checked = !event.currentTarget.checked;
      alertMessage(error.message, "danger");
    }
  });
  el("training-samples-path-save").addEventListener("click", async () => {
    try {
      await saveTrainingSources(
        {reference_samples_path: el("training-samples-path").value.trim()},
        "Путь эталонной выборки сохранён.",
      );
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-samples-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      el("training-samples-path-save").click();
    }
  });
  ["training-quick-quality", "training-dataset-quick-quality"].forEach((id) => {
    const button = el(id);
    if (button) button.addEventListener("click", startQuickQuality);
  });
  el("training-enable-ready").addEventListener("click", async () => {
    const excluded = new Set(state.plan?.excluded_styles || []);
    (state.plan?.rows || []).forEach((row) => {
      if (!row.mandatory_excluded && Number(row.candidate_tracks || 0) >= Number(row.minimum_required || 200)) {
        excluded.delete(row.style);
      }
    });
    try { await saveStyleSelection(excluded, "Все пригодные стили включены."); }
    catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-disable-weak").addEventListener("click", async () => {
    const excluded = new Set(state.plan?.excluded_styles || []);
    (state.plan?.rows || []).forEach((row) => {
      if (row.mandatory_excluded || Number(row.candidate_tracks || 0) < Number(row.minimum_required || 200)) {
        excluded.add(row.style);
      }
    });
    try { await saveStyleSelection(excluded, "Слабые и служебные стили отключены."); }
    catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-style-limit").addEventListener("change", async (event) => {
    try {
      const data = await requestJson("/api/training-dataset/settings", {
        method: "PATCH", body: JSON.stringify({max_tracks_per_style: Number(event.currentTarget.value)}),
      });
      renderPlan(data.plan);
      alertMessage(`Лимит сохранён: ${number(data.settings.max_tracks_per_style)} треков на стиль.`, "success");
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  const initialSelectPage = el("training-select-page");
  if (initialSelectPage) initialSelectPage.addEventListener("change", handleSelectPage);
  el("training-edit-taxonomy").addEventListener("click", () => setTaxonomyEditing(!state.editingTaxonomy));
  const initialKeepButton = el("training-bulk-keep");
  if (initialKeepButton) {
    initialKeepButton.addEventListener("click", () => {
      state.selected.clear();
      document.querySelectorAll(".training-folder-check").forEach((checkbox) => { checkbox.checked = false; });
      const selectPage = el("training-select-page");
      if (selectPage) selectPage.checked = false;
      updateSelectedCount();
      updateFolderActions();
      alertMessage("Выделение снято; данные папок не изменены.", "info");
    });
  }
  el("training-bulk-confirm").addEventListener("click", () => updateSelected("confirmed"));
  el("training-bulk-exclude").addEventListener("click", () => updateSelected("excluded"));
  el("training-page-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadFolders().catch(console.error); });
  el("training-page-next").addEventListener("click", () => { state.offset += state.limit; loadFolders().catch(console.error); });
  el("training-rf-stop").addEventListener("click", async () => {
    if (!window.confirm("Остановить текущее обучение? Уже рассчитанные аудиопризнаки сохранятся для следующего запуска, рабочая модель останется без изменений.")) return;
    try {
      const result = await requestJson("/stop_training", {method: "POST", body: "{}"});
      alertMessage(result.message || "Запрос на остановку отправлен.", "warning");
      await pollTrainingStatus();
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  el("training-dataset-retrain").addEventListener("click", async () => {
    if (!state.summary?.confirmed_tracks) return alertMessage("Сначала подтвердите размеченные папки.", "warning");
    const readyStyles = (state.plan?.rows || []).filter((row) => row.readiness === "ready");
    if (readyStyles.length < 2) return alertMessage("Для обучения нужно включить минимум два стиля, в каждом не меньше установленного минимума.", "warning");
    const selectedTotal = state.plan?.selected_total ?? state.summary.confirmed_tracks;
    if (!window.confirm(`Запустить обучение RF примерно на ${number(selectedTotal)} отобранных треках? Лимит применяется отдельно к каждому стилю до анализа аудио. Рабочая модель сменится только после quality gate.`)) return;
    try {
      const result = await requestJson("/retrain?force=1", {method: "POST", body: "{}"});
      alertMessage(result.status || "Обучение запущено.", "success");
      state.trainingWasRunning = true;
      await pollTrainingStatus();
    } catch (error) { alertMessage(error.message, "danger"); }
  });
  window.refreshTrainingProgress = pollTrainingStatus;
  pollTrainingStatus();
})();
