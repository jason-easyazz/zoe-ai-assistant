window.updateCheck = {
    async check() {
        const status = document.getElementById('updateStatus');
        const updateBtn = document.getElementById('runUpdateBtn');
        status.textContent = 'Checking...';
        try {
            const res = await fetch('/api/update/check');
            const data = await res.json();
            if (data.update_available) {
                status.textContent = 'Update available';
                updateBtn.style.display = 'inline-block';
            } else {
                status.textContent = 'Up to date';
                updateBtn.style.display = 'none';
            }
        } catch (e) {
            status.textContent = 'Failed to check';
            updateBtn.style.display = 'none';
        }
    },
    async run() {
        const status = document.getElementById('updateStatus');
        const progressWrap = document.getElementById('updateProgress');
        const bar = document.getElementById('updateProgressBar');
        const text = document.getElementById('updateProgressText');
        status.textContent = 'Updating...';
        progressWrap.style.display = 'block';
        let elapsed = 0;
        const estimate = 30; // seconds
        const interval = 500;
        const timer = setInterval(() => {
            elapsed += interval / 1000;
            const remaining = Math.max(estimate - elapsed, 0);
            const percent = Math.min((elapsed / estimate) * 100, 99);
            bar.style.width = percent + '%';
            text.textContent = `Updating... ${Math.round(percent)}% (${Math.ceil(remaining)}s remaining)`;
        }, interval);
        try {
            const res = await fetch('/api/update/run', { method: 'POST' });
            const data = await res.json();
            clearInterval(timer);
            bar.style.width = '100%';
            text.textContent = 'Update complete';
            status.textContent = data.message || 'Update complete';
            setTimeout(() => {
                progressWrap.style.display = 'none';
                bar.style.width = '0%';
                text.textContent = '';
            }, 2000);
        } catch (e) {
            clearInterval(timer);
            status.textContent = 'Update failed';
            text.textContent = 'Update failed';
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    const checkBtn = document.getElementById('checkUpdatesBtn');
    const runBtn = document.getElementById('runUpdateBtn');
    if (checkBtn) checkBtn.addEventListener('click', () => window.updateCheck.check());
    if (runBtn) runBtn.addEventListener('click', () => window.updateCheck.run());
});
