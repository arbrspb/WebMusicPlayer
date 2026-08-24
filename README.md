# Web Music Player

Web Music Player — локальный веб-инструмент для воспроизведения, анализа и персонального подбора музыки. Он объединяет индекс коллекции, жанровую модель на 134 акустических признаках, опциональные YAMNet / Discogs Multi-EffNet / Whisper, оценки пользователя и импорт метаданных Rekordbox.

Репозиторий содержит только исходный код и воспроизводимые настройки. Музыкальные файлы, личные БД, пользовательские модели, Rekordbox-экспорты и сторонние веса моделей намеренно не публикуются.

## Возможности

- локальное воспроизведение через VLC и веб-интерфейс;
- безопасное дополняющее сканирование большой библиотеки;
- жанровая классификация Random Forest с калибровкой, порогами и quality gate;
- отдельные признаки стиля, языка, версии и DJ-категории;
- умный поиск похожих треков и персонализация по рейтингам;
- конструктор обучающей выборки, review спорных папок и треков;
- опциональные Rekordbox, YAMNet, Faster Whisper и Discogs Multi-EffNet;
- CPU-режим и опциональные CUDA-пути с безопасным fallback на CPU.

## Требования

- Python 3.11;
- VLC Media Player для host playback;
- Windows — основной проверенный сценарий; часть backend-логики переносима;
- для анализа — свободное место под локальную БД и кэши.

## Быстрый старт

```powershell
git clone https://github.com/arbrspb/WebMusicPlayer.git
cd WebMusicPlayer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-onnx-cpu.txt
Copy-Item config.example.json config.json
Copy-Item librosa_config.example.json librosa_config.json
Copy-Item folder_keywords.example.json folder_keywords.json
python run.py
```

Откройте `http://127.0.0.1:8080`. Для небольшого Windows-окна управления сервером можно запускать `python gui_server.py`.

После первого запуска укажите свою локальную или UNC-папку музыки в настройках. Значения `config.json`, `librosa_config.json` и персональные правила папок `folder_keywords.json` являются локальными и Git их игнорирует.

## CPU и CUDA

Базовая установка работает на CPU. Для YAMNet и Discogs Multi-EffNet установите ровно один вариант ONNX Runtime:

```powershell
# CPU
python -m pip install -r requirements-onnx-cpu.txt

# либо проверенный проектом CUDA-вариант
python -m pip uninstall -y onnxruntime
python -m pip install -r requirements-cuda.txt
```

Не устанавливайте `onnxruntime` и `onnxruntime-gpu` одновременно. Подробности Windows CUDA: [docs/ONNX_CUDA_RUNTIME.md](docs/ONNX_CUDA_RUNTIME.md).

## Сторонние модели

Веса моделей не входят в репозиторий:

- **Discogs Multi-EffNet** скачивается только по явному нажатию пользователя напрямую с Essentia, с проверкой SHA-256 и подтверждением условий лицензии;
- **Faster Whisper** загружает выбранную модель в локальный `models/faster-whisper/` при первом включении;
- **YAMNet ONNX** устанавливается пользователем вручную; готовый сторонний ONNX-файл проект не распространяет.

Источники, лицензии и ограничения описаны в [docs/THIRD_PARTY_MODELS.md](docs/THIRD_PARTY_MODELS.md). В частности, Discogs EffNet следует считать доступным только для некоммерческого использования, пока у пользователя нет отдельного коммерческого разрешения правообладателя.

## Локальные данные

Эти файлы создаются или изменяются на компьютере пользователя и не должны попадать в Git:

- `scan_results.db`, `favorite.db`, `training_features_checkpoint.db`;
- `genre_model.pkl`, `models/*.pkl`, `models/*.onnx`;
- `config.json`, `librosa_config.json`, `folder_keywords.json`, `training_dataset.json`;
- `genre_review_queue.json`, `learning_curves/`, логи и отчёты;
- `samples/`, `test_uploads/` и любые аудиофайлы;
- выгрузки Rekordbox с реальными путями и метаданными.

Перед обновлением GitHub выполните локальный аудит:

```powershell
python tools\prepare_github_release.py --audit-only
```

Для отдельной чистой копии без тяжёлой старой Git-истории используйте инструкцию [docs/GITHUB_RELEASE.md](docs/GITHUB_RELEASE.md).

## Обучение

Обучение запускается из конструктора выборки. Пользователь выбирает источники и стили, подтверждает разметку, проверяет спорные элементы и затем запускает RF. Кандидат заменяет рабочую модель только после quality gate. Папка `samples` опциональна и включается отдельно.

Rekordbox не обязателен: на новой установке он выключен. Если он нужен, пользователь самостоятельно импортирует локальный экспорт и включает источник в настройках.

## Безопасность публикации

`.gitignore` защищает новые локальные данные, но не удаляет файлы из старой Git-истории. Если в прежней истории уже были БД, музыка или крупные модели, используйте чистый экспорт либо осознанное переписывание истории. Не выполняйте force-push без резервной копии и понимания последствий.

## Лицензия проекта

Исходный код Web Music Player распространяется по MIT License — см. [LICENSE](LICENSE). Сторонние библиотеки и модели сохраняют собственные лицензии; MIT-лицензия проекта на них не распространяется.
