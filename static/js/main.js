// main.js  20-38
// ========== Смена темы ==========
function applyTheme() {
  var selected = document.querySelector('input[name="themeOption"]:checked').value;
  localStorage.setItem("selectedTheme", selected);
  if (selected === "dark") {
    document.body.classList.add("dark-theme");
  } else {
    document.body.classList.remove("dark-theme");
  }
  var modalInstance = bootstrap.Modal.getInstance(document.getElementById("themeModal"));
  modalInstance.hide();
}
document.addEventListener("DOMContentLoaded", function(){
  var storedTheme = localStorage.getItem("selectedTheme") || "light";
  if (storedTheme === "dark"){
    document.getElementById("darkTheme").checked = true;
    document.body.classList.add("dark-theme");
  } else {
    document.getElementById("lightTheme").checked = true;
    document.body.classList.remove("dark-theme");
  }
});

// ================== Глобальные переменные и плейлист ==================
const playbackMode = window.playerConfig.playbackMode; // host или plyr
const playlist = window.playerConfig.playlist;
const currentPath = window.playerConfig.currentPath;
let currentIndex = 0;
window.playerPlyr = null;

// ========== Функция форматирования времени ==========
function formatTime(seconds) {
  const totalSec = Math.floor(seconds);
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  return mins + ":" + (secs < 10 ? "0" : "") + secs;
}

// ========== HOST (VLC) ==========
function playTrackHost(trackPath) {
  fetch('/play?path=' + encodeURIComponent(trackPath))
    .then(response => response.json())
    .then(data => {
      if (data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
      }
      if (window.currentVolume !== undefined) {
        setTimeout(function(){
          setVolumeHost(window.currentVolume);
        }, 500);
      }
    })
    .catch(err => console.log(err));
}

function pauseTrackHost() {
  fetch('/pause')
    .then(response => response.json())
    .then(data => {
      if (data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Пауза: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        const ct = (data.current_time || 0) / 1000;
        const dur = (data.duration || 0) / 1000;
        document.getElementById("seekSlider").max = dur;
        document.getElementById("seekSlider").value = ct;
        document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
      }
    })
    .catch(err => console.log(err));
}

function nextTrackHost() {
  fetch('/next')
    .then(response => response.json())
    .then(data => {
      if (data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
      }
    })
    .catch(err => console.log(err));
}

function prevTrackHost() {
  fetch('/prev')
    .then(response => response.json())
    .then(data => {
      if (data.track) {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = "Сейчас играет: " + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
      }
    })
    .catch(err => console.log(err));
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
    .then(response => response.json())
    .catch(err => console.log(err));
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
}

