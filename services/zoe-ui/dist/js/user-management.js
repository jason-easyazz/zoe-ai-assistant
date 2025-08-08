document.addEventListener('DOMContentLoaded', () => {
    const userDisplay = document.getElementById('currentUserDisplay');
    fetch('/api/whoami').then(res => {
        if (!res.ok) throw new Error();
        return res.json();
    }).then(data => {
        if (userDisplay) {
            userDisplay.textContent = `${data.username} (${data.role})`;
        }
        if (data.role === 'admin') {
            const section = document.getElementById('userManagementSection');
            if (section) section.style.display = 'block';
            loadUsers();
        }
    }).catch(() => {
        if (userDisplay) userDisplay.textContent = 'Not logged in';
    });

    const addBtn = document.getElementById('addUserBtn');
    if (addBtn) addBtn.addEventListener('click', addUser);
});

function loadUsers() {
    fetch('/api/users/list').then(r => r.json()).then(data => {
        const list = document.getElementById('userList');
        if (!list) return;
        list.innerHTML = '';
        data.users.forEach(u => {
            const li = document.createElement('li');
            li.textContent = `${u.username} (${u.role})`;
            const roleSelect = document.createElement('select');
            ['user','admin'].forEach(role => {
                const opt = document.createElement('option');
                opt.value = role;
                opt.textContent = role;
                if (role === u.role) opt.selected = true;
                roleSelect.appendChild(opt);
            });
            roleSelect.onchange = () => changeRole(u.username, roleSelect.value);
            const delBtn = document.createElement('button');
            delBtn.textContent = 'Delete';
            delBtn.onclick = () => deleteUser(u.username);
            li.appendChild(roleSelect);
            li.appendChild(delBtn);
            list.appendChild(li);
        });
    });
}

function addUser() {
    const username = document.getElementById('newUsername').value;
    const passcode = document.getElementById('newPasscode').value;
    const role = document.getElementById('newRole').value;
    fetch('/api/users/register', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, passcode, role})
    }).then(res => {
        if (!res.ok) throw new Error('Failed');
        return res.json();
    }).then(() => {
        loadUsers();
    });
}

function deleteUser(username) {
    fetch(`/api/users/${username}`, {method: 'DELETE'}).then(() => loadUsers());
}

function changeRole(username, role) {
    fetch(`/api/users/${username}/role`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({role})
    }).then(() => loadUsers());
}
