document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".review-correction-form").forEach(function (form) {
        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            const status = form.querySelector(".correction-status");
            const analysisNode = form.querySelector(".correction-analysis");
            let analysis = {};
            try {
                analysis = analysisNode ? JSON.parse(analysisNode.textContent) : {};
            } catch (_error) {
                analysis = {};
            }
            const payload = {
                path: form.dataset.path,
                base_genre: form.querySelector(".correction-base-genre")?.value || "",
                language: form.querySelector(".correction-language")?.value || "Auto",
                version_type: form.querySelector(".correction-version")?.value || "Auto",
                note: form.querySelector(".correction-note")?.value || "",
                analysis: analysis
            };
            if (!payload.base_genre) {
                if (status) status.innerHTML = '<span class="text-danger">Выберите базовый стиль.</span>';
                return;
            }
            if (status) status.innerHTML = '<span class="text-muted">Сохраняем…</span>';
            try {
                const response = await fetch("/librosa-review/corrections", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (!response.ok || data.error) throw new Error(data.error || "Ошибка сохранения");
                if (status) {
                    status.innerHTML = '<span class="text-success">Сохранено. Повторная проверка применит ручную метку.</span>';
                }
                window.setTimeout(function () { window.location.reload(); }, 900);
            } catch (error) {
                if (status) status.innerHTML = `<span class="text-danger">${String(error.message || error)}</span>`;
            }
        });
    });

    document.querySelectorAll(".review-delete").forEach(function (button) {
        button.addEventListener("click", async function () {
            if (!window.confirm("Удалить ручное исправление? Исходный MP3 затронут не будет.")) return;
            const entryId = button.dataset.id;
            try {
                const response = await fetch(`/librosa-review/${encodeURIComponent(entryId)}`, {
                    method: "DELETE"
                });
                const data = await response.json();
                if (!response.ok || data.error) throw new Error(data.error || "Ошибка удаления");
                document.getElementById(`review-row-${entryId}`)?.remove();
            } catch (error) {
                window.alert(String(error.message || error));
            }
        });
    });
});
