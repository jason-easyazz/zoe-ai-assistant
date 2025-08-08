// Settings page logic for model selection

async function loadModelSettings() {
    const radios = document.querySelectorAll('input[name="model"]');
    const statusElems = {
        'llama3.2:3b': document.getElementById('llama-status'),
        'mistral:7b': document.getElementById('mistral-status')
    };

    try {
        const resp = await fetch('/api/models/status');
        const data = await resp.json();
        data.forEach(m => {
            const el = statusElems[m.name];
            if (el) {
                el.textContent = m.available ? '✅' : '⚠️';
            }
        });
    } catch (e) {
        console.error('Status load failed', e);
    }

    try {
        const resp = await fetch('/api/settings/model');
        const data = await resp.json();
        radios.forEach(r => {
            if (r.value === data.active_model) {
                r.checked = true;
            }
        });
    } catch (e) {
        console.error('Active model load failed', e);
    }

    radios.forEach(r => {
        r.addEventListener('change', async () => {
            const selected = document.querySelector('input[name="model"]:checked').value;
            if (statusElems[selected]) {
                statusElems[selected].textContent = '⏳';
            }
            try {
                await fetch('/api/settings/model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model_name: selected })
                });
                if (statusElems[selected]) {
                    statusElems[selected].textContent = '✅';
                }
            } catch (e) {
                console.error('Model switch failed', e);
                if (statusElems[selected]) {
                    statusElems[selected].textContent = '⚠️';
                }
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', loadModelSettings);
