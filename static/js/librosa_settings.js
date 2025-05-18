// librosa_settings.js
document.addEventListener("DOMContentLoaded", function() {
    const form = document.getElementById("librosa-form");
    const status = document.getElementById("save-status");
    const useRekordbox = document.getElementById("use_rekordbox");
    const rekordboxSection = document.getElementById("rekordbox-section");
    const rekordboxXML = document.getElementById("rekordbox-xml");
    const uploadBtn = document.getElementById("upload-rekordbox-btn");
    const parseBtn = document.getElementById("parse-rekordbox-btn");
    const statusBlock = document.getElementById("rekordbox-status-block");
    const progressBlock = document.getElementById("rekordbox-progress");

   function updateRekordboxStatus() {
    fetch("/librosa-settings/rekordbox-status")
    .then(r => r.json())
    .then(data => {
        if (data.status === "ready") {
            statusBlock.innerHTML = `<span class="text-success">Файл Reckordbox успешно распаршен и готов к использованию.<br>Треков: ${data.count}</span>`;
        } else if (data.status === "xml_uploaded") {
            statusBlock.innerHTML = `<span class="text-primary">XML-файл загружен, но ещё не распарсен. Нажмите "Парсить XML".</span>`;
        } else {
            statusBlock.innerHTML = `<span class="text-warning">Файл Reckordbox не загружен.</span>`;
        }
    });
}

    if (useRekordbox) {
        useRekordbox.addEventListener("change", function() {
            rekordboxSection.style.display = useRekordbox.checked ? "block" : "none";
            updateRekordboxStatus();
        });
    }

    if (rekordboxSection) {
        // Показываем статус при загрузке страницы
        updateRekordboxStatus();
    }

    if (uploadBtn) {
        uploadBtn.addEventListener("click", function() {
            if (!rekordboxXML.files.length) {
                progressBlock.innerText = "Выберите XML-файл!";
                return;
            }
            let formData = new FormData();
            formData.append("xmlfile", rekordboxXML.files[0]);
            progressBlock.innerText = "Загрузка...";
            fetch("/librosa-settings/upload-rekordbox", {
                method: "POST",
                body: formData
            }).then(r => r.json())
             .then(res => {
                if (res.error) {
                    progressBlock.innerText = "Ошибка: " + res.error;
                } else {
                    progressBlock.innerText = "Файл загружен.";
                }
                updateRekordboxStatus();
             });
        });
    }

   if (parseBtn) {
    parseBtn.addEventListener("click", function() {
        fetch("/librosa-settings/rekordbox-status")
            .then(r => r.json())
            .then(data => {
                if (data.status === "not_ready") {
                    progressBlock.innerText = "Сначала загрузите XML-файл!";
                    return;
                }
                if (data.status === "ready") {
                    progressBlock.innerText = "Файл уже распарсен.";
                    return;
                }
                // data.status === "xml_uploaded" — можно парсить!
                progressBlock.innerText = "Парсинг XML...";
                fetch("/librosa-settings/parse-rekordbox", { method: "POST" })
                    .then(r => r.json())
                    .then(res => {
                        if (res.error) {
                            progressBlock.innerText = "Ошибка парсинга: " + res.error;
                        } else {
                            progressBlock.innerText = "XML успешно распаршен!";
                        }
                        updateRekordboxStatus();
                    });
            });
    });
}

    // === ОБРАБОТЧИК СОХРАНЕНИЯ ФОРМЫ ===
    if (form) {
        form.addEventListener("submit", function(e){
            let featureCheckboxes = [
                "features.mfcc",
                "features.chroma",
                "features.spectral_contrast",
                "features.zcr",
                "features.tonnetz"
            ];
            let anyChecked = featureCheckboxes.some(name => form.querySelector(`[name="${name}"]`)?.checked);
            if (!anyChecked) {
                status.innerText = "Ошибка: Включите хотя бы один признак для обучения!";
                e.preventDefault();
                return;
            }
            e.preventDefault();
            const data = {};
            Array.from(form.elements).forEach(el => {
                if (!el.name) return;
                // Поддержка вложенных полей, например features.chroma
                if (el.name.includes(".")) {
                    const [section, key] = el.name.split(".");
                    data[section] = data[section] || {};
                    if (el.type === "checkbox") {
                        data[section][key] = el.checked;
                    } else if (el.type === "number" || el.type === "range") {
                        data[section][key] = Number(el.value);
                    } else {
                        data[section][key] = el.value;
                    }
                } else {
                    if (el.type === "checkbox") {
                        data[el.name] = el.checked;
                    } else if (el.type === "number" || el.type === "range") {
                        data[el.name] = Number(el.value);
                    } else {
                        data[el.name] = el.value;
                    }
                }
            });
            fetch("/librosa-settings", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(data)
            })
            .then(r => r.json())
            .then(res => {
                status.innerText = "Сохранено!";
                setTimeout(() => status.innerText = "", 1500);
            })
            .catch(err => {
                status.innerText = "Ошибка: " + err;
            });
        });
    }
});