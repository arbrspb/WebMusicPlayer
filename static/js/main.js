// main.js 14-08-25 01-50
// Включить/выключить логи через переменную
window.ENABLE_LOGS = true; // true — включено, false — отключено

if (!window.ENABLE_LOGS) {
  ["log", "warn", "error", "info", "debug"].forEach(function(method) {
    console[method] = function() {};
  });
}

let lastTrackStatus = null;

function updatePlayPauseButton(status = lastTrackStatus) {
  const button = document.getElementById("pause_button");
  if (!button) return;
  const isPlaying = status === "playing";
  const icon = button.querySelector("i");
  const label = button.querySelector("span");
  if (icon) icon.className = isPlaying ? "bi bi-pause-fill" : "bi bi-play-fill";
  if (label) label.textContent = isPlaying ? "Пауза" : "Играть";
  button.title = isPlaying ? "Пауза" : "Играть";
  button.setAttribute("aria-label", button.title);
}
// Глобальная переменная для режима воспроизведения
let mode = "playlist"; // Возможные значения: "playlist", "single", "random", и т.д.

// ===== Энергетическая волна / анимация (ГЛОБАЛЬНО!) начало 3-х функций =====
function drawEnergyWave(strength = 1) {
  const canvas = document.getElementById('energyWave');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const colors = ['rgba(255,0,200,0.8)','rgba(120,255,0,0.6)','rgba(255,255,0,0.5)','rgba(200,0,50,0.3)'];
  const time = Date.now() / (strength === 1 ? 240 : 100);

  for (let i = 0; i < colors.length; i++) {
    ctx.save();
    ctx.globalAlpha = 0.7 + 0.3*Math.sin(time/40 + i);
    ctx.beginPath();
    const radius = (w/3) + Math.sin(time/30 + i*2)*80*strength;
    ctx.arc(w/2 + Math.sin(time/50 + i)*100, h/2 + Math.cos(time/70 + i)*90, radius, 0, Math.PI*2);
    ctx.fillStyle = colors[i];
    ctx.shadowColor = colors[i];
    ctx.shadowBlur = 100*strength;
    ctx.fill();
    ctx.restore();
  }
}

let targetWaveStrength = 1; // 1 — пауза, 2 — проигрывание
let currentWaveStrength = 1; // для плавной анимации

function animateEnergyWave() {
  // Плавное приближение currentWaveStrength к targetWaveStrength
  const speed = 0.05; // скорость плавности (чем меньше, тем плавнее)
  currentWaveStrength += (targetWaveStrength - currentWaveStrength) * speed;
  drawEnergyWave(currentWaveStrength);
  requestAnimationFrame(animateEnergyWave);
}

// Вместо прямой смены waveStrength — меняй только targetWaveStrength!
function setEnergyWaveStatus(isPlaying) {
  targetWaveStrength = isPlaying ? 2 : 1;
  const canvas = document.getElementById('energyWave');
  if (canvas) {
    canvas.classList.toggle('energyWave-active', isPlaying);
    canvas.classList.toggle('energyWave-paused', !isPlaying);
  }
}
// ===== Энергетическая волна / анимация (ГЛОБАЛЬНО!) конец 3-х функций =====

// Функция для изменения режима
function setMode(newMode) {
  mode = newMode; // Обновляем глобальную переменную
  //console.log(`[ДИАГНОСТИКА MODE] Режим изменен на: ${mode}`);
}

// ========== Смена темы ==========
function applyTheme() {
  var selected = document.querySelector('input[name="themeOption"]:checked').value;
  localStorage.setItem("selectedTheme", selected);
  if (selected === "dark") {
    document.body.classList.add("dark-theme");
  } else {
    document.body.classList.remove("dark-theme");
  }
  postToSmartCatalog("catalog-theme", {theme: selected});
  // Работа с анимацией
  var energyAnimToggle = document.getElementById("energyAnimationToggle");
  var energyAnimChecked = energyAnimToggle ? energyAnimToggle.checked : false;
  localStorage.setItem("energyAnimationEnabled", energyAnimChecked ? "1" : "0");
  var canvas = document.getElementById("energyWave");
  if (canvas) {
    canvas.style.display = energyAnimChecked ? "block" : "none";
  }
  var modalInstance = bootstrap.Modal.getInstance(document.getElementById("themeModal"));
  modalInstance.hide();
}
document.addEventListener("DOMContentLoaded", function(){
  // --- Смена темы при загрузке ---
  var storedTheme = localStorage.getItem("selectedTheme") || "light";
  if (storedTheme === "dark"){
    document.getElementById("darkTheme").checked = true;
    document.body.classList.add("dark-theme");
  } else {
    document.getElementById("lightTheme").checked = true;
    document.body.classList.remove("dark-theme");
  }

  // --- Энергетическая волна / анимация ---
  const canvas = document.getElementById("energyWave");
  const energyAnimToggle = document.getElementById("energyAnimationToggle");

  // Синхронизируем чекбокс с localStorage
  let animationEnabled = (localStorage.getItem("energyAnimationEnabled") !== "0");
  if (energyAnimToggle) {
    energyAnimToggle.checked = animationEnabled;
//    energyAnimToggle.addEventListener("change", function() {
//      localStorage.setItem("energyAnimationEnabled", this.checked ? "1" : "0");
//      if (canvas) {
//        canvas.style.display = this.checked ? "block" : "none";
//      }
//    });
  }
  // canvas видимость и запуск анимации
  if (canvas) {
    canvas.style.display = animationEnabled ? "block" : "none";
    animateEnergyWave();
  }
    // Автопроигрывание: выставить правильный radio сразу при загрузке
  let storedAutoplay = localStorage.getItem("autoplayMode") || window.playerConfig?.autoplayMode || "off";
  document.querySelectorAll('input[name="autoplayModeOption"]').forEach(el => {
    el.checked = (el.value === storedAutoplay);
  });
  // === ИНИЦИАЛИЗАЦИЯ ГРОМКОСТИ ===
  let storedVolume = localStorage.getItem("currentVolume");
  let defaultVolume = window.playerConfig?.defaultVolume ?? 100;
  let slider = document.getElementById("volumeSlider");
  let display = document.getElementById("volume_display");

  if (slider) {
    if (storedVolume !== null) {
      window.currentVolume = Number(storedVolume);
    } else {
      window.currentVolume = Number(defaultVolume);
      localStorage.setItem("currentVolume", window.currentVolume);
    }
    slider.value = window.currentVolume;
    if (display) display.innerText = "Громкость: " + window.currentVolume + "%";
    if (playbackMode === "host") setVolumeHost(window.currentVolume);
    else if (playbackMode === "plyr") setVolumePlyr(window.currentVolume);

    // Только UI и localStorage — для плавного отклика
    slider.addEventListener('input', function() {
      window.currentVolume = Number(slider.value);
      localStorage.setItem("currentVolume", window.currentVolume);
      if (display) display.innerText = "Громкость: " + window.currentVolume + "%";
    });
    // Отправка на сервер — только после отпускания мыши
    slider.addEventListener('change', function() {
      window.currentVolume = Number(slider.value);
      if (playbackMode === "host") setVolumeHost(window.currentVolume);
      else if (playbackMode === "plyr") setVolumePlyr(window.currentVolume);
    });
  }
});

// ================== Глобальные переменные и плейлист ==================
const playbackMode = window.playerConfig.playbackMode; // host или plyr
const playlist = window.playerConfig.playlist;
const currentPath = window.playerConfig.currentPath;
let currentIndex = 0;
window.playerPlyr = null;
let isSeeking = false;
let seekPreviewValue = null;

// --- SEEK SLIDER с плавностью и защитой от скачков ---
document.addEventListener("DOMContentLoaded", function() {
  const seekSlider = document.getElementById("seekSlider");
  if (!seekSlider) return;

  seekSlider.addEventListener("mousedown", () => { isSeeking = true; });
  seekSlider.addEventListener("touchstart", () => { isSeeking = true; });

  seekSlider.addEventListener("input", function() {
    seekPreviewValue = Number(this.value);
    const dur = Number(this.max) || 0;
    document.getElementById("time_display").innerText = formatTime(seekPreviewValue) + " / " + formatTime(dur);
  });

  seekSlider.addEventListener("mouseup", doSeekOnRelease);
  seekSlider.addEventListener("touchend", doSeekOnRelease);

  function doSeekOnRelease() {
    if (seekPreviewValue !== null) {
      if (playbackMode === "host") {
        seekHost(seekPreviewValue);
      } else if (playbackMode === "plyr" && window.playerPlyr) {
        window.playerPlyr.currentTime = seekPreviewValue;
      }
      seekPreviewValue = null;
    }
    isSeeking = false;
    // Мгновенно обновить ползунок после отпускания
    if (playbackMode === "host") updateStatusHost();
    else if (playbackMode === "plyr") updateStatusPlyr();
  }
});

