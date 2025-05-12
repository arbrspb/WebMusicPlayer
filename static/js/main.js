// main.js 12-05-25

// В начале main.js
const autoplay = window.playerConfig.autoplay;
document.addEventListener("DOMContentLoaded", function() {
  if (autoplay) {
    // Найти индекс в плейлисте и сразу проиграть
    let idx = playlist.findIndex(item => item.replace(/\\/g, '/') === autoplay.replace(/\\/g, '/'));
    if (idx !== -1) {
      currentIndex = idx;
      playTrack(playlist[currentIndex]);
    }
  }
});

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
  // Обновляем индекс ТОЛЬКО тут, до запроса!
  const idx = playlist.findIndex(p =>
    p.replace(/\\/g, '/').replace(/\/+/g, '/').trim() === trackPath.replace(/\\/g, '/').replace(/\/+/g, '/').trim()
  );
  if (idx !== -1) currentIndex = idx;
  updateTrackHighlight();

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
  // Обновляем индекс до запроса!
  const idx = playlist.findIndex(p =>
    p.replace(/\\/g, '/').replace(/\/+/g, '/').trim() === trackPath.replace(/\\/g, '/').replace(/\/+/g, '/').trim()
  );
  if (idx !== -1) currentIndex = idx;
  updateTrackHighlight();

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
  // Обновляем индекс до вызова конкретного режима!
  const idx = playlist.findIndex(p =>
    p.replace(/\\/g, '/').replace(/\/+/g, '/').trim() === trackPath.replace(/\\/g, '/').replace(/\/+/g, '/').trim()
  );
  if (idx !== -1) currentIndex = idx;
  updateTrackHighlight();

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
  if (playlist.length === 0) return;

  if (playbackMode === "host") {
    // Шлём команду серверу
    nextTrackHost();
    // Найти трек, который теперь воспроизводится (через /status)
    setTimeout(updateCurrentIndexFromStatus, 400); // небольшая задержка, чтобы сервер успел обновить статус
  } else if (playbackMode === "plyr") {
    let newIndex = (currentIndex + 1) % playlist.length;
    playTrack(playlist[newIndex]);
  }
});

document.getElementById("prev_btn")?.addEventListener("click", function(){
  if (playlist.length === 0) return;

  if (playbackMode === "host") {
    prevTrackHost();
    setTimeout(updateCurrentIndexFromStatus, 400);
  } else if (playbackMode === "plyr") {
    let newIndex = (currentIndex - 1 + playlist.length) % playlist.length;
    playTrack(playlist[newIndex]);
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

document.getElementById("recommend_btn")?.addEventListener("click", function(){
  fetch('/recommend')
    .then(response => response.json())
    .then(data => {
      if (data.redirect) {
        // Показать модальное окно с выбором
        showRecommendModal(data.filename, data.folder, data.redirect);
      } else if (data.error) {
        alert("Похожий трек не найден: " + data.error);
      }
    })
    .catch(err => console.log(err));
});

// Модальное окно выбора действия (добавь элемент recommendModal в html)
function showRecommendModal(filename, folder, rel_path) {
  // Создаём модалку динамически или используй Bootstrap Modal
  const modalHtml = `
    <div class="modal fade" id="recommendModal" tabindex="-1">
      <div class="modal-dialog"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title">Похожий трек найден</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <p>Похожий трек: <b>${filename}</b><br>Папка: <i>${folder}</i></p>
          <p>Что сделать?</p>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-primary" id="goToTrack">Перейти к папке и воспроизвести</button>
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Остаться здесь</button>
        </div>
      </div></div>
    </div>`;
  // Удаляем старую модалку если есть
  document.getElementById('recommendModal')?.remove();
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  const modal = new bootstrap.Modal(document.getElementById('recommendModal'));
  modal.show();

  document.getElementById('goToTrack').onclick = function() {
    modal.hide();
    // Перейти в нужную папку
    window.location.href = `/browse?path=${encodeURIComponent(folder)}&autoplay=${encodeURIComponent(rel_path)}`;
  };
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

  document.getElementById("favoritesModal")?.addEventListener("show.bs.modal", loadFavorites);

  $('#folderModal').on('shown.bs.modal', function () {
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
    if (selected.length) {
      const path = selected[0];
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
function loadFavorites() {
  fetch('/favorites_list')
    .then(response => response.json())
    .then(data => {
      document.getElementById("favoritesContent").innerHTML = data.html;
    })
    .catch(err => {});
}
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
        loadFavorites();
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
        loadFavorites();
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

// функция для динамического выделения трека в боковой панели
function updateTrackHighlight() {
  document.querySelectorAll('#playlist .list-group-item').forEach(el => el.classList.remove('playing'));
  let nowPath = playlist[currentIndex];
  // Для отладки:
  console.log('[updateTrackHighlight] currentIndex:', currentIndex, ', nowPath:', nowPath);
  let el = Array.from(document.querySelectorAll('#playlist .list-group-item'))
    .find(el =>
      el.getAttribute('data-track') &&
      el.getAttribute('data-track').replace(/\\/g, '/').replace(/\/+/g, '/').trim() ===
      nowPath.replace(/\\/g, '/').replace(/\/+/g, '/').trim()
    );
  if (el) {
    el.classList.add('playing');
    // Для отладки:
    console.log('[updateTrackHighlight] выделено:', el.getAttribute('data-track'));
  } else {
    console.log('[updateTrackHighlight] не найден элемент для выделения!');
  }
}

function updateCurrentIndexFromStatus() {
  fetch('/status')
    .then(response => response.json())
    .then(data => {
      if (data && data.track) {
        const playingPath = data.track.replace(/\\/g, '/').replace(/\/+/g, '/').trim();
        const idx = playlist.findIndex(p =>
          p.replace(/\\/g, '/').replace(/\/+/g, '/').trim().endsWith(playingPath)
        );
        if (idx !== -1) {
          currentIndex = idx;
          updateTrackHighlight();
        }
      }
    })
    .catch(err => console.log('Ошибка при получении статуса для выделения:', err));
}