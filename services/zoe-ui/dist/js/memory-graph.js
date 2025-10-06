/**
 * Memory Graph Visualization
 * Obsidian-style knowledge graph for Zoe memories
 */

class MemoryGraph {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.network = null;
        this.nodes = new vis.DataSet([]);
        this.edges = new vis.DataSet([]);
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

            // Build graph nodes
            this.buildNodes(people, projects, notes);
            this.buildEdges(people, projects, notes);
            this.render();
        } catch (error) {
            console.error('Failed to load graph data:', error);
        }
    }

    buildNodes(people, projects, notes) {
        this.nodes.clear();

        // Add people nodes
        people.forEach(person => {
            this.nodes.add({
                id: `person-${person.id}`,
                label: person.name,
                group: 'people',
                title: `${person.name}\n${person.relationship || ''}`,
                value: 10,
                data: person
            });
        });

        // Add project nodes
        projects.forEach(project => {
            this.nodes.add({
                id: `project-${project.id}`,
                label: project.name,
                group: 'projects',
                title: `${project.name}\n${project.status || ''}`,
                value: 8,
                data: project
            });
        });

        // Add note nodes (only important ones)
        notes.slice(0, 10).forEach(note => {
            this.nodes.add({
                id: `note-${note.id}`,
                label: note.title || 'Note',
                group: 'notes',
                title: note.content ? note.content.substring(0, 100) : '',
                value: 5,
                data: note
            });
        });
    }

    buildEdges(people, projects, notes) {
        this.edges.clear();

        // Connect people to projects (based on mentions in notes)
        people.forEach(person => {
            projects.forEach(project => {
                // Check if person's name appears in project description
                if (project.description && 
                    project.description.toLowerCase().includes(person.name.toLowerCase())) {
                    this.edges.add({
                        from: `person-${person.id}`,
                        to: `project-${project.id}`,
                        label: 'works on',
                        arrows: 'to'
                    });
                }
            });

            // Connect people to relevant notes
            notes.forEach(note => {
                if (note.content && 
                    note.content.toLowerCase().includes(person.name.toLowerCase())) {
                    this.edges.add({
                        from: `person-${person.id}`,
                        to: `note-${note.id}`,
                        label: 'mentioned in',
                        arrows: 'to',
                        dashes: true
                    });
                }
            });
        });
    }

    render() {
        const data = {
            nodes: this.nodes,
            edges: this.edges
        };

        const options = {
            groups: {
                people: {
                    color: { background: '#7B61FF', border: '#5a4acc' },
                    shape: 'dot',
                    font: { color: '#fff' }
                },
                projects: {
                    color: { background: '#5AE0E0', border: '#3ab8b8' },
                    shape: 'diamond',
                    font: { color: '#fff' }
                },
                notes: {
                    color: { background: '#FFA500', border: '#cc8400' },
                    shape: 'square',
                    font: { color: '#fff' }
                }
            },
            physics: {
                stabilization: true,
                barnesHut: {
                    gravitationalConstant: -2000,
                    centralGravity: 0.3,
                    springLength: 95,
                    springConstant: 0.04
                }
            },
            interaction: {
                hover: true,
                navigationButtons: true,
                keyboard: true,
                tooltipDelay: 200
            },
            nodes: {
                font: {
                    size: 14,
                    face: 'SF Pro Display, system-ui'
                }
            },
            edges: {
                width: 2,
                color: { color: '#ccc', hover: '#7B61FF' },
                smooth: {
                    type: 'continuous'
                }
            }
        };

        this.network = new vis.Network(this.container, data, options);

        // Handle node clicks
        this.network.on('click', (params) => {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                this.handleNodeClick(nodeId);
            }
        });

        // Handle double-click for editing
        this.network.on('doubleClick', (params) => {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node = this.nodes.get(nodeId);
                if (node && node.data) {
                    this.openEditModal(node.data);
                }
            }
        });
    }

    handleNodeClick(nodeId) {
        const node = this.nodes.get(nodeId);
        if (!node || !node.data) return;

        // Show detail panel
        this.showNodeDetail(node.data, node.group);
    }

    showNodeDetail(data, type) {
        const detailPanel = document.getElementById('graphDetailPanel');
        if (!detailPanel) return;

        let html = '<div class="graph-detail-content">';
        html += `<button onclick="memoryGraph.closeDetail()" class="detail-close">×</button>`;

        if (type === 'people') {
            html += `
                <h3>${data.name}</h3>
                <p><strong>Relationship:</strong> ${data.relationship || 'N/A'}</p>
                <p><strong>Notes:</strong> ${data.notes || 'None'}</p>
                ${data.phone ? `<p><strong>Phone:</strong> ${data.phone}</p>` : ''}
                ${data.email ? `<p><strong>Email:</strong> ${data.email}</p>` : ''}
            `;
        } else if (type === 'projects') {
            html += `
                <h3>${data.name}</h3>
                <p><strong>Status:</strong> ${data.status || 'N/A'}</p>
                <p><strong>Description:</strong> ${data.description || 'None'}</p>
                ${data.priority ? `<p><strong>Priority:</strong> ${data.priority}</p>` : ''}
            `;
        } else if (type === 'notes') {
            html += `
                <h3>${data.title || 'Note'}</h3>
                <p>${data.content || 'No content'}</p>
                ${data.category ? `<p><strong>Category:</strong> ${data.category}</p>` : ''}
            `;
        }

        html += '</div>';
        detailPanel.innerHTML = html;
        detailPanel.classList.add('visible');
    }

    closeDetail() {
        const detailPanel = document.getElementById('graphDetailPanel');
        if (detailPanel) {
            detailPanel.classList.remove('visible');
        }
    }

    openEditModal(data) {
        // Trigger the existing edit modal
        if (window.editMemory) {
            window.editMemory(data.id, data);
        }
    }

    resetView() {
        if (this.network) {
            this.network.fit();
        }
    }

    togglePhysics() {
        if (this.network) {
            const physics = this.network.physics.options.enabled;
            this.network.setOptions({ physics: { enabled: !physics } });
        }
    }

    filterByType(type) {
        if (!type || type === 'all') {
            this.nodes.forEach(node => {
                this.nodes.update({ id: node.id, hidden: false });
            });
            return;
        }

        this.nodes.forEach(node => {
            const hidden = node.group !== type;
            this.nodes.update({ id: node.id, hidden });
        });
    }
}

// Global instance
let memoryGraph = null;

// Initialize when graph tab is opened
function initializeGraph() {
    if (!memoryGraph) {
        memoryGraph = new MemoryGraph('memoryGraphContainer');
    }
    memoryGraph.loadData();
}