// ========== Функция форматирования времени ==========
function formatTime(seconds) {
  const totalSec = Math.floor(seconds);
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  return mins + ":" + (secs < 10 ? "0" : "") + secs;
}

// ========== HOST (VLC) ==========
function playTrackHost(trackPath) {
  logPlayCommand(trackPath, "playTrackHost"); // Логирование команды воспроизведения

  //console.log(`[ДИАГНОСТИКА PLAY] Попытка воспроизведения трека: ${trackPath}, текущий статус: ${lastTrackStatus}`);
  console.debug("[ДИАГНОСТИКА DEBUG] playTrackHost вызывается с:", trackPath);

  // Сбрасываем статус перед запуском нового трека
  lastTrackStatus = null;

  fetch('/play?path=' + encodeURIComponent(trackPath))
    .then(response => {
      if (response.status === 404) {
        showTrackNotFoundModal(trackPath, "playlist");
        throw new Error("not_found");
      }
      return response.json();
    })
    .then(data => {
      if (data.track) {
        let trackName = data.title || (data.track ? data.track.split(/[/\\]+/).pop() : "Неизвестный трек");
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        //console.log(`[ДИАГНОСТИКА PLAY] Успешно начато воспроизведение трека: ${trackName}`);
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        lastTrackStatus = "playing"; // Обновляем статус
        updatePlayPauseButton("playing");
      } else {
        console.warn("[ДИАГНОСТИКА PLAY] Ответ сервера не содержит информации о треке.");
      }

      if (window.currentVolume !== undefined) {
        setTimeout(function(){
          //console.log(`[ДИАГНОСТИКА VOLUME] Установка громкости: ${window.currentVolume}`);
          setVolumeHost(window.currentVolume);
        }, 500);
      }
    })
    .catch(err => {
      if (err.message !== "not_found") {
        console.error(`[ДИАГНОСТИКА PLAY] Ошибка воспроизведения трека: ${err}`);
      }
    });
}

function pauseTrackHost() {
  fetch('/pause')
    .then(response => {
      if (response.status === 403) {
        alert("Этот трек был запущен с другого устройства. Чтобы управлять воспроизведением с этого устройства, перезапустите трек (нажмите Play).");
        return Promise.reject('Forbidden');
      }
      return response.json();
    })
    .then(data => {
      if (data && data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Пауза: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        const ct = (data.current_time || 0) / 1000;
        const dur = (data.duration || 0) / 1000;
        document.getElementById("seekSlider").max = dur;
        document.getElementById("seekSlider").value = ct;
        document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
        lastTrackStatus = "paused"; // Устанавливаем статус
        updatePlayPauseButton("paused");
      }
    })
    .catch(err => {
      if (err !== 'Forbidden') console.log(err);
    });
}

function resumeTrackHost() {
  fetch('/resume')
    .then(response => {
      if (response.status === 403) {
        alert("Этот трек был запущен с другого устройства. Чтобы управлять воспроизведением с этого устройства, перезапустите трек (нажмите Play).");
        return Promise.reject('Forbidden');
      }
      return response.json();
    })
    .then(data => {
      if (data && data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        const ct = (data.current_time || 0) / 1000;
        const dur = (data.duration || 0) / 1000;
        document.getElementById("seekSlider").max = dur;
        document.getElementById("seekSlider").value = ct;
        document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
        lastTrackStatus = "playing"; // Устанавливаем статус
        updatePlayPauseButton("playing");
      }
    })
    .catch(err => {
      if (err !== 'Forbidden') console.log(err);
    });
}

function nextTrackHost() {
  fetch('/next')
    .then(response => {
      if (response.status === 403) {
        alert("Этот трек был запущен с другого устройства. Чтобы управлять воспроизведением с этого устройства, перезапустите трек (нажмите Play).");
        return Promise.reject('Forbidden');
      }
      return response.json();
    })
    .then(data => {
      if (data && data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
      }
    })
    .catch(err => {
      if (err !== 'Forbidden') console.log(err);
    });
}

function prevTrackHost() {
  fetch('/prev')
    .then(response => {
      if (response.status === 403) {
        alert("Этот трек был запущен с другого устройства. Чтобы управлять воспроизведением с этого устройства, перезапустите трек (нажмите Play).");
        return Promise.reject('Forbidden');
      }
      return response.json();
    })
    .then(data => {
      if (data && data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
      }
    })
    .catch(err => {
      if (err !== 'Forbidden') console.log(err);
    });
}

function setHostVolumeWithRetry(vol, attempts) {
  if (attempts <= 0) return;
  fetch('/volume', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ volume: vol })
  })
    .then(response => {
      if (!response.ok) {
        setTimeout(() => setHostVolumeWithRetry(vol, attempts - 1), 500);
      } else {
        response.json().then(data => {});
      }
    })
    .catch(err => {
      setTimeout(() => setHostVolumeWithRetry(vol, attempts - 1), 500);
    });
}

function setVolumeHost(vol) {
  fetch('/volume', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ volume: vol })
  })
    .then(response => response.json())
    .catch(err => console.log(err));
}

function setVolumePlyr(vol) {
  if (window.playerPlyr) window.playerPlyr.volume = vol / 100;
}

function seekHost(newTimeSec) {
  fetch('/seek', {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ time: newTimeSec * 1000 })
  })
    .then(response => {
      if (response.status === 403) {
        alert("Этот трек был запущен с другого устройства. Чтобы управлять воспроизведением с этого устройства, перезапустите трек (нажмите Play).");
        return Promise.reject('Forbidden');
      }
      return response.json();
    })
    .catch(err => {
      if (err !== 'Forbidden') console.log(err);
    });
}

// ========== PLYR ==========
function resumeTrackPlyr() {
  if (window.playerPlyr && typeof window.playerPlyr.play === "function") {
    window.playerPlyr.play();
  } else {
    let audioElem = document.getElementById("audioPlayerPlyr");
    if (audioElem) audioElem.play();
  }
  document.getElementById("now_playing").innerText = "Сейчас играет: " + (window.currentTrackTitle || "Трек");
  document.getElementById("genre").innerText = window.currentTrackGenre || "N/A";
  lastTrackStatus = "playing";
  updatePlayPauseButton("playing");
}

function playTrackPlyr(trackPath) {
  fetch('/play?path=' + encodeURIComponent(trackPath))
    .then(response => {
      if (response.status === 404) {
        showTrackNotFoundModal(trackPath, "playlist");
        throw new Error("not_found");
      }
      return response.json();
    })
    .then(data => {
      let fileName = data.title || (data.track ? data.track.split(/[/\\]+/).pop() : trackPath.split(/[/\\]+/).pop());
      fileName = decodeURIComponent(fileName).replace(/&amp;/g, '&');
      window.currentTrackTitle = fileName;
      window.currentTrackGenre = (data.genre && data.genre.toLowerCase() !== "unknown") ? data.genre : "N/A";
      document.getElementById("now_playing").innerText = "Сейчас играет: " + window.currentTrackTitle;
      document.getElementById("genre").innerText = window.currentTrackGenre;
      lastTrackStatus = "playing";
      updatePlayPauseButton("playing");
      if (data.play_url) {
        window.playerPlyr.source = {
          type: 'audio',
          title: fileName,
          sources: [{ src: data.play_url, type: 'audio/mp3' }]
        };
        window.playerPlyr.play();
      }
      if (window.currentVolume !== undefined) {
        setVolumePlyr(window.currentVolume);
      }
      const newIndex = playlist.indexOf(trackPath);
      if (newIndex !== -1) currentIndex = newIndex;
    })
    .catch(err => {
      if (err.message !== "not_found") {
        console.log(err);
      }
    });
}

function updateStatusPlyr() {
  if (playerPlyr) {
    const ct = playerPlyr.currentTime || 0;
    const dur = playerPlyr.duration || 0;
    if (!isSeeking && document.getElementById("seekSlider")) {
      let seekSlider = document.getElementById("seekSlider");
      seekSlider.max = dur;
      seekSlider.value = ct;
      document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
    }
  }
}

