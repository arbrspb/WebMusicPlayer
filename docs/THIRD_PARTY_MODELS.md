# Сторонние модели

Этот репозиторий не содержит сторонние веса моделей. Такой подход уменьшает размер Git, не публикует чужие бинарники повторно и оставляет принятие условий лицензии пользователю.

Это техническое описание, а не юридическая консультация.

## Discogs Multi-EffNet

- Источник: <https://essentia.upf.edu/models/feature-extractors/discogs-effnet/>
- Метаданные модели: `discogs_multi_embeddings-effnet-bs64-1.json` на сайте Essentia.
- Лицензия upstream-моделей: <https://github.com/MTG/essentia-models/blob/master/LICENSE>
- Ограничение: CC BY-NC-SA 4.0, то есть некоммерческое использование; upstream предлагает отдельную proprietary license для коммерческого применения.

Кнопка в Web Music Player делает только пользовательскую прямую загрузку с Essentia, требует явного подтверждения и проверяет закреплённую SHA-256. Файл сохраняется локально как `models/discogs_multi_embeddings-effnet-bs64-1.onnx` и игнорируется Git.

Upstream license буквально говорит о размещённых `*.pb`; отдельная формулировка для ONNX в публичном metadata не приведена. Поэтому проект применяет консервативную политику: ONNX также не распространяется и считается некоммерческим компонентом. Для коммерческого использования нужно получить разъяснение/лицензию MTG/UPF.

## Whisper и faster-whisper

- OpenAI Whisper: <https://github.com/openai/whisper> — код и веса MIT.
- faster-whisper: <https://github.com/SYSTRAN/faster-whisper> — MIT.

При первом использовании `WhisperModel` получает выбранный вариант модели через стандартный механизм faster-whisper/Hugging Face и сохраняет его в `models/faster-whisper/`. Каталог локальный и не публикуется.

## YAMNet

- Reference implementation: <https://github.com/tensorflow/models/tree/master/research/audioset/yamnet>
- License: Apache License 2.0 для TensorFlow Model Garden/YAMNet source.

Приложение ожидает совместимый ONNX-файл `yamnet.onnx`. Репозиторий не раздаёт готовую конверсию и не утверждает происхождение любого произвольного ONNX из интернета. Пользователь должен получить или конвертировать модель из доверенного источника и сохранить уведомления Apache 2.0.

## Практический вывод

Самостоятельная загрузка пользователем обычно безопаснее повторной публикации весов, но она не отменяет условия исходной лицензии. Особенно важно: Discogs Multi-EffNet нельзя автоматически считать разрешённым для коммерческого продукта. Все три компонента можно отключить; базовый RF и проигрывание продолжают работать без них.

