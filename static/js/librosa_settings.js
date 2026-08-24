//// librosa_settings.js 14-08-25 01-50
//document.addEventListener("DOMContentLoaded", function() {
//    const form = document.getElementById("librosa-form");
//    const status = document.getElementById("save-status");
//    const useRekordbox = document.getElementById("use_rekordbox");
//    const rekordboxSection = document.getElementById("rekordbox-section");
//    const rbBalanceSection = document.getElementById("rekordbox-balance-section");
//    const rekordboxXML = document.getElementById("rekordbox-xml");
//    const uploadBtn = document.getElementById("upload-rekordbox-btn");
//    const parseBtn = document.getElementById("parse-rekordbox-btn");
//    const statusBlock = document.getElementById("rekordbox-status-block");
//    const progressBlock = document.getElementById("rekordbox-progress");
//    const xmlRadio = document.getElementById("rekordbox-xml-radio");
//    const jsonRadio = document.getElementById("rekordbox-json-radio");
//    const xmlSection = document.getElementById("rekordbox-xml-section");
//    const jsonSection = document.getElementById("rekordbox-json-section");
//    if (xmlRadio && jsonRadio && xmlSection && jsonSection) {
//        xmlRadio.addEventListener("change", function() {
//            if (xmlRadio.checked) {
//                xmlSection.style.display = "block";
//                jsonSection.style.display = "none";
//            }
//        });
//        jsonRadio.addEventListener("change", function() {
//            if (jsonRadio.checked) {
//                xmlSection.style.display = "none";
//                jsonSection.style.display = "block";
//            }
//        });
//    }
//// ========== JSON загрузка ===========
//    const rekordboxJSON = document.getElementById("rekordbox-json");
//    const uploadJSONBtn = document.getElementById("upload-rekordbox-json-btn");
//    const parseJSONBtn = document.getElementById("parse-rekordbox-json-btn");
//    const jsonStatusBlock = document.getElementById("rekordbox-json-status-block");
//    const jsonProgressBlock = document.getElementById("rekordbox-json-progress");
//
//
//    function updateRekordboxJsonStatus() {
//            fetch("/librosa-settings/rekordbox-json-status")
//            .then(r => r.json())
//            .then(data => {
//                if (data.status === "ready") {
//                    jsonStatusBlock.innerHTML = `<span class="text-success">JSON-файл успешно загружен и готов к использованию.<br>Треков: ${data.count}</span>`;
//                } else if (data.status === "json_uploaded") {
//                    jsonStatusBlock.innerHTML = `<span class="text-primary">JSON-файл загружен, но ещё не распарсен. Нажмите "Парсить JSON".</span>`;
//                } else {
//                    jsonStatusBlock.innerHTML = `<span class="text-warning">JSON-файл не загружен.</span>`;
//                }
//            });
//        }
//
//        if (uploadJSONBtn) {
//            uploadJSONBtn.addEventListener("click", function() {
//                if (!rekordboxJSON.files.length) {
//                    jsonProgressBlock.innerText = "Выберите JSON-файл!";
//                    return;
//                }
//                let formData = new FormData();
//                formData.append("jsonfile", rekordboxJSON.files[0]);
//                jsonProgressBlock.innerText = "Загрузка...";
//                fetch("/librosa-settings/upload-rekordbox-json", {
//                    method: "POST",
//                    body: formData
//                }).then(r => r.json())
//                 .then(res => {
//                    if (res.error) {
//                        jsonProgressBlock.innerText = "Ошибка: " + res.error;
//                    } else {
//                        jsonProgressBlock.innerText = "Файл загружен.";
//                    }
//                    updateRekordboxJsonStatus();
//                 });
//            });
//        }
//
//        if (parseJSONBtn) {
//            parseJSONBtn.addEventListener("click", function() {
//                fetch("/librosa-settings/rekordbox-json-status")
//                    .then(r => r.json())
//                    .then(data => {
//                        if (data.status === "not_ready") {
//                            jsonProgressBlock.innerText = "Сначала загрузите JSON-файл!";
//                            return;
//                        }
//                        if (data.status === "ready") {
//                            jsonProgressBlock.innerText = "Файл уже распарсен.";
//                            return;
//                        }
//                        // data.status === "json_uploaded" — можно парсить!
//                        jsonProgressBlock.innerText = "Парсинг JSON...";
//                        fetch("/librosa-settings/parse-rekordbox-json", {method: "POST"})
//                            .then(r => r.json())
//                            .then(res => {
//                                if (res.error) {
//                                    jsonProgressBlock.innerText = "Ошибка парсинга: " + res.error;
//                                } else {
//                                    jsonProgressBlock.innerText = "JSON успешно распаршен!";
//                                }
//                                updateRekordboxJsonStatus();
//                            });
//                    });
//            });
//        }
//
//    // Показываем статус для JSON при загрузке страницы
//    if (jsonSection) updateRekordboxJsonStatus();
//
//
//function updateRekordboxStatus() {
//    let source = xmlRadio && xmlRadio.checked ? "xml" : "json";
//    fetch(`/librosa-settings/rekordbox-status?source=${source}`)
//    .then(r => r.json())
//    .then(data => {
//        if (data.status === "ready" || data.status === "xml_ready") {
//            statusBlock.innerHTML = `<span class="text-success">XML-файл успешно распаршен и готов к использованию.<br>Треков: ${data.count ?? ""}</span>`;
//        } else if (data.status === "xml_uploaded") {
//            statusBlock.innerHTML = `<span class="text-primary">XML-файл загружен, но ещё не распарсен. Нажмите "Парсить XML".</span>`;
//        } else if (data.status === "json_ready") {
//            statusBlock.innerHTML = `<span class="text-success">JSON-файл успешно загружен и готов к использованию.<br>Треков: ${data.count ?? ""}</span>`;
//        } else if (data.status === "json_uploaded") {
//            statusBlock.innerHTML = `<span class="text-primary">JSON-файл загружен, но ещё не распарсен. Нажмите "Парсить JSON".</span>`;
//        } else {
//            statusBlock.innerHTML = `<span class="text-warning">Файл Reckordbox не загружен.</span>`;
//        }
//    });
//}
//
//    if (useRekordbox) {
//        function syncRekordboxVisibility() {
//            const enabled = useRekordbox.checked;
//            if (rekordboxSection) {
//                rekordboxSection.style.display = enabled ? "block" : "none";
//            }
//            if (rbBalanceSection) {
//                // Пустая строка = наследовать (отображать), 'none' = скрыть
//                rbBalanceSection.style.display = enabled ? "" : "none";
//            }
//            if (enabled) {
//                updateRekordboxStatus();
//            }
//        }
//        useRekordbox.addEventListener("change", syncRekordboxVisibility);
//        // Первичная синхронизация при загрузке
//        syncRekordboxVisibility();
//    }
//    if (rekordboxSection) {
//        // Показываем статус при загрузке страницы
//        updateRekordboxStatus();
//    }
//
//    if (uploadBtn) {
//        uploadBtn.addEventListener("click", function() {
//            if (!rekordboxXML.files.length) {
//                progressBlock.innerText = "Выберите XML-файл!";
//                return;
//            }
//            let formData = new FormData();
//            formData.append("xmlfile", rekordboxXML.files[0]);
//            progressBlock.innerText = "Загрузка...";
//            fetch("/librosa-settings/upload-rekordbox", {
//                method: "POST",
//                body: formData
//            }).then(r => r.json())
//             .then(res => {
//                if (res.error) {
//                    progressBlock.innerText = "Ошибка: " + res.error;
//                } else {
//                    progressBlock.innerText = "Файл загружен.";
//                }
//                updateRekordboxStatus();
//             });
//        });
//    }
//
//   if (parseBtn) {
//    parseBtn.addEventListener("click", function() {
//        fetch("/librosa-settings/rekordbox-status")
//            .then(r => r.json())
//            .then(data => {
//                if (data.status === "not_ready") {
//                    progressBlock.innerText = "Сначала загрузите XML-файл!";
//                    return;
//                }
//                if (data.status === "ready") {
//                    progressBlock.innerText = "Файл уже распарсен.";
//                    return;
//                }
//                // data.status === "xml_uploaded" — можно парсить!
//                progressBlock.innerText = "Парсинг XML...";
//                fetch("/librosa-settings/parse-rekordbox", { method: "POST" })
//                    .then(r => r.json())
//                    .then(res => {
//                        if (res.error) {
//                            progressBlock.innerText = "Ошибка парсинга: " + res.error;
//                        } else {
//                            progressBlock.innerText = "XML успешно распаршен!";
//                        }
//                        updateRekordboxStatus();
//                    });
//            });
//    });
//}
//
//// === ОБРАБОТЧИК СОХРАНЕНИЯ ФОРМЫ ===
//if (form) {
//    form.addEventListener("submit", function(e){
//        let featureCheckboxes = [
//            "features.mfcc",
//            "features.chroma",
//            "features.spectral_contrast",
//            "features.zcr",
//            "features.tonnetz",
//            "features.spectral_centroid",
//            "features.spectral_bandwidth",
//            "features.spectral_rolloff",
//            "features.rms",
//            "features.onset_strength",
//            "features.tempo",
//            "features.tempogram",
//            "features.delta_mfcc",
//            "features.delta2_mfcc",
//            "features.spectral_flatness",
//            "features.pitch",
//            "features.silence_ratio",
//            "features.energy_entropy",
//            "features.spectral_skewness",
//            "features.harmonic_ratio",
//            "features.mfcc_std",
//            "features.energy_ratio",
//            "features.spectral_stats"
//        ];
//        let anyChecked = featureCheckboxes.some(name => form.querySelector(`[name="${name}"]`)?.checked);
//        if (!anyChecked) {
//            status.innerText = "Ошибка: Включите хотя бы один признак для обучения!";
//            e.preventDefault();
//            return;
//        }
//        e.preventDefault();
//        const data = {};
//        Array.from(form.elements).forEach(el => {
//            if (!el.name) return;
//            // Поддержка вложенных полей, например features.chroma
//            if (el.name.includes(".")) {
//                const [section, key] = el.name.split(".");
//                data[section] = data[section] || {};
//                if (el.type === "checkbox") {
//                    data[section][key] = el.checked;
//                } else if (el.type === "number" || el.type === "range") {
//                    data[section][key] = Number(el.value);
//                } else {
//                    data[section][key] = el.value;
//                }
//            } else {
//                if (el.type === "checkbox") {
//                    data[el.name] = el.checked;
//                } else if (el.type === "number" || el.type === "range") {
//                    data[el.name] = Number(el.value);
//                } else {
//                    data[el.name] = el.value;
//                }
//            }
//        });
//
//        // === ДОБАВЛЕНО: обработка чекбокса learning curve ===
//
//
//        fetch("/librosa-settings", {
//            method: "POST",
//            headers: {"Content-Type": "application/json"},
//            body: JSON.stringify(data)
//        })
//        .then(r => r.json())
//        .then(res => {
//            status.innerText = "Сохранено!";
//            setTimeout(() => status.innerText = "", 1500);
//        })
//        .catch(err => {
//            status.innerText = "Ошибка: " + err;
//        });
//    });
//}
//            // --- Инициализация Bootstrap tooltip для элементов с data-bs-toggle="tooltip" ---
//    (function initTooltips(){
//        if (window.bootstrap && typeof bootstrap.Tooltip === "function") {
//            const tts = document.querySelectorAll('[data-bs-toggle="tooltip"]');
//            tts.forEach(el => {
//                try { new bootstrap.Tooltip(el); } catch(e) { /* игнорируем ошибки */ }
//            });
//        } else {
//            // Если bootstrap не подключён, остаётся стандартный title
//            if (typeof console !== "undefined") {
//                // Не засоряем логи если включён model/status – этого достаточно
//                console.debug && console.debug("[librosa_settings] bootstrap.Tooltip недоступен – используем native title");
//            }
//
//    })();
//});