function seekPlyr(newTimeSec) {
  if (playerPlyr) playerPlyr.currentTime = newTimeSec;
}
function pauseTrackPlyr() {
  if (window.playerPlyr && typeof window.playerPlyr.pause === "function") {
    window.playerPlyr.pause();
  } else {
    let audioElem = document.getElementById("audioPlayerPlyr");
    if (audioElem) audioElem.pause();
  }
  document.getElementById("genre").innerText = window.currentTrackGenre || "N/A";
  document.getElementById("now_playing").innerText = "Пауза: " + (window.currentTrackTitle || "Трек");
  let currentTime = window.playerPlyr ? window.playerPlyr.currentTime || 0 : 0;
  let duration = window.playerPlyr ? window.playerPlyr.duration || 0 : 0;
  document.getElementById("seekSlider").max = duration;
  document.getElementById("seekSlider").value = currentTime;
  document.getElementById("time_display").innerText = formatTime(currentTime) + " / " + formatTime(duration);
  lastTrackStatus = "paused";
  updatePlayPauseButton("paused");
}

// ========== Единая функция воспроизведения ==========
function playTrack(trackPath) {
  logPlayCommand(trackPath, "playTrack"); // ДИАГНОСТИКА (целая строка)
  updateNowPlayingFavoriteButton(trackPath);
  if (playbackMode === "host") {
    playTrackHost(trackPath);
  } else if (playbackMode === "plyr") {
    playTrackPlyr(trackPath);
  }
}

// ========== Обновление статуса ==========
function updateTaxonomyDisplay(taxonomy) {
  const target = document.getElementById("track-taxonomy");
  if (!target) return;
  if (!taxonomy || !taxonomy.base_genre) {
    target.innerText = "";
    target.style.display = "none";
    return;
  }
  const parts = ["Стиль: " + taxonomy.base_genre];
  if (taxonomy.language && taxonomy.language !== "Unknown") {
    parts.push("язык: " + taxonomy.language);
  }
  if (taxonomy.language_source) {
    parts.push("источник языка: " + taxonomy.language_source);
  }
  if (taxonomy.version_type && taxonomy.version_type !== "Unknown") {
    parts.push("версия: " + taxonomy.version_type);
  }
  if (taxonomy.mood) parts.push("настроение: " + taxonomy.mood);
  target.innerText = parts.join(" · ");
  target.style.display = "block";
}

function updateStatusHost() {
  if (typeof mode === "undefined") {
    console.error("[ДИАГНОСТИКА STATUS] Ошибка: переменная mode не определена. Установите режим по умолчанию.");
    return;
  }

  fetch('/status')
    .then(response => response.json())
    .then(data => {
      let prevStatus = lastTrackStatus;
      lastTrackStatus = data.status;
      updatePlayPauseButton(data.status);

      //console.log(`[ДИАГНОСТИКА STATUS] prevStatus=${prevStatus}, lastTrackStatus=${lastTrackStatus}, currentIndex=${currentIndex}, playlist.length=${playlist.length}`);

      if (data.status === "playing") {
        let trackName = data.title || (data.track ? data.track.split(/[/\\]+/).pop() : "Неизвестный трек");
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        updateTaxonomyDisplay(data.taxonomy);
        const ct = (data.current_time || 0) / 1000;
        const dur = (data.duration || 0) / 1000;
        if (!isSeeking && document.getElementById("seekSlider")) {
          let seekSlider = document.getElementById("seekSlider");
          seekSlider.max = dur;
          seekSlider.value = ct;
          document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
        }

        // Синхронизация currentIndex
        if (data.track) {
          let idx = playlist.findIndex(item =>
            item.replace(/\\/g, '/') === data.track.replace(/\\/g, '/')
          );
          if (idx !== -1 && idx !== currentIndex) {
            currentIndex = idx;
            console.log("[ДИАГНОСТИКА STATUS] Синхронизирован индекс по серверу:", currentIndex, playlist[currentIndex]);
          }
        }
        setEnergyWaveStatus(true);
      }

      else if (data.status === "paused") {
        console.log("[ДИАГНОСТИКА STATUS] Трек на паузе.");
        let trackName = data.title || (data.track ? data.track.split(/[/\\]+/).pop() : "Неизвестный трек");
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Пауза: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        updateTaxonomyDisplay(data.taxonomy);
        const ct = (data.current_time || 0) / 1000;
        const dur = (data.duration || 0) / 1000;
        document.getElementById("seekSlider").max = dur;
        document.getElementById("seekSlider").value = ct;
        document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);

        // --- АВТОВОСПРОИЗВЕДЕНИЕ: если только что стартовали трек (lastTrackStatus == null), а сервер вернул paused, отправим resume ---
        if (prevStatus === null && lastTrackStatus === "paused") {
          console.log("[ДИАГНОСТИКА AUTOFIX] После запуска трека сервер вернул paused, пробуем resume...");
          resumeTrackHost();
        }
        setEnergyWaveStatus(false);
      }

      // Если трек остановился и нужно выполнить автопереход
      else if (data.status === "stopped" && prevStatus === "playing") {
        console.log(`[ДИАГНОСТИКА AUTOPLAY] Трек остановился. Проверяем автопереход.`);

        const autoplayMode = localStorage.getItem("autoplayMode") || "off";
        if (
          autoplayMode === "playlist" &&
          mode === "playlist" &&
          playlist.length > 0
        ) {
          // Всегда определяем индекс по текущему треку с сервера!
          let currentTrackPath = (data.track || "").replace(/\\/g, '/');
          // ==== DEBUG: выводим сравниваемые значения ====
          console.log("[DEBUG] playlist[0]:", playlist[0]);
          console.log("[DEBUG] currentTrackPath:", currentTrackPath);
          console.log("[DEBUG] playlist полный список:", playlist);
          // ==== END DEBUG ====

          // Если сервер не вернул путь — не делаем автопереход
          if (!currentTrackPath) {
            console.warn("[ДИАГНОСТИКА AUTOPLAY] Сервер не вернул путь текущего трека. Автопереход невозможен.");
            return;
          }

          let idx = playlist.findIndex(item =>
            item.replace(/\\/g, '/') === currentTrackPath
          );
          if (idx !== -1) {
            if (idx < playlist.length - 1) {
              currentIndex = idx + 1;
              lastTrackStatus = null;
              console.warn(`[ДИАГНОСТИКА AUTOPLAY] Автопереход на следующий трек: ${playlist[currentIndex]}`);
              playTrackHost(playlist[currentIndex]);
              currentIndex = idx + 1; // Явно выставляем индекс
            } else {
              // Достигнут конец плейлиста, сбрасываем интерфейс
              console.info("[ДИАГНОСТИКА AUTOPLAY] Достигнут конец плейлиста.");
              document.getElementById("now_playing").innerText = "Не играет";
              document.getElementById("genre").innerText = "N/A";
              document.getElementById("time_display").innerText = "0:00 / 0:00";
              document.getElementById("seekSlider").value = 0;
              setEnergyWaveStatus(false);
            }
          } else {
            // Если не удалось найти индекс, начинаем сначала (или просто не делаем переход)
            console.warn("[ДИАГНОСТИКА AUTOPLAY] Не удалось определить индекс текущего трека для автоперехода.");
            // Можно попробовать: currentIndex = 0; playTrackHost(playlist[0]);
          }
        }
      }

      // Если плеер не играет
      else {
        console.log("[ДИАГНОСТИКА STATUS] Плеер не играет.");
        document.getElementById("now_playing").innerText = "Не играет";
        document.getElementById("genre").innerText = "N/A";
        document.getElementById("time_display").innerText = "0:00 / 0:00";
        document.getElementById("seekSlider").value = 0;
      }
    })
    .catch(err => console.error(`[ДИАГНОСТИКА STATUS] Ошибка выполнения запроса: ${err}`));
}


// Установка интервалов для обновления статуса
if (playbackMode === "host") {
  setInterval(updateStatusHost, 1000);
} else if (playbackMode === "plyr") {
  setInterval(updateStatusPlyr, 1000);
}

