document.addEventListener("DOMContentLoaded", function(){
    const form = document.getElementById("librosa-form");
    const status = document.getElementById("save-status");
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
});