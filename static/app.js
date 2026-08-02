(() => {
    const form = document.querySelector("#ingest-form");
    if (!form) return;

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
})();