// ========== Обработчики управления ==========
document.addEventListener("DOMContentLoaded", function() {
  document.getElementById("stop_btn")?.addEventListener("click", function(){
    if (playbackMode === "host") {
      lastTrackStatus = null;
      updatePlayPauseButton("stopped");
      fetch('/stop')
        .then(response => response.json())
        .catch(err => console.log(err));
    } else if (playbackMode === "plyr") {
      if (window.playerPlyr) {
        window.playerPlyr.pause();
        window.playerPlyr.currentTime = 0;
      }
      document.getElementById("now_playing").innerText = "Не играет";
      document.getElementById("genre").innerText = "N/A";
      document.getElementById("time_display").innerText = "0:00 / 0:00";
      document.getElementById("seekSlider").value = 0;
      lastTrackStatus = "stopped";
      updatePlayPauseButton("stopped");
    }
  });

  document.getElementById("next_btn")?.addEventListener("click", function(){
    if (playbackMode === "host") {
      nextTrackHost();
    } else if (playbackMode === "plyr") {
      if (playlist.length > 0) {
        currentIndex = (currentIndex + 1) % playlist.length;
        playTrackPlyr(playlist[currentIndex]);
      }
    }
  });

  document.getElementById("prev_btn")?.addEventListener("click", function(){
    if (playbackMode === "host") {
      prevTrackHost();
    } else if (playbackMode === "plyr") {
      if (playlist.length > 0) {
        currentIndex = (currentIndex - 1 + playlist.length) % playlist.length;
        playTrackPlyr(playlist[currentIndex]);
      }
    }
  });



  if (document.getElementById("volumeSlider")) {
    document.getElementById("volumeSlider").addEventListener("input", function(){
      const vol = Number(this.value);
      window.currentVolume = vol;
      if (playbackMode === "host") {
        setVolumeHost(vol);
      } else if (playbackMode === "plyr") {
        setVolumePlyr(vol);
      }
      document.getElementById("volume_display").innerText = "Громкость: " + vol + "%";
    });
  }

document.getElementById("pause_button")?.addEventListener("click", function(){
  if (playbackMode === "host") {
    if (lastTrackStatus === "playing") {
      pauseTrackHost();
    } else if (lastTrackStatus === "paused") {
      resumeTrackHost();
    } else {
      const targetTrack = lastKnownTrackPath || playlist[currentIndex] || playlist[0];
      if (targetTrack) playTrackHost(targetTrack);
    }
  } else if (playbackMode === "plyr") {
    if (window.playerPlyr && !window.playerPlyr.paused) {
      pauseTrackPlyr();
    } else if (window.currentTrackTitle) {
      resumeTrackPlyr();
    } else {
      const targetTrack = lastKnownTrackPath || playlist[currentIndex] || playlist[0];
      if (targetTrack) playTrackPlyr(targetTrack);
    }
  }
});

  function revealCurrentFolderInTree(tree, path) {
    const parts = String(path || "")
      .replace(/\\/g, "/")
      .split("/")
      .map(part => part.trim())
      .filter(Boolean);
    if (!tree || !parts.length) return;

    const nodeIds = [];
    parts.forEach((part) => {
      nodeIds.push(nodeIds.length ? `${nodeIds[nodeIds.length - 1]}/${part}` : part);
    });

    const openLevel = (index) => {
      const nodeId = nodeIds[index];
      const node = tree.get_node(nodeId);
      if (!node) return;
      if (index === nodeIds.length - 1) {
        tree.deselect_all();
        tree.select_node(nodeId);
        const nodeElement = tree.get_node(nodeId, true)?.get(0);
        nodeElement?.scrollIntoView({block: "center", behavior: "smooth"});
        return;
      }
      tree.open_node(nodeId, () => openLevel(index + 1), false);
    };

    openLevel(0);
  }

  $('#folderModal').on('shown.bs.modal', function () {
    const treeElement = $('#folderTree');
    const currentFolder = window.playerConfig?.currentPath || "";
    const existingTree = treeElement.jstree(true);
    if (existingTree) {
      revealCurrentFolderInTree(existingTree, currentFolder);
      return;
    }

    treeElement.one("loaded.jstree", function () {
      revealCurrentFolderInTree(treeElement.jstree(true), currentFolder);
    });
    treeElement.jstree({
      'core': {
        'data': {
          "url": "/get_directories",
          "dataType": "json",
          "data": function (node) { return { "id": node.id }; }
        }
      }
    });
  });


  document.getElementById("selectFolderBtn")?.addEventListener("click", function(){
      const tree = $('#folderTree').jstree(true);
      const selected = tree.get_selected();
      console.log("Выбранные элементы:", selected);
      if (selected.length) {
        const path = selected[0];
        console.log("Выбранный путь:", path);
        window.location.href = "/browse?path=" + encodeURIComponent(path);
      } else {
        alert("Пожалуйста, выберите папку.");
      }
    });

  // Plyr инициализация
if (playbackMode === "plyr") {
  window.playerPlyr = new Plyr('#audioPlayerPlyr');
  window.playerPlyr.on('ended', function() {
    // Приоритет localStorage, затем playerConfig
    let mode = localStorage.getItem("autoplayMode") ?? window.playerConfig?.autoplayMode ?? "off";
    if (mode === "playlist" && playlist.length > 0 && typeof currentIndex !== "undefined") {
      if (currentIndex < playlist.length - 1) {
        currentIndex++;
        playTrackPlyr(playlist[currentIndex]);
        return;
      }
    }
    // Если автоплей выключен, сбросить надписи
    document.getElementById("now_playing").innerText = "Не играет";
    document.getElementById("genre").innerText = "N/A";
    lastTrackStatus = "stopped";
    updatePlayPauseButton("stopped");
  });
}
   // +++ НАЧАЛО КОДА ДЛЯ ИНИЦИАЛИЗАЦИИ ВСЕХ ВССПЛЫВАЮЩИХ ПОДСКАЗОК +++
  var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
    // Убедимся, что для элемента еще не создан tooltip
    if (!bootstrap.Tooltip.getInstance(tooltipTriggerEl)) {
      return new bootstrap.Tooltip(tooltipTriggerEl);
    }
    return bootstrap.Tooltip.getInstance(tooltipTriggerEl); // Возвращаем существующий, если он есть
  });
  // +++ КОНЕЦ КОДА ДЛЯ ИНИЦИАЛИЗАЦИИ ВСЕХ ВССПЛЫВАЮЩИХ ПОДСКАЗОК +++

});

// ========== Favorites, jsTree и сканирование ==========
function normalizeFavoritePath(path) {
  return String(path || "").replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
}

const favoritePathSet = new Set(
  (window.playerConfig?.favoritePaths || []).map(normalizeFavoritePath)
);
const trackRatingMap = new Map(
  Object.entries(window.playerConfig?.favoriteRatings || {}).map(([path, rating]) => [
    normalizeFavoritePath(path), Number(rating || 0),
  ])
);

function toggleFavButton(btn, isFav) {
  if (!btn) return;
  const icon = btn.querySelector("i");
  btn.classList.toggle("btn-danger", isFav);
  btn.classList.toggle("is-favorite", isFav);
  btn.classList.toggle("btn-outline-danger", !isFav);
  if (icon) icon.className = isFav ? "bi bi-heart-fill" : "bi bi-heart";
  btn.setAttribute("aria-pressed", isFav ? "true" : "false");
  btn.setAttribute("aria-label", isFav ? "Убрать из избранного" : "Добавить в избранное");
  btn.title = isFav ? "Убрать из избранного" : "Добавить в избранное";
}

function isFavoritePath(path) {
  return favoritePathSet.has(normalizeFavoritePath(path));
}

function setFavoriteState(path, isFavorite) {
  const normalized = normalizeFavoritePath(path);
  if (!normalized) return;
  if (isFavorite) favoritePathSet.add(normalized);
  else favoritePathSet.delete(normalized);

  document.querySelectorAll(".fav-btn[data-track-path]").forEach(btn => {
    if (normalizeFavoritePath(btn.dataset.trackPath) === normalized) {
      toggleFavButton(btn, isFavorite);
    }
  });
  updateNowPlayingFavoriteButton(lastKnownTrackPath || path);
}

function updateNowPlayingFavoriteButton(path) {
  const btn = document.getElementById("current_favorite_btn");
  if (!btn) return;
  const value = String(path || "");
  btn.dataset.trackPath = value;
  btn.disabled = !value;
  toggleFavButton(btn, value ? isFavoritePath(value) : false);
}

function trackRating(path) {
  return Number(trackRatingMap.get(normalizeFavoritePath(path)) || 0);
}

