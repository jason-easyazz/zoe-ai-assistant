/**
 * Memory Timeline View
 * Chronological display of memories and events
 */

class MemoryTimeline {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.entries = [];
        this.groupBy = 'date'; // or 'type', 'person'
    }

    async loadData() {
        try {
            // Fetch all memories
            const [peopleResp, projectsResp, notesResp] = await Promise.all([
                apiRequest('/memories/?type=people'),
                apiRequest('/memories/?type=projects'),
                apiRequest('/memories/?type=notes')
            ]);

            const people = peopleResp.memories || [];
            const projects = projectsResp.memories || [];
            const notes = notesResp.memories || [];

            // Convert to timeline entries
            this.entries = [];
            
            people.forEach(p => this.entries.push({
                type: 'people',
                title: `Met ${p.name}`,
                subtitle: p.relationship || 'Person',
                date: new Date(p.created_at),
                icon: '👤',
                data: p,
                content: p.notes
            }));

            projects.forEach(p => this.entries.push({
                type: 'projects',
                title: p.name,
                subtitle: `Project ${p.status || ''}`,
                date: p.start_date ? new Date(p.start_date) : new Date(p.created_at),
                icon: '📁',
                data: p,
                content: p.description
            }));

            notes.forEach(n => this.entries.push({
                type: 'notes',
                title: n.title || 'Note',
                subtitle: n.category || 'General',
                date: new Date(n.created_at),
                icon: '📝',
                data: n,
                content: n.content
            }));

            // Sort by date (newest first)
            this.entries.sort((a, b) => b.date - a.date);

            this.render();
        } catch (error) {
            console.error('Failed to load timeline data:', error);
        }
    }

    render() {
        if (!this.container) return;

        let html = '';

        if (this.entries.length === 0) {
            html = '<div class="timeline-empty">No memories yet. Start creating some!</div>';
        } else {
            // Group by date
            const grouped = this.groupByDate(this.entries);
            
            Object.keys(grouped).forEach(dateKey => {
                const entries = grouped[dateKey];
                
                html += `<div class="timeline-group">`;
                html += `<div class="timeline-date-header">${this.formatDateHeader(dateKey)}</div>`;
                
                entries.forEach(entry => {
                    html += this.renderEntry(entry);
                });
                
                html += `</div>`;
            });
        }

        this.container.innerHTML = html;
    }

    groupByDate(entries) {
        const grouped = {};
        
        entries.forEach(entry => {
            const dateKey = this.getDateKey(entry.date);
            if (!grouped[dateKey]) {
                grouped[dateKey] = [];
            }
            grouped[dateKey].push(entry);
        });

        return grouped;
    }

    getDateKey(date) {
        const today = new Date();
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);

        // Reset time for comparison
        const compareDate = new Date(date);
        compareDate.setHours(0, 0, 0, 0);
        const compareToday = new Date(today);
        compareToday.setHours(0, 0, 0, 0);
        const compareYesterday = new Date(yesterday);
        compareYesterday.setHours(0, 0, 0, 0);

        if (compareDate.getTime() === compareToday.getTime()) {
            return 'today';
        } else if (compareDate.getTime() === compareYesterday.getTime()) {
            return 'yesterday';
        } else {
            return date.toISOString().split('T')[0]; // YYYY-MM-DD
        }
    }

    formatDateHeader(dateKey) {
        if (dateKey === 'today') return 'Today';
        if (dateKey === 'yesterday') return 'Yesterday';

        const date = new Date(dateKey);
        const now = new Date();
        const diffDays = Math.floor((now - date) / (1000 * 60 * 60 * 24));

        if (diffDays < 7) {
            return `${diffDays} days ago`;
        } else if (diffDays < 30) {
            const weeks = Math.floor(diffDays / 7);
            return `${weeks} week${weeks > 1 ? 's' : ''} ago`;
        } else if (diffDays < 365) {
            const months = Math.floor(diffDays / 30);
            return `${months} month${months > 1 ? 's' : ''} ago`;
        } else {
            return date.toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric' 
            });
        }
    }

    renderEntry(entry) {
        const time = entry.date.toLocaleTimeString('en-US', { 
            hour: '2-digit', 
            minute: '2-digit' 
        });

        const contentPreview = entry.content ? 
            entry.content.substring(0, 150) + (entry.content.length > 150 ? '...' : '') : '';

        return `
            <div class="timeline-entry" data-type="${entry.type}" 
                 onclick="memoryTimeline.showDetail(${JSON.stringify(entry).replace(/"/g, '&quot;')})">
                <div class="timeline-icon">${entry.icon}</div>
                <div class="timeline-content">
                    <div class="timeline-header">
                        <h4 class="timeline-title">${entry.title}</h4>
                        <span class="timeline-time">${time}</span>
                    </div>
                    <p class="timeline-subtitle">${entry.subtitle}</p>
                    ${contentPreview ? `<p class="timeline-preview">${contentPreview}</p>` : ''}
                    <div class="timeline-tags">
                        <span class="timeline-tag ${entry.type}">${entry.type}</span>
                    </div>
                </div>
            </div>
        `;
    }

    showDetail(entry) {
        // Use wikilink parser for consistent display
        if (window.wikilinkParser) {
            const entity = {
                type: entry.type,
                data: entry.data
            };
            window.wikilinkParser.showEntityDetail(entity);
        }
    }

    filterByType(type) {
        const allEntries = this.container.querySelectorAll('.timeline-entry');
        allEntries.forEach(entry => {
            if (type === 'all' || entry.dataset.type === type) {
                entry.style.display = 'flex';
            } else {
                entry.style.display = 'none';
            }
        });
    }

    search(query) {
        if (!query) {
            this.render();
            return;
        }

        const searchTerm = query.toLowerCase();
        const filtered = this.entries.filter(entry => {
            return entry.title.toLowerCase().includes(searchTerm) ||
                   (entry.content && entry.content.toLowerCase().includes(searchTerm)) ||
                   entry.subtitle.toLowerCase().includes(searchTerm);
        });

        // Render filtered results
        const temp = this.entries;
        this.entries = filtered;
        this.render();
        this.entries = temp;
    }
}

// Global instance
let memoryTimeline = null;

// Initialize when timeline tab is opened
function initializeTimeline() {
    if (!memoryTimeline) {
        memoryTimeline = new MemoryTimeline('memoryTimelineContainer');
    }
    memoryTimeline.loadData();
}