function playTrackPlyr(trackPath) {
  fetch('/play?path=' + encodeURIComponent(trackPath))
    .then(response => response.json())
    .then(data => {
      let fileName = data.title || (data.track ? data.track.split(/[/\\]+/).pop() : trackPath.split(/[/\\]+/).pop());
      fileName = decodeURIComponent(fileName).replace(/&amp;/g, '&');
      window.currentTrackTitle = fileName;
      window.currentTrackGenre = (data.genre && data.genre.toLowerCase() !== "unknown") ? data.genre : "N/A";
      document.getElementById("now_playing").innerText = "Сейчас играет: " + window.currentTrackTitle;
      document.getElementById("genre").innerText = window.currentTrackGenre;
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
    .catch(err => console.log(err));
}

function updateStatusPlyr() {
  if (playerPlyr) {
    const ct = playerPlyr.currentTime || 0;
    const dur = playerPlyr.duration || 0;
    document.getElementById("seekSlider").max = dur;
    document.getElementById("seekSlider").value = ct;
    document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
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
}

// ========== Единая функция воспроизведения ==========
function playTrack(trackPath) {
  if (playbackMode === "host") {
    playTrackHost(trackPath);
  } else if (playbackMode === "plyr") {
    playTrackPlyr(trackPath);
  }
}

// ========== Обновление статуса ==========
function updateStatusHost() {
  fetch('/status')
    .then(response => response.json())
    .then(data => {
      if (data.status === "playing" || data.status === "paused") {
        let trackName = data.title || data.track.split(/[/\\]+/).pop();
        trackName = decodeURIComponent(trackName).replace(/&amp;/g, '&');
        document.getElementById("now_playing").innerText = (data.status === "playing" ? "Сейчас играет: " : "Пауза: ") + trackName;
        document.getElementById("genre").innerText = data.genre || "N/A";
        const ct = (data.current_time || 0) / 1000;
        const dur = (data.duration || 0) / 1000;
        document.getElementById("seekSlider").max = dur;
        document.getElementById("seekSlider").value = ct;
        document.getElementById("time_display").innerText = formatTime(ct) + " / " + formatTime(dur);
      } else {
        document.getElementById("now_playing").innerText = "Не играет";
        document.getElementById("genre").innerText = "N/A";
        document.getElementById("time_display").innerText = "0:00 / 0:00";
        document.getElementById("seekSlider").value = 0;
      }
    })
    .catch(err => console.log(err));
}

if (playbackMode === "host") {
  setInterval(updateStatusHost, 1000);
} else if (playbackMode === "plyr") {
  setInterval(updateStatusPlyr, 1000);
}

// ========== Обработчики управления ==========
document.addEventListener("DOMContentLoaded", function() {
  document.getElementById("play_btn")?.addEventListener("click", function(){
    if (playbackMode === "host") {
      if (document.getElementById("now_playing").innerText === "Не играет" && playlist.length > 0) {
        currentIndex = 0;
        playTrackHost(playlist[currentIndex]);
      }
    } else if (playbackMode === "plyr") {
      if (window.playerPlyr) {
        window.playerPlyr.play();
        document.getElementById("now_playing").innerText = "Сейчас играет: " + (window.currentTrackTitle || "Трек");
        document.getElementById("genre").innerText = window.currentTrackGenre || "N/A";
      }
    }
  });

  document.getElementById("stop_btn")?.addEventListener("click", function(){
    if (playbackMode === "host") {
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

  document.getElementById("seekSlider")?.addEventListener("input", function(){
    const newTimeSec = Number(this.value);
    if (playbackMode === "host") {
      seekHost(newTimeSec);
    } else if (playbackMode === "plyr") {
      seekPlyr(newTimeSec);
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
      pauseTrackHost();
    } else if (playbackMode === "plyr") {
      if (window.playerPlyr && !window.playerPlyr.paused) {
        pauseTrackPlyr();
      } else {
        resumeTrackPlyr();
      }
    }
  });


  $('#folderModal').on('shown.bs.modal', function () {
    console.log("Модальное окно открыто, инициализируем jsTree");
    $('#folderTree').jstree({
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
  }
});

// ========== Favorites, jsTree и сканирование ==========
function toggleFavButton(btn, isFav) {
  if (isFav) {
    btn.classList.remove("btn-warning");
    btn.classList.add("btn-danger");
    btn.textContent = "Fav";
  } else {
    btn.classList.remove("btn-danger");
    btn.classList.add("btn-warning");
    btn.textContent = "Fav";
  }
}

function loadFavoritesContent(options = { withFilter: false, withRating: false }) {
  fetch("/favorites_list")
    .then(response => response.json())
    .then(data => {
      document.getElementById("favoritesContent").innerHTML = data.html;
      // Если требуется настройка фильтра по жанрам
      if (options.withFilter) {
        setupFavoriteGenreFilter();
      }
      // Если требуется инициализация рейтинговых обработчиков
      if (options.withRating) {
        initFavoritesRating();
      }
    })
    .catch(err => console.error("Ошибка при загрузке избранного:", err));
}
// Использование функции при открытии модального окна (где нужен фильтр и рейтинг)
document.getElementById('favoritesModal')?.addEventListener('show.bs.modal', () => {
  loadFavoritesContent({ withFilter: true, withRating: true });
});

function addFavorite(path, btn) {
  if (btn.classList.contains("btn-danger")) {
    alert("Трек уже добавлен в избранное!");
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
      if (data.status === "success") {
        // Обновляем список избранного без фильтрации и инициализации рейтинга
        loadFavoritesContent({ withFilter: false, withRating: false });
      } else {
        toggleFavButton(btn, false);
        alert("Ошибка при добавлении трека: " + (data.error || ""));
      }
    })
    .catch(err => {
      toggleFavButton(btn, false);
    });
}

function removeFavorite(path, btn) {
  if (!confirm("Удалить трек из избранного?")) return;
  if (btn) toggleFavButton(btn, false);
  fetch('/remove_favorite', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: path })
  })
    .then(response => response.json())
    .then(data => {
      if (data.status === "removed") {
        // Обновляем список избранного после удаления
        loadFavoritesContent({ withFilter: false, withRating: false });
        if (btn) {
          var elems = document.querySelectorAll('[data-track="' + path + '"] button.fav-btn');
          elems.forEach(function(el) {
            toggleFavButton(el, false);
          });
        }
      } else {
        if (btn) toggleFavButton(btn, true);
        alert("Ошибка при удалении трека: " + (data.error || ""));
      }
    })
    .catch(err => {
      if (btn) toggleFavButton(btn, true);
    });
}

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
  // Если модальное окно уже существует, удаляем его
  const existingModal = document.getElementById("recommendModal");
  if (existingModal) {
    existingModal.remove();
  }
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  const modalElement = document.getElementById("recommendModal");
  const modalInstance = new bootstrap.Modal(modalElement);
  modalInstance.show();

  document.getElementById("goToTrack").onclick = function() {
    modalInstance.hide();
    // Перенаправляем пользователя на /autoplay, передавая параметр track с выбранным треком для автозапуска
    window.location.href = `/autoplay?track=${encodeURIComponent(rel_path)}`;
  };
}

document.addEventListener("DOMContentLoaded", function(){
  setTimeout(() => { // небольшой таймаут для уверенности, что все переменные загружены
    const autoplay = window.playerConfig.autoplay;
    // Определяем тип навигации (и для новых, и для старых браузеров)
    let navType = "navigate";
    if (performance.getEntriesByType) {
      const navEntry = performance.getEntriesByType("navigation")[0];
      if (navEntry) navType = navEntry.type; // "navigate" | "reload" | "back_forward" | "prerender"
    } else if (performance.navigation) {
      if (performance.navigation.type === 1) navType = "reload";
      if (performance.navigation.type === 0) navType = "navigate";
    }

    console.log("Autoplay параметр найден:", autoplay, "Тип навигации:", navType);

    // Только если это не reload (то есть не F5/обновление)
    if (navType !== "reload" && autoplay && autoplay.trim() !== "") {
      let idx = playlist.findIndex(item =>
        item.replace(/\\/g, '/') === autoplay.replace(/\\/g, '/')
      );
      if (idx !== -1) {
        currentIndex = idx;
        console.log("Запускаем трек из плейлиста:", playlist[currentIndex]);
        playTrack(playlist[currentIndex]);
      } else {
        console.warn("Не найден трек для автозапуска");
      }
    }
  }, 1000);
});

// Функция для выделения текущего трека в списке по значению data-track
function highlightCurrentTrack(currentTrackPath) {
  // Удаление класса выделения со всех элементов списка
  $("#playlist .list-group-item").removeClass("current");

  // Находим элемент, чье значение data-track совпадает с currentTrackPath (с заменой обратных слэшей на прямые)
  $("#playlist .list-group-item").filter(function() {
    return $(this).data("track").replace(/\\/g, '/') === currentTrackPath.replace(/\\/g, '/');
  }).addClass("current");
}

// Функция опроса сервера для получения текущего трека
function updateCurrentTrackHighlight() {
  $.ajax({
    url: "/current-track",
    type: "GET",
    dataType: "json",
    success: function(data) {
      if (data.currentTrack && data.currentTrack.trim() !== "") {
        highlightCurrentTrack(data.currentTrack);
      }
    },
    error: function(xhr, status, error) {
      console.error("Ошибка при получении текущего трека:", error);
    }
  });
}

// Запускаем периодическое опрашивание каждую 3 секунды
setInterval(updateCurrentTrackHighlight, 500);

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
    const folder = trackPath.substring(0, trackPath.lastIndexOf('/'));
    const url = `/browse?path=${encodeURIComponent(folder)}&autoplay=${encodeURIComponent(trackPath)}`;
    window.location.href = url;
  } else {
    if (typeof playTrack === "function") {
      playTrack(trackPath);
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
  // Собираем жанры
  const entries = document.querySelectorAll('#favoritesContent .fav-entry .text-muted');
  const genresSet = new Set();
  entries.forEach(e => {
    const genreMatch = e.textContent.match(/Genre:\s*(.*)/i);
    if (genreMatch && genreMatch[1]) genresSet.add(genreMatch[1].trim());
  });

  const select = document.getElementById('favGenreFilter');
  // Очистить, кроме "Все жанры"
  select.querySelectorAll('option:not([value="all"])').forEach(opt => opt.remove());
  genresSet.forEach(g => {
    if (g && g !== "Unknown") {
      const opt = document.createElement('option');
      opt.value = g;
      opt.textContent = g;
      select.appendChild(opt);
    }
  });
  select.style.display = genresSet.size > 0 ? '' : 'none';

  // Сбросить на "Все жанры" при открытии
  select.value = "all";
  // Вешаем обработчик
  select.onchange = function() {
    const selected = select.value;
    document.querySelectorAll('#favoritesContent .fav-entry').forEach(item => {
      const genreDiv = item.querySelector('.text-muted');
      const genre = genreDiv ? (genreDiv.textContent.match(/Genre:\s*(.*)/i)?.[1]?.trim() || "Unknown") : "Unknown";
      if (selected === "all" || genre === selected) {
        item.style.display = "";
      } else {
        item.style.display = "none";
      }
    });
  };
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

      star.addEventListener('dblclick', function() {
        currentRating = 0;
        ratingElem.setAttribute('data-rating', currentRating);
        updateStars(stars, currentRating);
        const trackId = ratingElem.closest('.fav-entry').getAttribute('data-track-id');
        saveRating(trackId, currentRating);
      });
    });
  });
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
    } else {
      console.error("Ошибка сохранения рейтинга для трека " + trackId);
    }
  })
  .catch(err => console.error("Ошибка:", err));
}