function updateQuickRatingElement(element, rating) {
  if (!element) return;
  const value = Math.max(0, Math.min(5, Number(rating || 0)));
  element.dataset.rating = String(value);
  element.querySelectorAll(".quick-rating-star").forEach((star) => {
    const selected = Number(star.dataset.ratingValue || 0) <= value;
    star.classList.toggle("is-rated", selected);
    star.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  element.querySelector(".quick-rating-reset")?.classList.toggle("invisible", value === 0);
}

function updateTrackRatingUi(path, rating) {
  const normalized = normalizeFavoritePath(path);
  if (!normalized) return;
  if (Number(rating) > 0) trackRatingMap.set(normalized, Number(rating));
  else trackRatingMap.delete(normalized);
  document.querySelectorAll(".quick-rating[data-rating-track-path]").forEach((element) => {
    if (normalizeFavoritePath(element.dataset.ratingTrackPath) === normalized) {
      updateQuickRatingElement(element, rating);
    }
  });
}

function updateNowPlayingRating(path) {
  const element = document.getElementById("now-playing-rating");
  if (!element) return;
  const value = String(path || "");
  element.dataset.ratingTrackPath = value;
  element.querySelectorAll("button").forEach((button) => { button.disabled = !value; });
  updateQuickRatingElement(element, value ? trackRating(value) : 0);
}

async function saveQuickTrackRating(path, rating, {showToast = true} = {}) {
  const normalized = normalizeFavoritePath(path);
  if (!normalized) return;
  const previous = trackRating(path);
  updateTrackRatingUi(path, rating);
  try {
    const response = await fetch("/api/track-rating", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path, rating}),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
    updateTrackRatingUi(data.path || path, data.rating || 0);
    if (showToast && Number(data.rating) >= 4) {
      showUiToast(`Оценка ${Number(data.rating)}★ сохранена`, "Найти похожие", () => openSmartCatalogForPath(data.path || path));
    } else if (showToast) {
      showUiToast(Number(data.rating) ? `Оценка ${Number(data.rating)}★ сохранена` : "Оценка сброшена");
    }
    postToSmartCatalog("catalog-rating-updated", {path: data.path || path, rating: Number(data.rating || 0)});
    return data;
  } catch (error) {
    updateTrackRatingUi(path, previous);
    showUiToast(`Не удалось сохранить оценку: ${error.message}`);
    throw error;
  }
}

function showUiToast(message, actionLabel = "", action = null) {
  const container = document.getElementById("uiToastContainer");
  if (!container) return;
  const toastElement = document.createElement("div");
  toastElement.className = "toast align-items-center border-0";
  toastElement.setAttribute("role", "status");
  toastElement.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${message}</div>
      ${actionLabel ? `<button type="button" class="btn btn-link btn-sm text-nowrap ui-toast-action">${actionLabel}</button>` : ""}
      <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Закрыть"></button>
    </div>`;
  container.appendChild(toastElement);
  if (actionLabel && typeof action === "function") {
    toastElement.querySelector(".ui-toast-action")?.addEventListener("click", () => {
      action();
      bootstrap.Toast.getInstance(toastElement)?.hide();
    }, { once: true });
  }
  toastElement.addEventListener("hidden.bs.toast", () => toastElement.remove(), { once: true });
  new bootstrap.Toast(toastElement, { delay: 3500 }).show();
}

function refreshFavoritesIfOpen() {
  if (document.getElementById("favoritesModal")?.classList.contains("show")) {
    loadFavoritesContent();
  }
}

function loadFavoritesContent() {
  const currentHighlighted = document.querySelector("#playlist .list-group-item.current");
  const currentTrackPath = currentHighlighted ? currentHighlighted.getAttribute("data-track") : null;

  fetch("/favorites_list")
    .then(response => response.json())
    .then(data => {
      document.getElementById("favoritesContent").innerHTML = data.html;
      if (currentTrackPath) setTimeout(() => highlightCurrentTrack(currentTrackPath), 100);
      setupFavoriteGenreFilter();
      initFavoritesRating();
      return fetch("/current-track");
    })
    .then(response => response.json())
    .then(data => {
      if (data.currentTrack) highlightCurrentFavorite(data.currentTrack);
    })
    .catch(err => {
      console.error("Ошибка при загрузке избранного:", err);
      const content = document.getElementById("favoritesContent");
      if (content) content.innerHTML = '<div class="alert alert-warning">Не удалось загрузить избранное.</div>';
    });
}

document.getElementById("favoritesModal")?.addEventListener("show.bs.modal", loadFavoritesContent);

function toggleFavorite(path, btn) {
  if (!path) return;
  if (isFavoritePath(path)) removeFavorite(path, btn);
  else addFavorite(path, btn);
}

function addFavorite(path, btn, options = {}) {
  if (isFavoritePath(path)) {
    setFavoriteState(path, true);
    return;
  }
  toggleFavButton(btn, true);
  fetch('/favorite', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: path })
  })
    .then(response => response.json())
    .then(data => {
      if (data.status === "success" || data.status === "exists") {
        setFavoriteState(path, true);
        refreshFavoritesIfOpen();
        if (options.showToast !== false) {
          showUiToast("Добавлено в избранное", "Отменить", () => removeFavorite(path, null, { showToast: false }));
        }
      } else if (data.error && data.error.toLowerCase().includes("file not found")) {
        toggleFavButton(btn, false);
        showCannotAddFavoriteModal(path);
      } else {
        toggleFavButton(btn, false);
        showUiToast("Ошибка при добавлении трека");
      }
    })
    .catch(() => {
      toggleFavButton(btn, false);
      showUiToast("Не удалось добавить трек в избранное");
    });
}

function removeFavorite(path, btn, options = {}) {
  if (btn) toggleFavButton(btn, false);
  fetch('/remove_favorite', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: path })
  })
    .then(response => response.json())
    .then(data => {
      if (data.status === "removed") {
        setFavoriteState(path, false);
        refreshFavoritesIfOpen();
        if (options.showToast !== false) {
          showUiToast("Убрано из избранного", "Вернуть", () => addFavorite(path, null, { showToast: false }));
        }
      } else {
        setFavoriteState(path, true);
        showUiToast("Ошибка при удалении трека");
      }
    })
    .catch(() => {
      setFavoriteState(path, true);
      showUiToast("Не удалось изменить избранное");
    });
}

document.addEventListener("DOMContentLoaded", function() {
  document.getElementById("current_favorite_btn")?.addEventListener("click", function() {
    toggleFavorite(this.dataset.trackPath, this);
  });
  if (new URLSearchParams(window.location.search).get("open") === "favorites") {
    bootstrap.Modal.getOrCreateInstance(document.getElementById("favoritesModal")).show();
  }
  document.querySelectorAll(".quick-rating[data-rating-track-path]").forEach((element) => {
    updateQuickRatingElement(element, trackRating(element.dataset.ratingTrackPath));
  });
});

document.addEventListener("click", (event) => {
  const button = event.target.closest(".quick-rating [data-rating-value]");
  if (!button || button.disabled) return;
  const container = button.closest(".quick-rating");
  const path = container?.dataset.ratingTrackPath || "";
  if (!path) return;
  event.preventDefault();
  event.stopPropagation();
  saveQuickTrackRating(path, Number(button.dataset.ratingValue || 0)).catch(() => {});
});

function scanLibrary(){
  fetch('/scan_library')
    .then(response => response.json())
    .then(data => { alert("Сканирование выполнено."); })
    .catch(err => {});
}

// Обработчик для кнопки "Похожий"
document.getElementById("recommend_btn")?.addEventListener("click", function(){
  fetch('/recommend')
    .then(response => response.json())
    .then(data => {
      if (data.redirect) {
        // Вызываем модальное окно для выбора действия
        showRecommendModal(data.filename, data.folder, data.redirect);
      } else if (data.error && data.error.indexOf("Нет установлено жанра") !== -1) {
        if (confirm("Жанр текущего трека не установлен. Запустить анализ трека?")) {
          fetch('/analyze')
            .then(response => response.json())
            .then(anData => {
              if (anData.status === "analyzed") {
                alert("Жанр обновлен: " + anData.genre);
                fetch('/recommend')
                  .then(response => response.json())
                  .then(recData => {
                    if (recData.redirect) {
                      showRecommendModal(recData.filename, recData.folder, recData.redirect);
                    } else {
                      alert("Похожий трек не найден.");
                    }
                  });
              }
            });
        }
      } else {
        alert("Похожий трек не найден: " + (data.error || ""));
      }
    })
    .catch(err => console.log(err));
});

// Функция показа модального окна для найденного похожего трека
function showRecommendModal(filename, folder, rel_path) {
  // Удаляем старую модалку, если есть
  const existingModal = document.getElementById("recommendModal");
  if (existingModal) existingModal.remove();

  const modalHtml = `
    <div class="modal fade" id="recommendModal" tabindex="-1" aria-labelledby="recommendModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title" id="recommendModalLabel">Похожий трек найден</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>Найден трек: <b>${filename}</b></p>
            <p>Директория: <i>${folder}</i></p>
            <p>Перейти в эту директорию и воспроизвести трек?</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-primary" id="goToTrack">Перейти</button>
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Остаться здесь</button>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  const modalElement = document.getElementById("recommendModal");
  const modalInstance = new bootstrap.Modal(modalElement);
  modalInstance.show();

  // Только удаление DOM-элемента после закрытия
  modalElement.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => {
      modalElement.remove();
    }, 200);
  });

  document.getElementById("goToTrack").onclick = function() {
    modalInstance.hide();
    window.location.href = `/autoplay?track=${encodeURIComponent(rel_path)}`;
  };
}
// Функция показа модального окна для трека, недоступного по текущему пути
function showTrackNotFoundModal(trackPath, source = "playlist") {
  // Удаляем старую модалку
  const existingModal = document.getElementById("trackNotFoundModal");
  if (existingModal) existingModal.remove();

  const isFavorite = (source === "favorites");
  const removeBtnHtml = isFavorite
    ? `<button type="button" id="removeFavBtn" class="btn btn-danger">Удалить из избранного</button>`
    : "";
  const modalHtml = `
    <div class="modal fade" id="trackNotFoundModal" tabindex="-1" aria-labelledby="trackNotFoundModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-warning">
            <h5 class="modal-title" id="trackNotFoundModalLabel">Трек не найден</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>Выбранный трек<br><b>${trackPath.split('/').pop()}</b><br>недоступен по текущему пути.<br>
            ${isFavorite
              ? 'Вероятно, директория с музыкой была изменена или трек удалён.<br>Хотите удалить этот трек из избранного?'
              : 'Вероятно, директория с музыкой была изменена или трек удалён.'}
            </p>
          </div>
          <div class="modal-footer">
            ${removeBtnHtml}
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Закрыть</button>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  const modalElement = document.getElementById("trackNotFoundModal");
  const modalInstance = new bootstrap.Modal(modalElement);
  modalInstance.show();

  // После закрытия модалки сразу восстанавливаем выделение трека
  modalElement.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => {
      modalElement.remove();
      highlightCurrentTrack(); // Восстановить выделение трека
    }, 200);
  });
    if(isFavorite) {
      document.getElementById("removeFavBtn").onclick = function() {
        removeFavorite(trackPath);
        modalInstance.hide();
        // После удаления из избранного тоже восстанавливаем выделение трека
        setTimeout(() => highlightCurrentTrack(), 300);
      };
    }
  }

// Функция показа модального окна о треке, недоступном для добавления в избранное
function showCannotAddFavoriteModal(trackPath) {
  // Удаляем старую модалку, если есть
  const existingModal = document.getElementById("cannotAddFavoriteModal");
  if (existingModal) existingModal.remove();

  const modalHtml = `
    <div class="modal fade" id="cannotAddFavoriteModal" tabindex="-1" aria-labelledby="cannotAddFavoriteModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-warning">
            <h5 class="modal-title" id="cannotAddFavoriteModalLabel">Ошибка добавления в избранное</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>Невозможно добавить в избранное:<br>
              <b>${trackPath.split('/').pop()}</b><br>
              файл уже удалён или недоступен.</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Закрыть</button>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  const modalElement = document.getElementById("cannotAddFavoriteModal");
  const modalInstance = new bootstrap.Modal(modalElement);
  modalInstance.show();

  modalElement.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => {
      modalElement.remove();
    }, 200);
  });
}

