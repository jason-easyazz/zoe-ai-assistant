// Settings page functionality
const API_BASE = 'http://localhost:8000';

// Load current settings
async function loadSettings() {
    try {
        // Load API keys
        const response = await fetch(`${API_BASE}/api/settings/apikeys`);
        const data = await response.json();
        
        // Update UI to show which keys are configured
        for (const [service, key] of Object.entries(data.keys)) {
            const input = document.getElementById(`${service}-key`);
            if (input) {
                input.placeholder = key ? `Configured (****${key.slice(-4)})` : 'Not configured';
            }
        }
    } catch (error) {
        console.error('Error loading settings:', error);
    }
}

// Save API key
async function saveAPIKey(service) {
    const input = document.getElementById(`${service}-key`);
    if (!input || !input.value) return;
    
    try {
        const response = await fetch(`${API_BASE}/api/settings/apikeys`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                service: service,
                key: input.value
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            alert(`${service} API key saved successfully!`);
            input.value = '';  // Clear input
            loadSettings();    // Reload to show updated status
        } else {
            alert(`Error saving key: ${result.message}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// Test API key
async function testAPIKey(service) {
    try {
        const response = await fetch(`${API_BASE}/api/settings/apikeys/test/${service}`);
        const result = await response.json();
        
        if (result.working) {
            alert(`✅ ${service} API key is working!`);
        } else {
            alert(`⚠️ ${service} API key is configured but not tested`);
        }
    } catch (error) {
        alert(`Error testing key: ${error.message}`);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', loadSettings);
