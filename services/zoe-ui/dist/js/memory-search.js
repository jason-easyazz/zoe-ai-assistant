/**
 * Memory Search System
 * Advanced search across all memory types
 */

class MemorySearch {
    constructor() {
        this.searchResults = [];
        this.searchIndex = [];
    }

    async buildIndex() {
        try {
            // Fetch all memories to build search index
            const [peopleResp, projectsResp, notesResp] = await Promise.all([
                apiRequest('/memories/?type=people'),
                apiRequest('/memories/?type=projects'),
                apiRequest('/memories/?type=notes')
            ]);

            const people = peopleResp.memories || [];
            const projects = projectsResp.memories || [];
            const notes = notesResp.memories || [];

            this.searchIndex = [];

            // Index people
            people.forEach(p => {
                this.searchIndex.push({
                    type: 'people',
                    id: p.id,
                    title: p.name,
                    content: [p.name, p.relationship, p.notes, p.phone, p.email].filter(Boolean).join(' '),
                    data: p
                });
            });

            // Index projects
            projects.forEach(p => {
                this.searchIndex.push({
                    type: 'projects',
                    id: p.id,
                    title: p.name,
                    content: [p.name, p.description, p.status, p.priority].filter(Boolean).join(' '),
                    data: p
                });
            });

            // Index notes
            notes.forEach(n => {
                this.searchIndex.push({
                    type: 'notes',
                    id: n.id,
                    title: n.title || 'Note',
                    content: [n.title, n.content, n.category].filter(Boolean).join(' '),
                    data: n
                });
            });

        } catch (error) {
            console.error('Failed to build search index:', error);
        }
    }

    async search(query) {
        if (!query || query.trim().length < 2) {
            this.searchResults = [];
            return [];
        }

        const searchTerm = query.toLowerCase().trim();
        const words = searchTerm.split(/\s+/);

        // Score each indexed item
        this.searchResults = this.searchIndex
            .map(item => {
                const contentLower = item.content.toLowerCase();
                const titleLower = item.title.toLowerCase();
                
                let score = 0;

                // Exact title match (highest score)
                if (titleLower === searchTerm) score += 100;
                else if (titleLower.includes(searchTerm)) score += 50;

                // Title word matches
                words.forEach(word => {
                    if (titleLower.includes(word)) score += 20;
                });

                // Content matches
                words.forEach(word => {
                    const regex = new RegExp(word, 'gi');
                    const matches = contentLower.match(regex);
                    if (matches) score += matches.length * 5;
                });

                // Type boost (people > projects > notes)
                if (item.type === 'people') score += 3;
                else if (item.type === 'projects') score += 2;
                else if (item.type === 'notes') score += 1;

                return { ...item, score };
            })
            .filter(item => item.score > 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 20); // Top 20 results

        return this.searchResults;
    }

    renderResults(results, containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!results || results.length === 0) {
            container.innerHTML = '<div class="search-empty">No results found</div>';
            return;
        }

        let html = '<div class="search-results">';

        results.forEach(result => {
            const icon = this.getTypeIcon(result.type);
            const excerpt = this.getExcerpt(result.content, 100);

            html += `
                <div class="search-result-item" onclick="memorySearch.selectResult(${JSON.stringify(result).replace(/"/g, '&quot;')})">
                    <div class="search-result-icon">${icon}</div>
                    <div class="search-result-content">
                        <h4 class="search-result-title">${this.highlightMatches(result.title)}</h4>
                        <p class="search-result-excerpt">${this.highlightMatches(excerpt)}</p>
                        <div class="search-result-meta">
                            <span class="search-result-type">${result.type}</span>
                            <span class="search-result-score">Relevance: ${Math.round(result.score)}%</span>
                        </div>
                    </div>
                </div>
            `;
        });

        html += '</div>';
        container.innerHTML = html;
    }

    getTypeIcon(type) {
        const icons = {
            'people': '👤',
            'projects': '📁',
            'notes': '📝'
        };
        return icons[type] || '📄';
    }

    getExcerpt(content, maxLength) {
        if (!content) return '';
        if (content.length <= maxLength) return content;
        return content.substring(0, maxLength) + '...';
    }

    highlightMatches(text) {
        // Simple highlight - could be enhanced with actual query matching
        return text;
    }

    selectResult(result) {
        // Navigate to the selected result using wikilink parser
        if (window.wikilinkParser) {
            const entity = {
                type: result.type,
                data: result.data
            };
            window.wikilinkParser.showEntityDetail(entity);
        }

        // Close search panel
        this.closeSearch();
    }

    closeSearch() {
        const searchPanel = document.getElementById('searchPanel');
        if (searchPanel) {
            searchPanel.classList.remove('visible');
        }
    }

    openSearch() {
        const searchPanel = document.getElementById('searchPanel');
        if (searchPanel) {
            searchPanel.classList.add('visible');
            const searchInput = document.getElementById('searchInput');
            if (searchInput) {
                searchInput.focus();
            }
        }
    }
}

// Global instance
const memorySearch = new MemorySearch();

// Initialize search index when page loads
document.addEventListener('DOMContentLoaded', () => {
    memorySearch.buildIndex();
});

// Search with debounce
let searchTimeout;
function handleSearch(query) {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        const results = await memorySearch.search(query);
        memorySearch.renderResults(results, 'searchResultsContainer');
    }, 300);
}
