window.zoeAPI = {
    async sendMessage(message) {
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });
            if (!response.ok) throw new Error('Chat service unavailable');
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getHealth() {
        try {
            const response = await fetch('/health');
            return await response.json();
        } catch (error) {
            return { status: 'error' };
        }
    },
    async getDiagnostics() {
        try {
            const response = await fetch('/api/diagnostics');
            return await response.json();
        } catch (error) {
            return { status: 'error' };
        }
    },
    async getModules() {
        try {
            const response = await fetch('/api/modules/list');
            return await response.json();
        } catch (error) {
            return { modules: [] };
        }
    },
    async toggleModule(name, enabled) {
        try {
            const response = await fetch('/api/modules/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, enabled })
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getMatrixRooms() {
        try {
            const response = await fetch('/api/matrix/rooms');
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async sendMatrixMessage(room_id, message) {
        try {
            const response = await fetch('/api/matrix/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ room_id, message })
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async receiveMatrixMessages(room_id) {
        try {
            const response = await fetch(`/api/matrix/receive?room_id=${encodeURIComponent(room_id)}`);
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getDashboard() {
        try {
            const response = await fetch('/api/dashboard');
            if (!response.ok) throw new Error('Dashboard unavailable');
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async createTask(task) {
        try {
            const response = await fetch('/api/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(task)
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getTasks() {
        try {
            const response = await fetch('/api/tasks');
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async createJournal(entry) {
        try {
            const response = await fetch('/api/journal', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(entry)
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getJournal() {
        try {
            const response = await fetch('/api/journal');
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getSettings() {
        try {
            const response = await fetch('/api/settings');
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async updateSettings(settings) {
        try {
            const response = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async getEvents(params = '') {
        try {
            const response = await fetch(`/api/events${params}`);
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async createEvent(event) {
        try {
            const response = await fetch('/api/events', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(event)
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async updateEvent(id, event) {
        try {
            const response = await fetch(`/api/events/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(event)
            });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },
    async deleteEvent(id) {
        try {
            const response = await fetch(`/api/events/${id}`, { method: 'DELETE' });
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    }
};
