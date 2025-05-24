//librosa_stats.js
document.addEventListener('DOMContentLoaded', function() {
    // Получаем путь к папке из data-атрибута
    let folder = document.getElementById('genreStatsRoot').dataset.folder;

    // Экспорт JSON
    let exportBtn = document.getElementById('exportJsonBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', function() {
            fetch(`/librosa-genre-stats-export?folder=${encodeURIComponent(folder)}&with_links=1`)
                .then(resp => resp.json())
                .then(data => {
                    let blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
                    let url = URL.createObjectURL(blob);
                    let a = document.createElement("a");
                    a.href = url;
                    a.download = "genre_stats.json";
                    a.click();
                    URL.revokeObjectURL(url);
                });
        });
    }

    // Клик по жанру
    document.querySelectorAll('.genre-row').forEach(row => {
        row.addEventListener('click', function() {
            let genre = this.dataset.genre;
            fetch(`/librosa-genre-tracks?folder=${encodeURIComponent(folder)}&genre=${encodeURIComponent(genre)}`)
                .then(resp => resp.json())
                .then(files => {
                    let modalBody = document.getElementById('genreTracksModalBody');
                    modalBody.innerHTML = '';
                    if (files.length === 0) {
                        modalBody.innerHTML = 'Нет треков для этого жанра.';
                    } else {
                        files.forEach(f => {
                            let div = document.createElement('div');
                            div.className = "mb-2";
                            // Нумерация треков + имя
                            div.innerHTML = `<b>${f.idx || ''}. ${f.name}</b><br>
    <audio controls src="${f.url}"></audio>`;
                            modalBody.appendChild(div);
                        });
                    }
                    let modal = new bootstrap.Modal(document.getElementById('genreTracksModal'));
                    modal.show();
                });
        });
    });

    // === КНОПКА СБРОСА КЭША ===
    const clearBtn = document.getElementById('clearCacheBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            fetch('/librosa-clear-cache', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            })
            .then(resp => resp.json())
            .then(data => {
                document.getElementById('clearCacheMsg').innerText = "Кэш очищен!";
                setTimeout(() => {
                    document.getElementById('clearCacheMsg').innerText = '';
                }, 2000);
                // Можно добавить автоматическое обновление статистики, если нужно
                // location.reload();
            });
        });
    }
});