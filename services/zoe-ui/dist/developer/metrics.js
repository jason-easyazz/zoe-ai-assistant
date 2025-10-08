// Dashboard Metrics Updater
// Fetches real metrics from backend and updates display

async function updateSystemMetrics() {
    try {
        // Fetch metrics from the backend
        const response = await fetch('/api/developer/metrics');
        const data = await response.json();
        
        // Update CPU Usage
        const cpuElement = document.querySelector('.metric-card:nth-child(1) .metric-value');
        if (cpuElement && data.cpu_percent !== undefined) {
            cpuElement.textContent = `${data.cpu_percent.toFixed(1)}%`;
            const cpuDetail = document.querySelector('.metric-card:nth-child(1) .metric-detail');
            if (cpuDetail) {
                cpuDetail.textContent = '4 cores available';
            }
        }
        
        // Update Memory Usage
        const memElement = document.querySelector('.metric-card:nth-child(2) .metric-value');
        if (memElement && data.memory_percent !== undefined) {
            memElement.textContent = `${data.memory_percent.toFixed(1)}%`;
            const memDetail = document.querySelector('.metric-card:nth-child(2) .metric-detail');
            if (memDetail && data.memory_used_gb !== undefined && data.memory_total_gb !== undefined) {
                memDetail.textContent = `${data.memory_used_gb}GB / ${data.memory_total_gb}GB`;
            }
        }
        
        // Update Disk Usage
        const diskElement = document.querySelector('.metric-card:nth-child(3) .metric-value');
        if (diskElement && data.disk_percent !== undefined) {
            diskElement.textContent = `${data.disk_percent.toFixed(1)}%`;
            const diskDetail = document.querySelector('.metric-card:nth-child(3) .metric-detail');
            if (diskDetail && data.disk_used_gb !== undefined && data.disk_total_gb !== undefined) {
                diskDetail.textContent = `${data.disk_used_gb}GB / ${data.disk_total_gb}GB`;
            }
        }
        
        // Update container count if displayed
        const containerElement = document.querySelector('.containers-count');
        if (containerElement && data.containers_running !== undefined) {
            containerElement.textContent = `${data.containers_running} containers running`;
        }
        
        // Add color coding based on usage levels
        [cpuElement, memElement, diskElement].forEach(elem => {
            if (elem && elem.textContent !== '--') {
                const value = parseFloat(elem.textContent);
                elem.style.color = value > 80 ? '#ef4444' : value > 60 ? '#f59e0b' : '#10b981';
            }
        });
        
    } catch (error) {
        console.error('Failed to fetch metrics:', error);
        // If fetch fails, try alternative method
        tryAlternativeMetricsFetch();
    }
}

// Alternative method if direct fetch fails
async function tryAlternativeMetricsFetch() {
    try {
        // Try fetching through chat endpoint
        const response = await fetch('/api/developer/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: 'show system health'})
        });
        const data = await response.json();
        
        // Parse the response to extract metrics
        if (data.response) {
            const text = data.response;
            
            // Extract memory percentage
            const memMatch = text.match(/(\d+\.?\d*)%\s*used/);
            if (memMatch) {
                const memElement = document.querySelector('.metric-card:nth-child(2) .metric-value');
                if (memElement) memElement.textContent = `${memMatch[1]}%`;
            }
            
            // Extract disk percentage
            const diskMatch = text.match(/(\d+)%.*\/$/m);
            if (diskMatch) {
                const diskElement = document.querySelector('.metric-card:nth-child(3) .metric-value');
                if (diskElement) diskElement.textContent = `${diskMatch[1]}%`;
            }
        }
    } catch (error) {
        console.error('Alternative fetch also failed:', error);
    }
}

// Update metrics every 5 seconds
setInterval(updateSystemMetrics, 5000);

// Initial update on page load
document.addEventListener('DOMContentLoaded', () => {
    updateSystemMetrics();
    
    // Also ensure the metrics are visible
    const metricValues = document.querySelectorAll('.metric-value');
    metricValues.forEach(elem => {
        if (elem.textContent === '--' || !elem.textContent) {
            elem.textContent = 'Loading...';
            elem.style.fontSize = '24px';
            elem.style.fontWeight = 'bold';
        }
    });
});

// Export for use in other scripts
window.updateSystemMetrics = updateSystemMetrics;