// Функция показа модального окна о предупреждении удаления из избранного
let pendingRemoveFav = { path: null, btn: null };

function showRemoveFavoriteConfirmModal(path, btn) {
  // Удаляем старую модалку, если есть
  const existingModal = document.getElementById("removeFavoriteConfirmModal");
  if (existingModal) existingModal.remove();

  const modalHtml = `
    <div class="modal fade" id="removeFavoriteConfirmModal" tabindex="-1" aria-labelledby="removeFavoriteConfirmModalLabel" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header bg-danger text-white">
            <h5 class="modal-title" id="removeFavoriteConfirmModalLabel">Удалить из избранного</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button>
          </div>
          <div class="modal-body">
            <p>Вы действительно хотите удалить из избранного трек:<br><b>${path.split('/').pop()}</b>?</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-danger" id="confirmRemoveFavBtn">Удалить</button>
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Отмена</button>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  pendingRemoveFav = { path, btn };
  const modalElement = document.getElementById("removeFavoriteConfirmModal");
  const modalInstance = new bootstrap.Modal(modalElement);
  modalInstance.show();

  modalElement.addEventListener('hidden.bs.modal', function () {
    setTimeout(() => {
      modalElement.remove();
      pendingRemoveFav = { path: null, btn: null };
    }, 200);
  });

  document.getElementById("confirmRemoveFavBtn").onclick = function() {
    // Выполняем удаление после подтверждения
    actuallyRemoveFavorite(pendingRemoveFav.path, pendingRemoveFav.btn);
    modalInstance.hide();
  };
}

function actuallyRemoveFavorite(path, btn) {
  removeFavorite(path, btn);
}

// Функция Синхронизации текущего трека и currentIndex при старте
document.addEventListener("DOMContentLoaded", function() {
  setTimeout(() => {
    const params = new URLSearchParams(window.location.search);
    const autoplay = window.playerConfig.autoplay;
    const playlist = window.playerConfig.playlist;

    // --- 1. Переход из избранного: воспроизводим всегда, если есть autoplay и fromfavorites=1 ---
    const fromFavorites = params.get("fromfavorites") === "1";

    if (autoplay && autoplay.trim() !== "" && fromFavorites) {
      let autoplayFile = autoplay.split('/').pop();
      let idx = playlist.findIndex(item => {
        let fileName = item.split('/').pop().split('\\').pop();
        return fileName === autoplayFile;
      });
      if (idx !== -1) {
        currentIndex = idx;
        lastTrackStatus = null;
        playTrack(playlist[currentIndex]);
        window.playerConfig.autoplay = "";
        // Сбросить параметры из адресной строки, чтобы при обновлении не стартовало снова
        const url = new URL(window.location);
        url.searchParams.delete('autoplay');
        url.searchParams.delete('fromfavorites');
        window.history.replaceState({}, '', url);
      }
      return;
    }

    // --- 2. Обычный автозапуск только если не из избранного и режим не "off" ---
    const autoplayMode = window.playerConfig.autoplayMode || "off";
    if (autoplay && autoplay.trim() !== "" && autoplayMode !== "off") {
      let autoplayFile = autoplay.split('/').pop();
      let idx = playlist.findIndex(item => {
        let fileName = item.split('/').pop().split('\\').pop();
        return fileName === autoplayFile;
      });
      if (idx !== -1) {
        currentIndex = idx;
        lastTrackStatus = null;
        playTrack(playlist[currentIndex]);
        window.playerConfig.autoplay = "";
        // Сбросить параметр autoplay из адресной строки (на всякий случай)
        const url = new URL(window.location);
        url.searchParams.delete('autoplay');
        window.history.replaceState({}, '', url);
      }
      return;
    }

    // --- 3. Синхронизируемся с сервером: если играет — только выделяем, без автозапуска! ---
    fetch("/status")
      .then(response => response.json())
      .then(data => {
        if (data.status === "playing" || data.status === "paused") {
          let currentTrack = data.track || "";
          let idx = playlist.findIndex(item => {
            let fileName = item.split('/').pop().split('\\').pop();
            let serverFile = currentTrack.split('/').pop().split('\\').pop();
            return fileName === serverFile;
          });
          if (idx !== -1) {
            currentIndex = idx;
            lastTrackStatus = data.status;
            // UI-обновление, playTrack НЕ ДЕЛАТЬ!
            console.log("Синхронизирован с сервером (UI обновлён), играет:", playlist[currentIndex]);
          }
        }
      });
  }, 700); // можно уменьшить задержку до 300-700 мс
});

let lastKnownTrackPath = null;

const smartCatalogPanel = document.getElementById("smartCatalogPanel");
const smartCatalogFrame = document.getElementById("smartCatalogFrame");
const smartCatalogDock = document.getElementById("smartCatalogDock");
const smartCatalogReference = document.getElementById("smartCatalogReference");
const smartCatalogDockSummary = document.getElementById("smartCatalogDockSummary");

function smartCatalogTrackName(path) {
  const normalized = String(path || "").replace(/\\/g, "/");
  return normalized.split("/").pop() || normalized || "текущий трек";
}

function postToSmartCatalog(type, payload = {}) {
  if (!smartCatalogFrame?.contentWindow || smartCatalogFrame.src === "about:blank") return;
  smartCatalogFrame.contentWindow.postMessage({type, ...payload}, window.location.origin);
}

function setSmartCatalogReference(path, label = "") {
  if (!smartCatalogReference) return;
  smartCatalogReference.textContent = label || smartCatalogTrackName(path);
  smartCatalogReference.title = String(path || "");
}

function showSmartCatalogPanel() {
  if (!smartCatalogPanel || !smartCatalogFrame) return;
  smartCatalogPanel.classList.add("is-open");
  smartCatalogPanel.setAttribute("aria-hidden", "false");
  if (smartCatalogDock) smartCatalogDock.hidden = true;
  document.body.classList.add("smart-catalog-open");
}

function openSmartCatalogForPath(path) {
  if (!smartCatalogPanel || !smartCatalogFrame) return;
  const reference = String(path || "");
  const firstOpen = smartCatalogFrame.dataset.initialized !== "true";
  if (firstOpen) {
    smartCatalogFrame.dataset.initialized = "true";
    smartCatalogFrame.src = `/intelligence?embedded=1&path=${encodeURIComponent(reference)}`;
  } else if (reference) {
    postToSmartCatalog("catalog-set-reference", {path: reference, autoMatch: true});
  }
  setSmartCatalogReference(reference);
  showSmartCatalogPanel();
}

function openSmartCatalogPanel({useCurrent = false} = {}) {
  if (!smartCatalogPanel || !smartCatalogFrame) return;
  const reference = lastKnownTrackPath || "";
  const firstOpen = smartCatalogFrame.dataset.initialized !== "true";
  if (firstOpen || (useCurrent && reference)) return openSmartCatalogForPath(reference);
  showSmartCatalogPanel();
}

function minimizeSmartCatalogPanel() {
  if (!smartCatalogPanel) return;
  smartCatalogPanel.classList.remove("is-open");
  smartCatalogPanel.setAttribute("aria-hidden", "true");
  if (smartCatalogDock) smartCatalogDock.hidden = false;
  document.body.classList.remove("smart-catalog-open");
}

function closeSmartCatalogPanel() {
  minimizeSmartCatalogPanel();
  if (smartCatalogDock) smartCatalogDock.hidden = true;
  if (smartCatalogFrame) {
    smartCatalogFrame.src = "about:blank";
    delete smartCatalogFrame.dataset.initialized;
  }
  if (smartCatalogDockSummary) smartCatalogDockSummary.textContent = "Сессия свёрнута";
  setSmartCatalogReference("");
}

document.getElementById("smart_select_btn")?.addEventListener("click", () => openSmartCatalogPanel());
document.getElementById("smartCatalogUseCurrent")?.addEventListener("click", () => openSmartCatalogPanel({useCurrent: true}));
document.getElementById("smartCatalogMinimize")?.addEventListener("click", minimizeSmartCatalogPanel);
document.getElementById("smartCatalogClose")?.addEventListener("click", closeSmartCatalogPanel);
smartCatalogDock?.addEventListener("click", () => openSmartCatalogPanel());

document.addEventListener("click", (event) => {
  if (!smartCatalogPanel?.classList.contains("is-open")) return;
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (smartCatalogPanel.contains(target)) return;
  if (target.closest("#smart_select_btn, #smartCatalogDock")) return;
  minimizeSmartCatalogPanel();
});

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || event.source !== smartCatalogFrame?.contentWindow || !event.data) return;
  const {type, path, label, count, rating} = event.data;
  if (type === "catalog-play" && path) {
    playTrack(path);
    highlightCurrentTrack(path);
    return;
  }
  if (type === "catalog-reference") {
    setSmartCatalogReference(path, label);
    return;
  }
  if (type === "catalog-rated" && path) {
    updateTrackRatingUi(path, Number(rating || 0));
    return;
  }
  if (type === "catalog-results" && smartCatalogDockSummary) {
    smartCatalogDockSummary.textContent = `Найдено треков: ${Number(count || 0)}`;
    return;
  }
  if (type === "catalog-queue" && smartCatalogDockSummary && Number(count || 0) > 0) {
    smartCatalogDockSummary.textContent = `В очереди: ${Number(count)}`;
    return;
  }
  if (type === "catalog-ready") {
    postToSmartCatalog("catalog-theme", {
      theme: document.body.classList.contains("dark-theme") ? "dark" : "light",
    });
    postToSmartCatalog("catalog-playing", {path: lastKnownTrackPath || ""});
  }
});

function highlightCurrentTrack(currentTrackPath) {
  if (currentTrackPath && currentTrackPath.trim() !== "") {
    lastKnownTrackPath = currentTrackPath;
  }
  const pathToHighlight = (currentTrackPath && currentTrackPath.trim() !== "") ? currentTrackPath : lastKnownTrackPath;
  postToSmartCatalog("catalog-playing", {path: pathToHighlight || ""});
  updateNowPlayingFavoriteButton(pathToHighlight);
  updateNowPlayingRating(pathToHighlight);
  if (!pathToHighlight) return;

  // Найти элемент, который нужно выделить
  const $toHighlight = $("#playlist .list-group-item").filter(function() {
    return $(this).data("track")?.replace(/\\/g, '/') === pathToHighlight.replace(/\\/g, '/');
  });

  //console.log('Восстанавливаю выделение', pathToHighlight, (new Date()).toISOString(), 'Найдено элементов:', $toHighlight.length);

  // --- Если нужный элемент не найден, НЕ снимаем выделение! ---
  if ($toHighlight.length === 0) {
    return;
  }

  // Снимаем выделение со всех и ставим только на нужный
  $("#playlist .list-group-item").removeClass("current");
  $toHighlight.addClass("current");
}

// Функция опроса сервера для получения текущего трека
function updateCurrentTrackHighlight() {
  $.ajax({
    url: "/current-track",
    type: "GET",
    dataType: "json",
    success: function(data) {
      // ВСЕГДА вызываем, не только если пришёл новый трек
      highlightCurrentTrack(data.currentTrack);
      if (document.getElementById("favoritesModal")?.classList.contains("show")) {
        highlightCurrentFavorite(data.currentTrack);
      }
    },
    error: function(xhr, status, error) {
      // Даже при ошибке — пробуем восстановить по кэшу
      highlightCurrentTrack();
    }
  });
}

// Одного опроса достаточно и для плейлиста, и для открытого списка избранного.
setInterval(updateCurrentTrackHighlight, 1500);
// ДИАГНОСТИКА: Начало отслеживание множественного воспроизведения
let diagnosticData = {
  lastPlayCommand: null,
  playCommandCount: 0,
  statusHistory: []
};

function logPlayCommand(trackPath, source) {
  diagnosticData.lastPlayCommand = {
    track: trackPath,
    source: source,
    timestamp: new Date().toISOString()
  };
  diagnosticData.playCommandCount++;
  console.log(`[ДИАГНОСТИКА PLAY] #${diagnosticData.playCommandCount} ${source}: ${trackPath}`);
}