// static/js/librosa_settings.js
// Версия: 2025-08-09 (добавлена поддержка скрытия блока балансировки Rekordbox и tooltip)
// Логика сохранена. Исправлены незакрытые скобки / IIFE.
// Если потребуется логирование через backend – можно добавить fetch к пользовательскому endpoint.
// Файл не зависит от jQuery, только vanilla JS + (опционально) bootstrap.Tooltip.

document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    // --- Основные элементы формы ---
    const form = document.getElementById("librosa-form");
    const status = document.getElementById("save-status");

    // --- Rekordbox основной чекбокс и секции ---
    const useRekordbox = document.getElementById("use_rekordbox");
    const rekordboxSection = document.getElementById("rekordbox-section");              // секция загрузки/парсинга
    const rbBalanceSection = document.getElementById("rekordbox-balance-section");      // секция "Балансировка и лимиты (Rekordbox)"

    // --- XML / JSON элементы ---
    const rekordboxXML = document.getElementById("rekordbox-xml");
    const uploadBtn = document.getElementById("upload-rekordbox-btn");
    const parseBtn = document.getElementById("parse-rekordbox-btn");
    const statusBlock = document.getElementById("rekordbox-status-block");
    const progressBlock = document.getElementById("rekordbox-progress");

    const xmlRadio = document.getElementById("rekordbox-xml-radio");
    const jsonRadio = document.getElementById("rekordbox-json-radio");
    const xmlSection = document.getElementById("rekordbox-xml-section");
    const jsonSection = document.getElementById("rekordbox-json-section");

    // --- JSON загрузка / парсинг ---
    const rekordboxJSON = document.getElementById("rekordbox-json");
    const uploadJSONBtn = document.getElementById("upload-rekordbox-json-btn");
    const parseJSONBtn = document.getElementById("parse-rekordbox-json-btn");
    const jsonStatusBlock = document.getElementById("rekordbox-json-status-block");
    const jsonProgressBlock = document.getElementById("rekordbox-json-progress");

    // ---------------------------------------------------------------------
    // Переключение между XML и JSON секциями
    // ---------------------------------------------------------------------
    if (xmlRadio && jsonRadio && xmlSection && jsonSection) {
        xmlRadio.addEventListener("change", function () {
            if (xmlRadio.checked) {
                xmlSection.style.display = "block";
                jsonSection.style.display = "none";
            }
        });
        jsonRadio.addEventListener("change", function () {
            if (jsonRadio.checked) {
                xmlSection.style.display = "none";
                jsonSection.style.display = "block";
            }
        });
    }

    // ---------------------------------------------------------------------
    // Обновление статуса JSON (загружен / распарсен)
    // ---------------------------------------------------------------------
    function updateRekordboxJsonStatus() {
        if (!jsonStatusBlock) return;
        fetch("/librosa-settings/rekordbox-json-status")
            .then(r => r.json())
            .then(data => {
                if (data.status === "ready") {
                    jsonStatusBlock.innerHTML = `<span class="text-success">JSON-файл успешно загружен и готов к использованию.<br>Треков: ${data.count}</span>`;
                } else if (data.status === "json_uploaded") {
                    jsonStatusBlock.innerHTML = `<span class="text-primary">JSON-файл загружен, но ещё не распарсен. Нажмите &laquo;Парсить JSON&raquo;.</span>`;
                } else {
                    jsonStatusBlock.innerHTML = `<span class="text-warning">JSON-файл не загружен.</span>`;
                }
            })
            .catch(() => {
                jsonStatusBlock.innerHTML = `<span class="text-danger">Ошибка запроса статуса JSON.</span>`;
            });
    }

    if (jsonSection) {
        // Показ статуса при загрузке страницы если секция JSON доступна
        updateRekordboxJsonStatus();
    }

    if (uploadJSONBtn) {
        uploadJSONBtn.addEventListener("click", function () {
            if (!rekordboxJSON || !rekordboxJSON.files.length) {
                if (jsonProgressBlock) jsonProgressBlock.innerText = "Выберите JSON-файл!";
                return;
            }
            const formData = new FormData();
            formData.append("jsonfile", rekordboxJSON.files[0]);
            if (jsonProgressBlock) jsonProgressBlock.innerText = "Загрузка...";
            fetch("/librosa-settings/upload-rekordbox-json", {
                method: "POST",
                body: formData
            })
                .then(r => r.json())
                .then(res => {
                    if (jsonProgressBlock) {
                        jsonProgressBlock.innerText = res.error ? ("Ошибка: " + res.error) : "Файл загружен.";
                    }
                    updateRekordboxJsonStatus();
                })
                .catch(err => {
                    if (jsonProgressBlock) jsonProgressBlock.innerText = "Ошибка: " + err;
                });
        });
    }

    if (parseJSONBtn) {
        parseJSONBtn.addEventListener("click", function () {
            fetch("/librosa-settings/rekordbox-json-status")
                .then(r => r.json())
                .then(data => {
                    if (data.status === "not_ready") {
                        if (jsonProgressBlock) jsonProgressBlock.innerText = "Сначала загрузите JSON-файл!";
                        return;
                    }
                    if (data.status === "ready") {
                        if (jsonProgressBlock) jsonProgressBlock.innerText = "Файл уже распарсен.";
                        return;
                    }
                    // status === "json_uploaded"
                    if (jsonProgressBlock) jsonProgressBlock.innerText = "Парсинг JSON...";
                    fetch("/librosa-settings/parse-rekordbox-json", { method: "POST" })
                        .then(r => r.json())
                        .then(res => {
                            if (jsonProgressBlock) {
                                jsonProgressBlock.innerText = res.error ? ("Ошибка парсинга: " + res.error) : "JSON успешно распаршен!";
                            }
                            updateRekordboxJsonStatus();
                        })
                        .catch(err => {
                            if (jsonProgressBlock) jsonProgressBlock.innerText = "Ошибка: " + err;
                        });
                });
        });
    }

    // ---------------------------------------------------------------------
    // Обновление статуса Rekordbox (XML или JSON в зависимости от выбора радио)
    // ---------------------------------------------------------------------
    function updateRekordboxStatus() {
        if (!statusBlock) return;
        const source = (xmlRadio && xmlRadio.checked) ? "xml" : "json";
        fetch(`/librosa-settings/rekordbox-status?source=${source}`)
            .then(r => r.json())
            .then(data => {
                if (data.status === "ready" || data.status === "xml_ready") {
                    statusBlock.innerHTML = `<span class="text-success">XML-файл успешно распаршен и готов к использованию.<br>Треков: ${data.count ?? ""}</span>`;
                } else if (data.status === "xml_uploaded") {
                    statusBlock.innerHTML = `<span class="text-primary">XML-файл загружен, но ещё не распарсен. Нажмите &laquo;Парсить XML&raquo;.</span>`;
                } else if (data.status === "json_ready") {
                    statusBlock.innerHTML = `<span class="text-success">JSON-файл успешно загружен и готов к использованию.<br>Треков: ${data.count ?? ""}</span>`;
                } else if (data.status === "json_uploaded") {
                    statusBlock.innerHTML = `<span class="text-primary">JSON-файл загружен, но ещё не распарсен. Нажмите &laquo;Парсить JSON&raquo;.</span>`;
                } else {
                    statusBlock.innerHTML = `<span class="text-warning">Файл Rekordbox не загружен.</span>`;
                }
            })
            .catch(() => {
                statusBlock.innerHTML = `<span class="text-danger">Ошибка запроса статуса.</span>`;
            });
    }

    // ---------------------------------------------------------------------
    // Синхронизация видимости секций при переключении use_rekordbox
    // ---------------------------------------------------------------------
    if (useRekordbox) {
        function syncRekordboxVisibility() {
            const enabled = useRekordbox.checked;
            if (rekordboxSection) {
                rekordboxSection.style.display = enabled ? "block" : "none";
            }
            if (rbBalanceSection) {
                rbBalanceSection.style.display = enabled ? "" : "none";
            }
            if (enabled) {
                updateRekordboxStatus();
                updateRekordboxJsonStatus();
            }
        }
        useRekordbox.addEventListener("change", syncRekordboxVisibility);
        // Первичная синхронизация
        syncRekordboxVisibility();
    }

    // Если секция уже на странице – обновим статус при загрузке
    if (rekordboxSection && useRekordbox && useRekordbox.checked) {
        updateRekordboxStatus();
    }

    // ---------------------------------------------------------------------
    // Загрузка XML
    // ---------------------------------------------------------------------
    if (uploadBtn) {
        uploadBtn.addEventListener("click", function () {
            if (!rekordboxXML || !rekordboxXML.files.length) {
                if (progressBlock) progressBlock.innerText = "Выберите XML-файл!";
                return;
            }
            const formData = new FormData();
            formData.append("xmlfile", rekordboxXML.files[0]);
            if (progressBlock) progressBlock.innerText = "Загрузка...";
            fetch("/librosa-settings/upload-rekordbox", {
                method: "POST",
                body: formData
            })
                .then(r => r.json())
                .then(res => {
                    if (progressBlock) {
                        progressBlock.innerText = res.error ? ("Ошибка: " + res.error) : "Файл загружен.";
                    }
                    updateRekordboxStatus();
                })
                .catch(err => {
                    if (progressBlock) progressBlock.innerText = "Ошибка: " + err;
                });
        });
    }

    // ---------------------------------------------------------------------
    // Парсинг XML
    // ---------------------------------------------------------------------
    if (parseBtn) {
        parseBtn.addEventListener("click", function () {
            fetch("/librosa-settings/rekordbox-status")
                .then(r => r.json())
                .then(data => {
                    if (data.status === "not_ready") {
                        if (progressBlock) progressBlock.innerText = "Сначала загрузите XML-файл!";
                        return;
                    }
                    if (data.status === "ready") {
                        if (progressBlock) progressBlock.innerText = "Файл уже распарсен.";
                        return;
                    }
                    if (progressBlock) progressBlock.innerText = "Парсинг XML...";
                    fetch("/librosa-settings/parse-rekordbox", { method: "POST" })
                        .then(r => r.json())
                        .then(res => {
                            if (progressBlock) {
                                progressBlock.innerText = res.error ? ("Ошибка парсинга: " + res.error) : "XML успешно распаршен!";
                            }
                            updateRekordboxStatus();
                        })
                        .catch(err => {
                            if (progressBlock) progressBlock.innerText = "Ошибка: " + err;
                        });
                });
        });
    }

    // ---------------------------------------------------------------------
    // Сохранение формы настроек
    // ---------------------------------------------------------------------
    if (form) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();

            const featureCheckboxes = [
                "features.mfcc",
                "features.chroma",
                "features.spectral_contrast",
                "features.zcr",
                "features.tonnetz",
                "features.spectral_centroid",
                "features.spectral_bandwidth",
                "features.spectral_rolloff",
                "features.rms",
                "features.onset_strength",
                "features.tempo",
                "features.tempogram",
                "features.delta_mfcc",
                "features.delta2_mfcc",
                "features.spectral_flatness",
                "features.pitch",
                "features.silence_ratio",
                "features.energy_entropy",
                "features.spectral_skewness",
                "features.harmonic_ratio",
                "features.mfcc_std",
                "features.energy_ratio",
                "features.spectral_stats"
            ];

            const anyChecked = featureCheckboxes.some(name => form.querySelector(`[name="${name}"]`)?.checked);
            if (!anyChecked) {
                if (status) status.innerText = "Ошибка: Включите хотя бы один признак для обучения!";
                return;
            }

            const data = {};
            Array.from(form.elements).forEach(el => {
                if (!el.name) return;
                if (el.name.includes(".")) {
                    const [section, key] = el.name.split(".");
                    data[section] = data[section] || {};
                    if (el.type === "checkbox") {
                        data[section][key] = el.checked;
                    } else if (el.type === "radio") {
                        if (el.checked) data[section][key] = el.value;
                    } else if (el.type === "number" || el.type === "range") {
                        data[section][key] = Number(el.value);
                    } else {
                        data[section][key] = el.value;
                    }
                } else {
                    if (el.type === "checkbox") {
                        data[el.name] = el.checked;
                    } else if (el.type === "radio") {
                        if (el.checked) data[el.name] = el.value;
                    } else if (el.type === "number" || el.type === "range") {
                        data[el.name] = Number(el.value);
                    } else {
                        data[el.name] = el.value;
                    }
                }
            });

            fetch("/librosa-settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            })
                .then(r => r.json())
                .then(res => {
                    if (res.error) {
                        if (status) status.innerText = "Ошибка: " + res.error;
                    } else {
                        if (status) {
                            status.innerText = "Сохранено!";
                            setTimeout(() => { if (status) status.innerText = ""; }, 1500);
                        }
                    }
                })
                .catch(err => {
                    if (status) status.innerText = "Ошибка: " + err;
                });
        });
    }

    // ---------------------------------------------------------------------
    // Инициализация tooltip (bootstrap)
    // ---------------------------------------------------------------------
    (function initTooltips() {
        const tooltipNodes = document.querySelectorAll('[data-bs-toggle="tooltip"]');
        if (window.bootstrap && typeof bootstrap.Tooltip === "function") {
            tooltipNodes.forEach(el => {
                try {
                    new bootstrap.Tooltip(el);
                } catch (e) {
                    // noop
                }
            });
        } else {
            // fallback: нативный title уже работает
            if (typeof console !== "undefined" && console.debug) {
                console.debug("[librosa_settings] Bootstrap Tooltips не активны (отсутствует bootstrap JS) – использую native title");
            }
        }
    })();
});
