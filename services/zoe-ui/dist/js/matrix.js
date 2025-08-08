let currentRoomId = null;
let pollHandle = null;

async function loadRooms() {
    const data = await window.zoeAPI.getMatrixRooms();
    const list = document.getElementById('matrixRooms');
    if (!list) return;
    list.innerHTML = '';
    if (!data.rooms) return;
    Object.entries(data.rooms).forEach(([id, name]) => {
        const div = document.createElement('div');
        div.className = 'matrix-room';
        div.textContent = name;
        div.onclick = () => selectRoom(id, div);
        list.appendChild(div);
    });
    // Auto-select first room
    const first = list.querySelector('.matrix-room');
    if (first) first.click();
}

async function selectRoom(roomId, element) {
    currentRoomId = roomId;
    document.querySelectorAll('.matrix-room').forEach(r => r.classList.remove('active'));
    if (element) element.classList.add('active');
    await loadMessages();
    if (pollHandle) clearInterval(pollHandle);
    pollHandle = setInterval(loadMessages, 5000);
}

async function loadMessages() {
    if (!currentRoomId) return;
    const data = await window.zoeAPI.receiveMatrixMessages(currentRoomId);
    const container = document.getElementById('chatMessages');
    if (!container) return;
    container.innerHTML = '';
    (data.messages || []).forEach(msg => {
        const div = document.createElement('div');
        div.className = 'matrix-message';
        div.textContent = `${msg.sender}: ${msg.message}`;
        container.appendChild(div);
    });
    container.scrollTop = container.scrollHeight;
}

window.sendMessage = async function() {
    const input = document.getElementById('chatInput');
    if (!input) return;
    const message = input.value.trim();
    if (!message || !currentRoomId) return;
    await window.zoeAPI.sendMatrixMessage(currentRoomId, message);
    input.value = '';
    await loadMessages();
};

document.addEventListener('DOMContentLoaded', loadRooms);