function checkForOverlappingTracks() {
  if (playbackMode === "host") {
    fetch('/status')
      .then(response => response.json())
      .then(data => {
        const currentStatus = {
          status: data.status,
          track: data.track,
          title: data.title,
          timestamp: new Date().toISOString()
        };

        // Сохраняем историю статусов
        diagnosticData.statusHistory.push(currentStatus);
        if (diagnosticData.statusHistory.length > 10) {
          diagnosticData.statusHistory.shift(); // Оставляем только последние 10
        }

        // Проверяем несоответствия
        const displayedText = document.getElementById("now_playing").innerText;
        if (data.status === "playing" && data.title) {
          if (!displayedText.includes(data.title)) {
            console.error(`[ДИАГНОСТИКА OVERLAP] ОБНАРУЖЕНО НАЛОЖЕНИЕ!
              Интерфейс показывает: "${displayedText}"
              VLC играет: "${data.title}" (${data.track})
              Последняя команда воспроизведения: ${JSON.stringify(diagnosticData.lastPlayCommand)}
              История статусов: ${JSON.stringify(diagnosticData.statusHistory.slice(-3))}`);
          }
        }
      })
      .catch(err => console.log("[ДИАГНОСТИКА] Ошибка проверки статуса:", err));
  }
}

// Запускаем диагностику каждые 2 секунды
setInterval(checkForOverlappingTracks, 2000);
// ДИАГНОСТИКА: Конец отслеживание множественного воспроизведения

// Пример обработчика для формы избранных настроек с AJAX (если используется fetch)
document.addEventListener("DOMContentLoaded", function() {
  var favSettingsForm = document.getElementById("favSettingsForm");
  if (favSettingsForm) {
    favSettingsForm.addEventListener("submit", function(event) {
      event.preventDefault();  // отменяем стандартную отправку формы
      var formData = new FormData(favSettingsForm);
      fetch("/update_fav_settings", {
        method: "POST",
        body: formData
      })
      .then(response => response.json())
      .then(data => {
        // Выполняем программную замену адреса.
        window.location.href = data.redirect;
      })
      .catch(err => {
        console.error("Ошибка обновления настройки:", err);
        alert("Ошибка обновления настроек избранных");
      });
    });
  }
});

