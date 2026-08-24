(() => {
  "use strict";

  const app = document.getElementById("catalog-app");
  const message = document.getElementById("message");
  const pathInput = document.getElementById("track-path");
  const referenceInput = document.getElementById("reference-paths");
  const embedded = app?.dataset.embedded === "true";
  let pollTimer = null;
  let currentResultItems = [];
  let queueItems = [];
  let branchHistory = [];
  let activeBranchId = null;
  let currentPlayingPath = "";
  let currentScoreWeights = {};
  let matchInProgress = false;
  let resultSort = {key: "similarity", direction: "desc"};
  const catalogSessionKey = "smartCatalogSessionV1";

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`;
  const normalizePath = (value) => String(value || "").trim().replaceAll("\\", "/").toLowerCase();

  function resultSortValue(item, key) {
    if (key === "track") return shortTrackName(item.path || item.rel_path || "");
    if (key === "category") return item.base_genre || item.genre || "Unknown";
    if (key === "character") return Number(item.energy || 0);
    return Number(item.similarity || 0);
  }

  function sortedResultItems(items) {
    const direction = resultSort.direction === "asc" ? 1 : -1;
    return [...items].sort((left, right) => {
      const leftValue = resultSortValue(left, resultSort.key);
      const rightValue = resultSortValue(right, resultSort.key);
      if (typeof leftValue === "string" || typeof rightValue === "string") {
        return String(leftValue).localeCompare(String(rightValue), "ru", {numeric: true, sensitivity: "base"}) * direction;
      }
      return (Number(leftValue) - Number(rightValue)) * direction;
    });
  }

  function renderSortHeaders() {
    document.querySelectorAll("#results-sort-controls [data-sort-column]").forEach((header) => {
      const key = header.dataset.sortColumn;
      const active = key === resultSort.key;
      const button = header.querySelector(".catalog-sort-button");
      const icon = button?.querySelector(".bi");
      header.setAttribute("aria-sort", active ? (resultSort.direction === "asc" ? "ascending" : "descending") : "none");
      button?.classList.toggle("is-active", active);
      if (button) button.title = active
        ? `Сейчас: ${resultSort.direction === "asc" ? "по возрастанию" : "по убыванию"}. Нажмите, чтобы изменить направление.`
        : "Сортировать по этому столбцу";
      if (icon) icon.className = `bi ${active ? (resultSort.direction === "asc" ? "bi-arrow-up" : "bi-arrow-down") : "bi-arrow-down-up"}`;
    });
  }

  function changeResultSort(key) {
    if (!key) return;
    if (resultSort.key === key) {
      resultSort.direction = resultSort.direction === "asc" ? "desc" : "asc";
    } else {
      resultSort = {key, direction: ["track", "category"].includes(key) ? "asc" : "desc"};
    }
    renderTracks(currentResultItems, document.getElementById("results-title").textContent || "Результаты");
  }

  function scorePart(label, value, weight, note = "") {
    if (value == null) return "";
    const numeric = Math.max(0, Math.min(1, Number(value || 0)));
    const weightLabel = Number.isFinite(Number(weight)) ? ` · вес ${Math.round(Number(weight) * 100)}%` : "";
    return `<div class="score-row" title="${escapeHtml(note)}">
      <span class="score-row-label">${escapeHtml(label)}</span>
      <span class="score-row-value">${escapeHtml(percent(numeric))}${escapeHtml(weightLabel)}</span>
      <span class="score-bar"><span style="width:${escapeHtml(percent(numeric))}"></span></span>
    </div>`;
  }

  function scoreExplanation(item, index) {
    const overall = item.similarity == null ? "Диагностика" : percent(item.similarity);
    const acoustic = item.acoustic_similarity;
    const semanticFallback = item.semantic_similarity == null;
    const deepFallback = item.deep_similarity == null;
    const rows = [
      scorePart(deepFallback ? "EffNet → акустика" : "EffNet", deepFallback ? acoustic : item.deep_similarity, currentScoreWeights.deep, deepFallback ? "Глубокий индекс ещё не построен; применена акустическая замена." : "Глубокое сходство звучания Discogs Multi-EffNet."),
      scorePart("Акустика", acoustic, currentScoreWeights.acoustic, "Компактные аудиопризнаки основного индекса."),
      scorePart("Характер", item.character_similarity, currentScoreWeights.character, "Энергия, плотность, яркость, танцевальность, вокал и настроение."),
      scorePart(semanticFallback ? "YAMNet → акустика" : "YAMNet", semanticFallback ? acoustic : item.semantic_similarity, currentScoreWeights.semantic, semanticFallback ? "YAMNet-вектор отсутствует; применена акустическая замена." : "Семантическое сходство по YAMNet."),
      scorePart("BPM", item.bpm_similarity, currentScoreWeights.bpm, "Близость темпа с учётом половинного и двойного BPM."),
      scorePart("Ваш вкус", item.personal_score, currentScoreWeights.personal, "Реальная оценка или прогноз персональной модели."),
    ].filter(Boolean).join("");
    const detailsId = `catalog-score-details-${index}`;
    return {
      summary: `<button class="score-explanation-toggle" type="button" data-score-details="${detailsId}" aria-controls="${detailsId}" aria-expanded="false"><strong>${escapeHtml(overall)}</strong><span class="muted small">Почему</span><i class="bi bi-chevron-down" aria-hidden="true"></i></button>`,
      detailsId,
      details: `<div class="score-breakdown">${rows || '<span class="score-fallback">Для этой выдачи доступны не все компоненты.</span>'}</div>`,
    };
  }

  function applyCatalogTheme(theme) {
    const resolved = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.bsTheme = resolved;
  }

  applyCatalogTheme(localStorage.getItem("selectedTheme") || "light");

  function ratingMarkup(path, rating) {
    const value = Math.max(0, Math.min(5, Number(rating || 0)));
    const stars = [1, 2, 3, 4, 5].map((star) =>
      `<button type="button" class="catalog-rating-star ${star <= value ? "is-rated" : ""}" data-rating-value="${star}" aria-label="${star} из 5" title="Оценить на ${star} из 5">★</button>`
    ).join("");
    return `<span class="catalog-rating" data-rating-path="${escapeHtml(path)}" data-rating="${value}" aria-label="Ваша оценка">${stars}<button type="button" class="catalog-rating-reset ${value ? "" : "invisible"}" data-rating-value="0" aria-label="Сбросить оценку" title="Сбросить оценку">×</button></span>`;
  }

  function updateCatalogRating(path, rating) {
    const normalized = normalizePath(path);
    const value = Math.max(0, Math.min(5, Number(rating || 0)));
    const updateItems = (items) => (items || []).forEach((item) => {
      if (normalizePath(item.path || item.rel_path) === normalized) item.user_rating = value || null;
    });
    updateItems(currentResultItems);
    updateItems(queueItems);
    branchHistory.forEach((branch) => updateItems(branch.items));
    document.querySelectorAll(".catalog-rating[data-rating-path]").forEach((element) => {
      if (normalizePath(element.dataset.ratingPath) !== normalized) return;
      element.dataset.rating = String(value);
      element.querySelectorAll(".catalog-rating-star").forEach((star) => {
        const selected = Number(star.dataset.ratingValue || 0) <= value;
        star.classList.toggle("is-rated", selected);
        star.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      element.querySelector(".catalog-rating-reset")?.classList.toggle("invisible", value === 0);
    });
    saveCatalogSession();
  }

  async function saveCatalogRating(path, rating) {
    const element = [...document.querySelectorAll(".catalog-rating[data-rating-path]")]
      .find((candidate) => normalizePath(candidate.dataset.ratingPath) === normalizePath(path));
    const previous = Number(element?.dataset.rating || 0);
    updateCatalogRating(path, rating);
    try {
      const data = await api("/api/track-rating", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({path, rating}),
      });
      updateCatalogRating(data.path || path, data.rating || 0);
      postToPlayer("catalog-rated", {path: data.path || path, rating: Number(data.rating || 0)});
      showMessage(Number(data.rating) ? `Оценка ${Number(data.rating)}★ сохранена и будет учитываться в персонализации.` : "Оценка сброшена.", "success");
    } catch (error) {
      updateCatalogRating(path, previous);
      throw error;
    }
  }

  function shortTrackName(path) {
    const value = String(path || "").replaceAll("\\", "/");
    return value.split("/").pop() || value || "трек";
  }

  function saveCatalogSession() {
    try {
      sessionStorage.setItem(catalogSessionKey, JSON.stringify({
        queueItems: queueItems.slice(0, 100),
        branchHistory: branchHistory.slice(0, 10),
        activeBranchId,
      }));
    } catch (error) {
      console.debug("Не удалось сохранить сессию подбора", error);
    }
  }

  function restoreCatalogSession() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(catalogSessionKey) || "{}");
      queueItems = Array.isArray(saved.queueItems) ? saved.queueItems.slice(0, 100) : [];
      branchHistory = Array.isArray(saved.branchHistory) ? saved.branchHistory.slice(0, 10) : [];
      activeBranchId = branchHistory.some((branch) => branch.id === saved.activeBranchId)
        ? saved.activeBranchId : (branchHistory[0]?.id || null);
    } catch (error) {
      queueItems = [];
      branchHistory = [];
      activeBranchId = null;
      sessionStorage.removeItem(catalogSessionKey);
    }
  }

  function postToPlayer(type, payload = {}) {
    if (!embedded || window.parent === window) return;
    window.parent.postMessage({type, ...payload}, window.location.origin);
  }

  function showMessage(text, kind = "info") {
    message.className = `alert alert-${kind}`;
    message.textContent = text;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function trackPath() {
    return pathInput.value.trim();
  }

  function playUrl(path) {
    const normalized = String(path || "").replaceAll("\\", "/");
    const slash = normalized.lastIndexOf("/");
    const folder = slash >= 0 ? normalized.slice(0, slash) : "";
    return `/browse?path=${encodeURIComponent(folder)}&autoplay=${encodeURIComponent(normalized)}`;
  }

  function playTrackFromCatalog(path) {
    if (!path) return;
    currentPlayingPath = path;
    updatePlayingMarkers();
    if (embedded) {
      postToPlayer("catalog-play", {path});
    } else {
      window.location.href = playUrl(path);
    }
  }

  async function loadStats() {
    const stats = await api("/api/intelligence/stats");
    const readiness = document.getElementById("catalog-readiness-notice");
    if (readiness) {
      if (!Number(stats.scan_tracks || 0)) {
        readiness.className = "alert alert-warning d-flex flex-wrap justify-content-between align-items-center gap-2";
        readiness.innerHTML = `<span><strong>Сначала нужен основной индекс.</strong> После сканирования каталог сможет строить профили и подбирать похожие треки.</span><a class="btn btn-sm btn-outline-dark" href="/settings#settings-library">Открыть обработку коллекции</a>`;
      } else if (!Number(stats.profiled_tracks || 0) || Number(stats.pending_tracks || 0) > 0) {
        readiness.className = "alert alert-info d-flex flex-wrap justify-content-between align-items-center gap-2";
        readiness.innerHTML = `<span><strong>Каталог ещё не синхронизирован.</strong> В основном индексе ${Number(stats.scan_tracks || 0).toLocaleString("ru-RU")} треков; ожидают профиля ${Number(stats.pending_tracks || 0).toLocaleString("ru-RU")}.</span><button class="btn btn-sm btn-info" type="button" data-catalog-action="sync">Обновить каталог</button>`;
      } else {
        readiness.className = "alert d-none";
        readiness.innerHTML = "";
      }
    }
    const cards = [
      ["В базе", stats.scan_tracks, "просканированных треков"],
      ["Профили", stats.profiled_tracks, `покрытие ${percent(stats.coverage)}`],
      ["Векторы", stats.embedded_tracks, "компактный акустический поиск"],
      ["Discogs‑EffNet", stats.deep_tracks, `глубокое покрытие ${percent(stats.deep_coverage)}`],
      ["YAMNet", stats.semantic_tracks, "глубоко проанализировано"],
      ["Ожидают", stats.pending_tracks, "новых/изменённых профилей"],
      ["Стили обновлены", stats.model_style_tracks, "решения текущей модели"],
      ["Средняя энергия", percent(stats.average_energy), "по всей коллекции"],
      ["Средний вокал", percent(stats.average_vocalness), "ориентировочная оценка"],
    ];
    document.getElementById("stats-cards").innerHTML = cards.map(([title, value, hint]) => `
      <div class="col-6 col-md-4 col-xl-2"><div class="card metric h-100"><div class="card-body">
        <div class="muted small">${escapeHtml(title)}</div><div class="metric-value">${escapeHtml(value)}</div>
        <div class="muted small">${escapeHtml(hint)}</div>
      </div></div></div>`).join("");
  }

  async function loadProgress() {
    const data = await api("/api/intelligence/progress");
    const total = Number(data.total || 0);
    const processed = Number(data.processed || 0);
    const ratio = total ? Math.min(1, processed / total) : 0;
    const bar = document.getElementById("index-progress-bar");
    bar.style.width = percent(ratio);
    bar.textContent = percent(ratio);
    bar.classList.toggle("progress-bar-animated", Boolean(data.running));
    document.getElementById("index-progress-text").textContent =
      `Статус: ${data.status || "idle"}. Обработано ${processed.toLocaleString("ru-RU")} из ${total.toLocaleString("ru-RU")}`
      + (data.error ? `. Ошибка: ${data.error}` : "");
    if (data.running && !pollTimer) pollTimer = setInterval(refreshProgress, 1500);
    if (!data.running && pollTimer) {
      clearInterval(pollTimer); pollTimer = null;
      await Promise.all([loadStats(), loadCollections()]);
    }
  }

  async function refreshProgress() {
    try { await loadProgress(); } catch (error) { showMessage(error.message, "danger"); }
  }

  async function startIndex() {
    const rawLimit = document.getElementById("index-limit").value.trim();
    const payload = {dimensions: 32};
    if (rawLimit) payload.limit = Number(rawLimit);
    await api("/api/intelligence/index/start", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    showMessage("Построение индекса запущено. Можно оставить страницу открытой.", "success");
    await loadProgress();
  }

  async function syncIndex() {
    await api("/api/intelligence/sync/start", {method: "POST"});
    showMessage("Запущено добавление новых треков и обновление стилей текущей моделью. MP3 повторно не читаются.", "success");
    await loadProgress();
  }

  function metricCard(label, value, extra = "") {
    return `<div class="col-6 col-md-4 col-xl-2"><div class="card h-100"><div class="card-body">
      <div class="d-flex justify-content-between"><span>${escapeHtml(label)}</span><strong>${percent(value)}</strong></div>
      <div class="progress profile-bar mt-2"><div class="progress-bar" style="width:${percent(value)}"></div></div>
      <small class="muted">${escapeHtml(extra)}</small>
    </div></div></div>`;
  }

  function renderProfile(item) {
    const profile = item.profile || item;
    document.getElementById("profile-summary").innerHTML = `
      <span class="badge text-bg-success me-2">стиль: ${escapeHtml(item.model_base_genre || item.base_genre || item.genre || "Unknown")}</span>
      <span class="badge text-bg-light me-2">язык: ${escapeHtml(item.model_language || item.language || "Unknown")}</span>
      <span class="badge text-bg-primary me-2">роль: ${escapeHtml(profile.role || "—")}</span>
      <span class="badge text-bg-secondary me-2">настроение: ${escapeHtml(profile.mood || "—")}</span>
      <span class="badge text-bg-dark me-2">BPM: ${Number(profile.bpm || 0).toFixed(1)}</span>
      <span class="badge text-bg-info me-2">${escapeHtml(item.semantic_source ? "YAMNet 1024D" : "акустика 32D")}</span>
      ${item.user_rating != null
        ? `<span class="badge text-bg-warning">ваша оценка: ${Number(item.user_rating).toFixed(0)}★</span>`
        : (item.predicted_rating != null
          ? `<span class="badge text-bg-warning">прогноз: ${Number(item.predicted_rating).toFixed(1)}★</span>`
          : "")}`;
    document.getElementById("profile-metrics").innerHTML = [
      ["Энергия", profile.energy], ["Плотность", profile.density],
      ["Яркость", profile.brightness], ["Танцевальность", profile.danceability],
      ["Вокал", profile.vocalness], ["Позитивность", profile.valence],
    ].map(([label, value]) => metricCard(label, value)).join("");
  }

  async function loadProfile() {
    if (!trackPath()) throw new Error("Сначала укажите путь трека");
    const item = await api(`/api/intelligence/profile?path=${encodeURIComponent(trackPath())}`);
    renderProfile(item);
    showMessage("Профиль трека загружен.", "success");
  }

  async function deepAnalyze() {
    if (!trackPath()) throw new Error("Сначала укажите путь трека");
    showMessage("Идёт глубокий анализ трека и YAMNet-вектора…", "warning");
    const item = await api("/api/intelligence/analyze-current", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: trackPath()}),
    });
    renderProfile(item);
    showMessage("Глубокий профиль сохранён.", "success");
    await loadStats();
  }

  function renderTracks(items, title) {
    currentResultItems = Array.isArray(items) ? items : [];
    document.getElementById("results-title").textContent = title;
    document.getElementById("results-count").textContent = String(currentResultItems.length);
    const body = document.getElementById("results-body");
    if (!currentResultItems.length) {
      body.innerHTML = `<tr><td colspan="5" class="muted">Ничего не найдено.</td></tr>`;
      postToPlayer("catalog-results", {count: 0, title});
      return;
    }
    const displayedItems = sortedResultItems(currentResultItems);
    body.innerHTML = displayedItems.map((item, index) => {
      const path = item.path || item.rel_path;
      const rating = item.user_rating != null
        ? `; ваши ${Number(item.user_rating).toFixed(0)}★`
        : (item.predicted_rating != null ? `; прогноз ${Number(item.predicted_rating).toFixed(1)}★` : "");
      const character = `${item.role || "—"}; E ${percent(item.energy)}; V ${percent(item.vocalness)}${rating}`;
      const reasons = Array.isArray(item.reasons) && item.reasons.length ? `<div class="muted small">${escapeHtml(item.reasons.join(", "))}</div>` : "";
      const score = scoreExplanation(item, index);
      return `<tr class="catalog-result-row" data-track-path="${escapeHtml(path)}">
        <td class="track-path"><strong class="track-title" title="${escapeHtml(shortTrackName(path))}">${escapeHtml(shortTrackName(path))}</strong><div class="muted small track-path-line" title="${escapeHtml(path)}">${escapeHtml(path)}</div>${reasons}</td>
        <td>${escapeHtml(item.base_genre || item.genre || "Unknown")}<div class="muted small">${escapeHtml(item.language || "Unknown")}</div></td>
        <td>${escapeHtml(character)}</td><td>${score.summary}</td>
        <td><div class="d-flex flex-wrap gap-1 track-actions">
          ${ratingMarkup(path, item.user_rating)}
          <button class="btn btn-sm btn-primary play-catalog-track" data-path="${escapeHtml(path)}" title="Воспроизвести, не закрывая подбор"><i class="bi bi-play-fill"></i><span class="track-action-label"> Играть</span></button>
          <button class="btn btn-sm btn-outline-secondary queue-catalog-track" data-path="${escapeHtml(path)}" title="Добавить в очередь"><i class="bi bi-list-ul"></i><span class="track-action-label"> В очередь</span></button>
          <button class="btn btn-sm btn-outline-info branch-catalog-track" data-path="${escapeHtml(path)}" title="Начать новую ветку похожих треков"><i class="bi bi-diagram-2"></i><span class="track-action-label"> Похожие</span></button>
          <button class="btn btn-sm btn-outline-secondary add-reference" data-path="${escapeHtml(path)}" title="Добавить как ещё один понравившийся эталон">+ эталон</button>
        </div></td>
      </tr>
      <tr class="catalog-result-details d-none" id="${score.detailsId}">
        <td colspan="5"><div class="catalog-result-details-panel">${score.details}</div></td>
      </tr>`;
    }).join("");
    renderSortHeaders();
    updatePlayingMarkers();
    postToPlayer("catalog-results", {count: currentResultItems.length, title});
  }

  function makeBranchId() {
    return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function saveBranch(referencesUsed, title, items, weights = {}) {
    const branch = {
      id: makeBranchId(),
      references: [...referencesUsed],
      title,
      items: Array.isArray(items) ? items : [],
      weights: {...weights},
      createdAt: Date.now(),
    };
    branchHistory.unshift(branch);
    branchHistory = branchHistory.slice(0, 25);
    activeBranchId = branch.id;
    renderHistory();
    saveCatalogSession();
  }

  function restoreBranch(id) {
    const branch = branchHistory.find((item) => item.id === id);
    if (!branch) return;
    activeBranchId = branch.id;
    referenceInput.value = branch.references.join("\n");
    pathInput.value = branch.references[0] || "";
    currentScoreWeights = {...(branch.weights || {})};
    renderTracks(branch.items, branch.title);
    renderHistory();
    saveCatalogSession();
    switchSessionView("results");
    postToPlayer("catalog-reference", {path: branch.references[0] || "", label: shortTrackName(branch.references[0])});
    showMessage("Предыдущая ветка подбора восстановлена.", "success");
  }

  function renderHistory() {
    document.getElementById("history-count").textContent = String(branchHistory.length);
    const body = document.getElementById("history-body");
    if (!branchHistory.length) {
      body.innerHTML = `<div class="muted">История появится после первого подбора.</div>`;
      return;
    }
    body.innerHTML = branchHistory.map((branch) => `
      <div class="session-item ${branch.id === activeBranchId ? "border-info" : ""}">
        <div class="d-flex justify-content-between gap-2 align-items-start">
          <div class="min-w-0"><strong>${escapeHtml(shortTrackName(branch.references[0]))}</strong>
            <div class="muted small">Эталонов: ${branch.references.length} · результатов: ${branch.items.length} · ${new Date(branch.createdAt).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"})}</div>
          </div>
          <button class="btn btn-sm btn-outline-info restore-branch" data-branch-id="${escapeHtml(branch.id)}">Вернуться</button>
        </div>
      </div>`).join("");
  }

  function queueContains(path) {
    const normalized = normalizePath(path);
    return queueItems.some((item) => normalizePath(item.path || item.rel_path) === normalized);
  }

  function addToQueue(path) {
    if (!path || queueContains(path)) {
      showMessage("Этот трек уже находится в очереди.", "warning");
      return;
    }
    const item = currentResultItems.find((candidate) => normalizePath(candidate.path || candidate.rel_path) === normalizePath(path)) || {path};
    queueItems.push(item);
    renderQueue();
    saveCatalogSession();
    switchSessionView("queue");
    showMessage("Трек добавлен в очередь подбора.", "success");
  }

  function renderQueue() {
    document.getElementById("queue-count").textContent = String(queueItems.length);
    const body = document.getElementById("queue-body");
    if (!queueItems.length) {
      body.innerHTML = `<div class="muted">Очередь пока пуста.</div>`;
      postToPlayer("catalog-queue", {count: 0});
      return;
    }
    body.innerHTML = queueItems.map((item, index) => {
      const path = item.path || item.rel_path;
      return `<div class="session-item" data-track-path="${escapeHtml(path)}">
        <div class="d-flex justify-content-between gap-2 align-items-center">
          <div><strong>${index + 1}. ${escapeHtml(shortTrackName(path))}</strong><div class="muted small">${escapeHtml(item.base_genre || item.genre || "Unknown")}</div>${ratingMarkup(path, item.user_rating)}</div>
          <div class="d-flex flex-wrap gap-1">
            <button class="btn btn-sm btn-info play-queue-track" data-path="${escapeHtml(path)}" title="Воспроизвести"><i class="bi bi-play-fill"></i></button>
            <button class="btn btn-sm btn-outline-warning branch-queue-track" data-path="${escapeHtml(path)}" title="Подобрать похожие"><i class="bi bi-diagram-2"></i></button>
            <button class="btn btn-sm btn-outline-secondary move-queue-up" data-index="${index}" title="Выше" ${index === 0 ? "disabled" : ""}><i class="bi bi-arrow-up"></i></button>
            <button class="btn btn-sm btn-outline-secondary move-queue-down" data-index="${index}" title="Ниже" ${index === queueItems.length - 1 ? "disabled" : ""}><i class="bi bi-arrow-down"></i></button>
            <button class="btn btn-sm btn-outline-danger remove-queue-track" data-index="${index}" title="Убрать"><i class="bi bi-x-lg"></i></button>
          </div>
        </div>
      </div>`;
    }).join("");
    updatePlayingMarkers();
    postToPlayer("catalog-queue", {count: queueItems.length});
  }

  function switchSessionView(view) {
    ["results", "queue", "history"].forEach((name) => {
      document.getElementById(`session-${name}-view`)?.classList.toggle("d-none", name !== view);
      document.querySelector(`[data-session-view="${name}"]`)?.classList.toggle("active", name === view);
    });
  }

  function updatePlayingMarkers() {
    document.querySelectorAll("[data-track-path]").forEach((element) => {
      element.classList.toggle("is-playing", Boolean(currentPlayingPath) && normalizePath(element.dataset.trackPath) === normalizePath(currentPlayingPath));
    });
  }

  function selectResultRow(row) {
    document.querySelectorAll(".catalog-result-row.is-selected").forEach((element) => {
      if (element !== row) element.classList.remove("is-selected");
    });
    row?.classList.add("is-selected");
  }

  function toggleScoreDetails(button) {
    const details = document.getElementById(button.dataset.scoreDetails || "");
    if (!details) return;
    const expanded = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    details.classList.toggle("d-none", !expanded);
  }

  async function startBranch(path) {
    if (!path) return;
    referenceInput.value = path;
    pathInput.value = path;
    postToPlayer("catalog-reference", {path, label: shortTrackName(path)});
    switchSessionView("results");
    await matchReferences();
  }

  function references() {
    return [...new Set(referenceInput.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean))];
  }

  function optionalNumber(id, divisor = 1) {
    const raw = document.getElementById(id).value.trim();
    return raw === "" ? null : Number(raw) / divisor;
  }

  function collectFilters() {
    return {
      style: document.getElementById("filter-style").value,
      language: document.getElementById("filter-language").value,
      role: document.getElementById("filter-role").value,
      mood: document.getElementById("filter-mood").value,
      vocal_mode: document.getElementById("filter-vocal").value,
      bpm_min: optionalNumber("filter-bpm-min"),
      bpm_max: optionalNumber("filter-bpm-max"),
      energy_min: optionalNumber("filter-energy-min", 100),
      energy_max: optionalNumber("filter-energy-max", 100),
      personal_min: optionalNumber("filter-personal-min"),
      clean_only: document.getElementById("clean-only").checked,
    };
  }

  function filtersAsQuery() {
    const params = new URLSearchParams();
    Object.entries(collectFilters()).forEach(([key, value]) => {
      if (value !== null && value !== "" && value !== false && value !== "any") params.set(key, String(value));
    });
    const scope = document.getElementById("scope-prefix").value.trim();
    if (scope) params.set("scope_prefix", scope);
    return params;
  }

  async function matchReferences() {
    if (matchInProgress) return;
    const selectedReferences = references();
    if (!selectedReferences.length) throw new Error("Добавьте хотя бы один эталонный трек");
    const matchButton = document.getElementById("match-references");
    matchInProgress = true;
    if (matchButton) {
      matchButton.disabled = true;
      matchButton.textContent = "Подбираем…";
    }
    postToPlayer("catalog-reference", {path: selectedReferences[0], label: shortTrackName(selectedReferences[0])});
    showMessage("Сравниваю кандидатов с эталоном…", "info");
    try {
      const data = await api("/api/intelligence/match", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          references: selectedReferences,
          filters: collectFilters(),
          scope_prefix: document.getElementById("scope-prefix").value.trim(),
          exclude_versions: document.getElementById("exclude-versions").checked,
          limit: Number(document.getElementById("result-limit").value || 20),
        }),
      });
      const referencesUsed = Array.isArray(data.references_used) ? data.references_used : [];
      const missingReferences = Array.isArray(data.missing_references) ? data.missing_references : [];
      const candidateCount = Number(data.candidate_count || 0);
      const items = Array.isArray(data.items) ? data.items : [];
      const title = `Лучшие совпадения · эталонов: ${referencesUsed.length} · кандидатов: ${candidateCount}`;
      currentScoreWeights = {...(data.weights || {})};
      renderTracks(items, title);
      saveBranch(selectedReferences, title, items, currentScoreWeights);
      switchSessionView("results");
      const historyCount = document.getElementById("recommendation-history-count");
      if (historyCount) historyCount.textContent = `В cooldown: ${Number(data.recommendation_history?.tracks || 0)}.`;
      if (missingReferences.length) {
        showMessage(`Не найдены в индексе: ${missingReferences.join("; ")}`, "warning");
      } else if (!items.length) {
        showMessage("Ничего не найдено по текущим условиям. Попробуйте ослабить фильтры.", "warning");
      } else {
        showMessage(`Подобрано ${items.length} треков.`, "success");
      }
      await loadCollections();
    } finally {
      matchInProgress = false;
      if (matchButton) {
        matchButton.disabled = false;
        matchButton.textContent = "Подобрать лучшие";
      }
    }
  }

  async function loadVersions() {
    const data = await api(`/api/intelligence/versions?path=${encodeURIComponent(trackPath())}`);
    renderTracks(data.items, "Версии и возможные дубликаты");
  }

  async function loadCollections() {
    const query = filtersAsQuery();
    const data = await api(`/api/intelligence/collections?${query.toString()}`);
    document.getElementById("collections").innerHTML = data.items.map((item) => `
      <div class="col-sm-6 col-lg-3"><div class="card collection-card h-100" data-slug="${escapeHtml(item.slug)}">
        <div class="card-body"><div class="d-flex justify-content-between"><h5>${escapeHtml(item.title)}</h5>
        <span class="badge text-bg-primary">${Number(item.count).toLocaleString("ru-RU")}</span></div>
        <p class="muted mb-0">${escapeHtml(item.description)}</p></div>
      </div></div>`).join("");
    document.querySelectorAll(".collection-card").forEach((card) => card.addEventListener("click", async () => {
      try {
        const collectionQuery = filtersAsQuery();
        collectionQuery.set("limit", "100");
        const result = await api(`/api/intelligence/collections/${encodeURIComponent(card.dataset.slug)}?${collectionQuery.toString()}`);
        const definition = data.items.find((item) => item.slug === card.dataset.slug);
        renderTracks(result.items, definition?.title || "Умная коллекция");
      } catch (error) { showMessage(error.message, "danger"); }
    }));
  }

  function fillSelect(id, items) {
    const select = document.getElementById(id);
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = `${item.value} (${Number(item.count).toLocaleString("ru-RU")})`;
      select.appendChild(option);
    });
  }

  async function loadFilterOptions() {
    const options = await api("/api/intelligence/filter-options");
    fillSelect("filter-style", options.styles);
    fillSelect("filter-language", options.languages);
    fillSelect("filter-role", options.roles);
    fillSelect("filter-mood", options.moods);
  }

  function useReferenceFolder() {
    const first = references()[0] || trackPath();
    if (!first) throw new Error("Сначала укажите эталонный трек");
    const normalized = first.replaceAll("/", "\\");
    const slash = normalized.lastIndexOf("\\");
    document.getElementById("scope-prefix").value = slash >= 0 ? normalized.slice(0, slash) : "";
    loadCollections().catch((error) => showMessage(error.message, "danger"));
  }

  const guarded = (handler) => async (...args) => {
    try { await handler(...args); } catch (error) { showMessage(error.message, "danger"); }
  };
  document.getElementById("start-index").addEventListener("click", guarded(startIndex));
  document.getElementById("sync-index").addEventListener("click", guarded(syncIndex));
  document.getElementById("stop-index").addEventListener("click", guarded(async () => {
    await api("/api/intelligence/index/stop", {method: "POST"});
    showMessage("Запрошена безопасная остановка после текущего пакета.", "warning");
  }));
  document.getElementById("load-profile").addEventListener("click", guarded(loadProfile));
  document.getElementById("deep-analyze").addEventListener("click", guarded(deepAnalyze));
  document.getElementById("match-references").addEventListener("click", guarded(matchReferences));
  document.getElementById("catalog-readiness-notice")?.addEventListener("click", guarded(async (event) => {
    if (event.target.closest('[data-catalog-action="sync"]')) await syncIndex();
  }));
  document.getElementById("scope-reference-folder").addEventListener("click", guarded(async () => useReferenceFolder()));
  document.getElementById("load-versions").addEventListener("click", guarded(loadVersions));
  document.getElementById("session-view-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-session-view]");
    if (button) switchSessionView(button.dataset.sessionView);
  });
  document.getElementById("results-sort-controls")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sort-key]");
    if (button) changeResultSort(button.dataset.sortKey);
  });
  document.getElementById("clear-catalog-queue")?.addEventListener("click", () => {
    queueItems = [];
    renderQueue();
    saveCatalogSession();
    showMessage("Очередь очищена.", "info");
  });
  document.getElementById("clear-catalog-history")?.addEventListener("click", guarded(async () => {
    branchHistory = [];
    activeBranchId = null;
    renderHistory();
    saveCatalogSession();
    await api("/api/intelligence/recommendation-history", {method: "DELETE"});
    const historyCount = document.getElementById("recommendation-history-count");
    if (historyCount) historyCount.textContent = "";
    showMessage("История подбора очищена.", "info");
  }));
  document.getElementById("results-body").addEventListener("click", guarded(async (event) => {
    const resultRow = event.target.closest(".catalog-result-row");
    if (resultRow) selectResultRow(resultRow);
    const scoreToggle = event.target.closest(".score-explanation-toggle");
    if (scoreToggle) return toggleScoreDetails(scoreToggle);
    const ratingButton = event.target.closest(".catalog-rating [data-rating-value]");
    if (ratingButton) {
      const rating = ratingButton.closest(".catalog-rating");
      return saveCatalogRating(rating.dataset.ratingPath, Number(ratingButton.dataset.ratingValue || 0));
    }
    const playButton = event.target.closest(".play-catalog-track");
    if (playButton) return playTrackFromCatalog(playButton.dataset.path);
    const queueButton = event.target.closest(".queue-catalog-track");
    if (queueButton) return addToQueue(queueButton.dataset.path);
    const branchButton = event.target.closest(".branch-catalog-track");
    if (branchButton) return startBranch(branchButton.dataset.path);
    const referenceButton = event.target.closest(".add-reference");
    if (!referenceButton) return;
    const values = references();
    if (!values.includes(referenceButton.dataset.path)) values.push(referenceButton.dataset.path);
    referenceInput.value = values.join("\n");
    postToPlayer("catalog-reference", {path: values[0] || "", label: `${shortTrackName(values[0])} +${Math.max(0, values.length - 1)}`});
    showMessage("Трек добавлен к эталонам. Нажмите «Подобрать лучшие», чтобы уточнить общий профиль.", "success");
  }));
  document.getElementById("queue-body").addEventListener("click", guarded(async (event) => {
    const ratingButton = event.target.closest(".catalog-rating [data-rating-value]");
    if (ratingButton) {
      const rating = ratingButton.closest(".catalog-rating");
      return saveCatalogRating(rating.dataset.ratingPath, Number(ratingButton.dataset.ratingValue || 0));
    }
    const playButton = event.target.closest(".play-queue-track");
    if (playButton) return playTrackFromCatalog(playButton.dataset.path);
    const branchButton = event.target.closest(".branch-queue-track");
    if (branchButton) return startBranch(branchButton.dataset.path);
    const removeButton = event.target.closest(".remove-queue-track");
    if (removeButton) {
      queueItems.splice(Number(removeButton.dataset.index), 1);
      saveCatalogSession();
      return renderQueue();
    }
    const upButton = event.target.closest(".move-queue-up");
    const downButton = event.target.closest(".move-queue-down");
    const moveButton = upButton || downButton;
    if (!moveButton) return;
    const from = Number(moveButton.dataset.index);
    const to = from + (upButton ? -1 : 1);
    if (to < 0 || to >= queueItems.length) return;
    [queueItems[from], queueItems[to]] = [queueItems[to], queueItems[from]];
    renderQueue();
    saveCatalogSession();
  }));
  document.getElementById("history-body").addEventListener("click", (event) => {
    const button = event.target.closest(".restore-branch");
    if (button) restoreBranch(button.dataset.branchId);
  });

  window.addEventListener("message", (event) => {
    if (!embedded || event.origin !== window.location.origin || !event.data) return;
    const {type, path, autoMatch} = event.data;
    if (type === "catalog-theme") {
      applyCatalogTheme(event.data.theme);
      return;
    }
    if (type === "catalog-playing") {
      currentPlayingPath = String(path || "");
      updatePlayingMarkers();
      return;
    }
    if (type === "catalog-rating-updated" && path) {
      updateCatalogRating(path, Number(event.data.rating || 0));
      return;
    }
    if (type === "catalog-set-reference" && path) {
      referenceInput.value = path;
      pathInput.value = path;
      postToPlayer("catalog-reference", {path, label: shortTrackName(path)});
      if (autoMatch) guarded(async () => startBranch(path))();
    }
  });

  restoreCatalogSession();
  renderQueue();
  renderHistory();
  Promise.all([loadStats(), loadProgress(), loadFilterOptions()]).then(() => loadCollections()).then(async () => {
    const initialPath = pathInput.value.trim();
    if (initialPath && !embedded) await loadProfile().catch(() => {});
    if (initialPath && embedded) await matchReferences();
    postToPlayer("catalog-ready", {reference: initialPath});
  }).catch((error) => showMessage(error.message, "danger"));
})();
