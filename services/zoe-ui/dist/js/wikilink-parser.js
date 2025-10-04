/**
 * Wikilink Parser & Navigation
 * Enables [[link]] style navigation between memories
 */

class WikilinkParser {
    constructor() {
        this.navigationHistory = [];
        this.currentEntity = null;
    }

    /**
     * Parse text for wikilinks and convert to clickable elements
     */
    parseWikilinks(text) {
        if (!text) return text;

        // Match [[link]] or [[link|display text]]
        const wikilinkRegex = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
        
        return text.replace(wikilinkRegex, (match, target, label) => {
            const displayText = label || target;
            return `<a href="#" class="wikilink" data-target="${target}" 
                    onclick="wikilinkParser.navigate('${target}'); return false;">
                    ${displayText}
                </a>`;
        });
    }

    /**
     * Navigate to a wikilink target
     */
    async navigate(target) {
        try {
            // Try to find the entity
            const entity = await this.findEntity(target);
            
            if (entity) {
                this.pushHistory(this.currentEntity);
                this.currentEntity = entity;
                this.showEntityDetail(entity);
            } else {
                // Create new entity prompt
                this.promptCreateEntity(target);
            }
        } catch (error) {
            console.error('Navigation failed:', error);
            this.showError(`Could not navigate to "${target}"`);
        }
    }

    /**
     * Find entity by name across all memory types
     */
    async findEntity(name) {
        const searchTerm = name.toLowerCase();

        // Search people
        const peopleResp = await apiRequest('/memories/?type=people');
        const people = peopleResp.memories || [];
        const person = people.find(p => p.name.toLowerCase() === searchTerm);
        if (person) {
            return { type: 'people', data: person };
        }

        // Search projects
        const projectsResp = await apiRequest('/memories/?type=projects');
        const projects = projectsResp.memories || [];
        const project = projects.find(p => p.name.toLowerCase() === searchTerm);
        if (project) {
            return { type: 'projects', data: project };
        }

        // Search notes by title
        const notesResp = await apiRequest('/memories/?type=notes');
        const notes = notesResp.memories || [];
        const note = notes.find(n => n.title && n.title.toLowerCase() === searchTerm);
        if (note) {
            return { type: 'notes', data: note };
        }

        return null;
    }

    /**
     * Show entity detail modal
     */
    showEntityDetail(entity) {
        const modal = document.getElementById('wikilinkDetailModal');
        if (!modal) return;

        const { type, data } = entity;
        let html = '<div class="wikilink-detail-content">';
        
        // Back button
        if (this.navigationHistory.length > 0) {
            html += '<button onclick="wikilinkParser.back()" class="wikilink-back">← Back</button>';
        }
        
        html += '<button onclick="wikilinkParser.close()" class="wikilink-close">×</button>';

        // Content based on type
        if (type === 'people') {
            html += `
                <h2>${data.name}</h2>
                <p class="entity-type">👤 Person</p>
                ${data.relationship ? `<p><strong>Relationship:</strong> ${data.relationship}</p>` : ''}
                ${data.notes ? `<div class="entity-notes">${this.parseWikilinks(data.notes)}</div>` : ''}
                ${data.phone ? `<p><strong>Phone:</strong> ${data.phone}</p>` : ''}
                ${data.email ? `<p><strong>Email:</strong> <a href="mailto:${data.email}">${data.email}</a></p>` : ''}
            `;
        } else if (type === 'projects') {
            html += `
                <h2>${data.name}</h2>
                <p class="entity-type">📁 Project</p>
                ${data.status ? `<p><strong>Status:</strong> <span class="status-badge status-${data.status}">${data.status}</span></p>` : ''}
                ${data.description ? `<div class="entity-notes">${this.parseWikilinks(data.description)}</div>` : ''}
                ${data.priority ? `<p><strong>Priority:</strong> ${data.priority}</p>` : ''}
            `;
        } else if (type === 'notes') {
            html += `
                <h2>${data.title || 'Note'}</h2>
                <p class="entity-type">📝 Note</p>
                ${data.category ? `<p><strong>Category:</strong> ${data.category}</p>` : ''}
                ${data.content ? `<div class="entity-content">${this.parseWikilinks(data.content)}</div>` : ''}
            `;
        }

        // Edit button
        html += `
            <div class="wikilink-actions">
                <button onclick="wikilinkParser.edit()" class="btn-primary">Edit</button>
            </div>
        `;

        html += '</div>';

        modal.innerHTML = html;
        modal.classList.add('visible');
    }

    /**
     * Navigate back in history
     */
    back() {
        if (this.navigationHistory.length > 0) {
            const previous = this.navigationHistory.pop();
            this.currentEntity = previous;
            if (previous) {
                this.showEntityDetail(previous);
            } else {
                this.close();
            }
        }
    }

    /**
     * Close detail modal
     */
    close() {
        const modal = document.getElementById('wikilinkDetailModal');
        if (modal) {
            modal.classList.remove('visible');
        }
        this.currentEntity = null;
        this.navigationHistory = [];
    }

    /**
     * Edit current entity
     */
    edit() {
        if (this.currentEntity && window.editMemory) {
            const { data } = this.currentEntity;
            this.close();
            window.editMemory(data.id, data);
        }
    }

    /**
     * Prompt to create new entity
     */
    promptCreateEntity(name) {
        if (confirm(`"${name}" doesn't exist. Create it?`)) {
            // Open create modal with pre-filled name
            if (window.openAddModal) {
                window.openAddModal();
                setTimeout(() => {
                    const nameInput = document.getElementById('memoryName');
                    if (nameInput) {
                        nameInput.value = name;
                    }
                }, 100);
            }
        }
    }

    /**
     * Push to navigation history
     */
    pushHistory(entity) {
        if (entity) {
            this.navigationHistory.push(entity);
        }
    }

    /**
     * Show error message
     */
    showError(message) {
        // Use existing notification system if available
        if (window.showNotification) {
            window.showNotification(message, 'error');
        } else {
            alert(message);
        }
    }
}

// Global instance
const wikilinkParser = new WikilinkParser();

// Helper function to apply wikilinks to elements
function applyWikilinks(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = wikilinkParser.parseWikilinks(element.textContent);
    }
}