function playFavoriteTrack(trackPath) {
  console.log("playFavoriteTrack вызвана с trackPath:", trackPath);

  // Получаем режим favoriteMode из localStorage либо конфига
  let favMode = localStorage.getItem("favoriteMode");
  if (!favMode) {
    if (typeof playerConfig !== "undefined" && playerConfig.favoriteMode) {
      favMode = playerConfig.favoriteMode;
    } else if (window.settingsConfig && window.settingsConfig.favoriteMode) {
      favMode = window.settingsConfig.favoriteMode;
    } else {
      favMode = "stay";
    }
    localStorage.setItem("favoriteMode", favMode);
  }

  console.log("Окончательное значение favoriteMode:", favMode);

  if (favMode === "switch") {
    // --- ДОБАВЛЕНО: Проверка существования перед переходом ---
    fetch('/file_exists?path=' + encodeURIComponent(trackPath))
      .then(response => response.json())
      .then(data => {
        if (data.exists) {
          const folder = trackPath.substring(0, trackPath.lastIndexOf('/'));
          const url = `/browse?path=${encodeURIComponent(folder)}&autoplay=${encodeURIComponent(trackPath)}&fromfavorites=1`;
          window.location.href = url;
        } else {
          showTrackNotFoundModal(trackPath, "favorites");
        }
      })
      .catch(err => {
        showTrackNotFoundModal(trackPath, "favorites");
      });
    return;
  } else {
    if (typeof playTrack === "function") {
      playTrack(trackPath);
      // Синхронизация currentIndex после запуска трека из избранного
      let idx = playlist.findIndex(item =>
        item.replace(/\\/g, '/') === trackPath.replace(/\\/g, '/')
      );
      if (idx !== -1) {
        currentIndex = idx;
        lastTrackStatus = null;
        console.log("Синхронизирован индекс (из избранного):", currentIndex, playlist[currentIndex]);
      } else {
        console.warn("Не найден трек в плейлисте при запуске из избранного:", trackPath);
      }
    } else {
      console.warn("Функция playTrack не определена");
    }
    if (typeof highlightCurrentTrack === "function") {
      highlightCurrentTrack(trackPath);
    }
  }
}

// После загрузки избранного (после вставки HTML в #favoritesContent)
function setupFavoriteGenreFilter() {
  const entries = document.querySelectorAll("#favoritesContent .fav-entry");
  const genresSet = new Set();
  entries.forEach(entry => genresSet.add(entry.dataset.genre || "Unknown"));

  const select = document.getElementById("favGenreFilter");
  if (!select) return;
  select.querySelectorAll('option:not([value="all"])').forEach(option => option.remove());
  [...genresSet].sort((a, b) => a.localeCompare(b, "ru")).forEach(genre => {
    if (genre && genre !== "Unknown") {
      const opt = document.createElement("option");
      opt.value = genre;
      opt.textContent = genre;
      select.appendChild(opt);
    }
  });

  const search = document.getElementById("favoritesSearch");
  const sort = document.getElementById("favoritesSort");
  select.onchange = applyFavoritesView;
  if (search) search.oninput = applyFavoritesView;
  if (sort) sort.onchange = applyFavoritesView;
  applyFavoritesView();
}

function applyFavoritesView() {
  const list = document.querySelector("#favoritesContent ul.list-group");
  if (!list) {
    const count = document.getElementById("favoritesCount");
    if (count) count.textContent = "0 треков";
    return;
  }
  const searchValue = (document.getElementById("favoritesSearch")?.value || "").trim().toLowerCase();
  const selectedGenre = document.getElementById("favGenreFilter")?.value || "all";
  const sortMode = document.getElementById("favoritesSort")?.value || "added";
  const entries = [...list.querySelectorAll(".fav-entry")];

  entries.sort((a, b) => {
    if (sortMode === "title") return (a.dataset.title || "").localeCompare(b.dataset.title || "", "ru");
    if (sortMode === "rating") {
      const aRating = Number(a.querySelector(".track-rating")?.dataset.rating || 0);
      const bRating = Number(b.querySelector(".track-rating")?.dataset.rating || 0);
      return bRating - aRating || (a.dataset.title || "").localeCompare(b.dataset.title || "", "ru");
    }
    return Number(a.dataset.addedIndex || 0) - Number(b.dataset.addedIndex || 0);
  });

  let visibleCount = 0;
  entries.forEach(entry => {
    list.appendChild(entry);
    const matchesSearch = !searchValue || (entry.dataset.title || "").includes(searchValue);
    const matchesGenre = selectedGenre === "all" || (entry.dataset.genre || "Unknown") === selectedGenre;
    const visible = matchesSearch && matchesGenre;
    entry.classList.toggle("d-none", !visible);
    if (visible) visibleCount += 1;
  });
  const count = document.getElementById("favoritesCount");
  if (count) count.textContent = `${visibleCount} из ${entries.length} треков`;
}

// Функция рейтинга треков

function initFavoritesRating() {
  // Находим все контейнеры рейтинга в списке избранного
  document.querySelectorAll("#favoritesContent .track-rating").forEach(function(ratingElem) {
    const stars = ratingElem.querySelectorAll('.star');
    // Получаем текущий рейтинг из data-атрибута
    let currentRating = parseInt(ratingElem.getAttribute('data-rating')) || 0;
    updateStars(stars, currentRating);

    stars.forEach(function(star) {
      star.addEventListener('mouseover', function() {
        const hoverValue = parseInt(this.getAttribute('data-value'));
        updateStars(stars, hoverValue);
      });

      star.addEventListener('mouseout', function() {
        updateStars(stars, currentRating);
      });

      star.addEventListener('click', function() {
        currentRating = parseInt(this.getAttribute('data-value'));
        ratingElem.setAttribute('data-rating', currentRating);
        updateStars(stars, currentRating);
        // Сохраняем рейтинг для этого трека
        const trackId = ratingElem.closest('.fav-entry').getAttribute('data-track-id');
        saveRating(trackId, currentRating);
      });

    });
  });
}

function resetFavoriteRating(button) {
  const entry = button.closest(".fav-entry");
  const ratingElem = entry?.querySelector(".track-rating");
  if (!entry || !ratingElem) return;
  ratingElem.dataset.rating = "0";
  updateStars(ratingElem.querySelectorAll(".star"), 0);
  saveRating(entry.dataset.trackId, 0);
  applyFavoritesView();
  showUiToast("Оценка сброшена");
}

// Функция обновления отображения звезд
function updateStars(stars, rating) {
  stars.forEach(function(star, idx) {
    if (idx < rating) {
      star.classList.add('rated');
      star.innerHTML = '&#9733;'; // заполненная звезда (★)
    } else {
      star.classList.remove('rated');
      star.innerHTML = '&#9734;'; // пустая звезда (☆)
    }
  });
  const resetButton = stars[0]?.closest(".favorite-rating-row")?.querySelector(".reset-rating");
  resetButton?.classList.toggle("invisible", Number(rating) === 0);
}

// Функция для сохранения рейтинга через AJAX
function saveRating(trackId, rating) {
  fetch('/updateRating', {  // на сервере создайте соответствующий эндпоинт
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ trackId: trackId, rating: rating })
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      console.log("Рейтинг сохранён для трека " + trackId);
      updateTrackRatingUi(data.path || trackId, Number(data.rating || rating || 0));
      postToSmartCatalog("catalog-rating-updated", {path: data.path || trackId, rating: Number(data.rating || rating || 0)});
      applyFavoritesView();
    } else {
      console.error("Ошибка сохранения рейтинга для трека " + trackId);
    }
  })
  .catch(err => console.error("Ошибка:", err));
}
function applyAutoplayMode() {
    const selected = document.querySelector('input[name="autoplayModeOption"]:checked').value;
    // Сохраняем в localStorage и в window.playerConfig
    localStorage.setItem("autoplayMode", selected);
    window.playerConfig.autoplayMode = selected;

    // (опционально) отправляем POST на сервер — если реализовано
    fetch('/update_autoplay_mode', {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "autoplay_mode=" + encodeURIComponent(selected)
    });

    // Закрытие модального окна
    const modalInstance = bootstrap.Modal.getInstance(document.getElementById("autoplayModeModal"));
    if (modalInstance) modalInstance.hide();
}

// Выделение текущего трека в избранном
function highlightCurrentFavorite(currentTrackPath) {
  // Убрать выделение у всех
  document.querySelectorAll("#favoritesContent .fav-entry").forEach(el => {
    el.classList.remove("current");
  });
  // Выделить только тот, у кого совпадает data-track-id
  if (currentTrackPath) {
    // Привести к строке с прямыми слэшами для сравнения (аналогично плейлисту)
    let normalized = currentTrackPath.replace(/\\/g, '/');
    let el = document.querySelector(`#favoritesContent .fav-entry[data-track-id="${CSS.escape(normalized)}"]`);
    if (el) el.classList.add("current");
  }
}
