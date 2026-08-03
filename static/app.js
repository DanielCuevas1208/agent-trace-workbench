(() => {
    const form = document.querySelector("#ingest-form");
    if (form) {
        const status = document.querySelector("#ingest-status");
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            status.className = "form-status";
            status.textContent = "Storing...";
            try {
                const payload = JSON.parse(document.querySelector("#trace-json").value);
                const response = await fetch("/api/traces", {
                    method: "POST",
                    headers: { "content-type": "application/json", "x-trace-source": "dashboard.json" },
                    body: JSON.stringify(payload),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || "Trace could not be stored.");
                status.textContent = "Stored. Opening run...";
                window.location.href = `/runs/${encodeURIComponent(result.run_id)}`;
            } catch (error) {
                status.className = "form-status error";
                status.textContent = error.message;
            }
        });
    }

    const saveForm = document.querySelector("#save-comparison");
    if (saveForm) {
        const status = document.querySelector("#comparison-status");
        saveForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            status.className = "form-status";
            status.textContent = "Saving...";
            try {
                const data = new FormData(saveForm);
                const response = await fetch("/api/comparisons", {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({
                        run_a: data.get("run_a"),
                        run_b: data.get("run_b"),
                        label: data.get("label"),
                    }),
                });
                if (!response.ok) throw new Error("Comparison could not be saved.");
                status.textContent = "Saved.";
                window.location.reload();
            } catch (error) {
                status.className = "form-status error";
                status.textContent = error.message;
            }
        });
    }

    document.querySelectorAll("[data-delete]").forEach((link) => {
        link.addEventListener("click", async (event) => {
            event.preventDefault();
            const response = await fetch(`/api/comparisons/${encodeURIComponent(link.dataset.delete)}`, {
                method: "DELETE",
            });
            if (response.ok) window.location.reload();
        });
    });

    const stateFilter = document.querySelector(".state-filter");
    if (stateFilter) {
        const chips = stateFilter.querySelectorAll(".chip");
        const applyFilter = (state) => {
            chips.forEach((chip) => {
                const active = chip.dataset.state === state;
                chip.classList.toggle("chip-active", active);
                chip.setAttribute("aria-pressed", active ? "true" : "false");
            });
            document.querySelectorAll("[data-state]").forEach((row) => {
                row.hidden = state !== "all" && row.dataset.state !== state;
            });
        };
        chips.forEach((chip) => {
            chip.addEventListener("click", () => applyFilter(chip.dataset.state));
        });
    }

    const publishButton = document.querySelector("[data-collector-publish]");
    if (publishButton) {
        const status = document.querySelector("#publish-status");
        publishButton.addEventListener("click", async () => {
            status.className = "form-status";
            status.textContent = "Sending to collector...";
            try {
                const response = await fetch(
                    `/api/runs/${encodeURIComponent(publishButton.dataset.collectorPublish)}/export/collector`,
                    { method: "POST", headers: { "content-type": "application/json" } },
                );
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || "Run could not be sent.");
                if (result.status !== "accepted") throw new Error(result.detail || "Collector rejected the run.");
                status.textContent = `Sent ${result.span_count} spans.`;
            } catch (error) {
                status.className = "form-status error";
                status.textContent = error.message;
            }
        });
    }

    const annotationsForm = document.querySelector("#annotations-form");
    if (annotationsForm) {
        const status = document.querySelector("#annotation-status");
        annotationsForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            status.className = "form-status";
            status.textContent = "Saving...";
            try {
                const data = new FormData(annotationsForm);
                const response = await fetch(
                    `/api/runs/${encodeURIComponent(annotationsForm.dataset.run)}/annotations`,
                    {
                        method: "PATCH",
                        headers: { "content-type": "application/json" },
                        body: JSON.stringify({
                            label: String(data.get("label") || ""),
                            note: String(data.get("note") || ""),
                        }),
                    },
                );
                if (!response.ok) throw new Error("Annotations could not be saved.");
                status.textContent = "Saved.";
                window.location.reload();
            } catch (error) {
                status.className = "form-status error";
                status.textContent = error.message;
            }
        });
    }
})();
