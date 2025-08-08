window.zoeAPI = {
    async sendMessage(message) {
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });
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
    }
};

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
      }
  };

