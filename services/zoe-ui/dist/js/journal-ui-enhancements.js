/**
 * Journal UI Enhancements
 * Autocomplete for people, locations, and missing UI connections
 */

// State for tags
let selectedPeople = [];
let selectedPlaces = [];
let selectedTags = [];

/**
 * People Autocomplete
 */
async function setupPeopleAutocomplete() {
    const peopleInput = document.querySelector('.tag-input[placeholder="Tag people..."]');
    if (!peopleInput) return;
    
    let peopleList = [];
    let debounceTimer;
    
    // Create autocomplete dropdown
    const dropdown = document.createElement('div');
    dropdown.style.cssText = `
        position: absolute;
        background: white;
        border: 1px solid #ddd;
        border-radius: 8px;
        max-height: 200px;
        overflow-y: auto;
        z-index: 1000;
        display: none;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    `;
    peopleInput.parentElement.appendChild(dropdown);
    
    // Load people from API
    async function loadPeople() {
        try {
            const session = window.zoeAuth?.getCurrentSession?.();
            const userId = session?.user_info?.user_id || session?.user_id;
            const qs = userId ? `?user_id=${encodeURIComponent(userId)}&limit=100` : `?limit=100`;
            const response = await fetch(`/api/memories/people${qs}`);
            const data = await response.json();
            peopleList = data.people || [];
        } catch (error) {
            console.error('Failed to load people:', error);
        }
    }
    
    // Filter and show suggestions
    function showSuggestions(query) {
        if (!query) {
            dropdown.style.display = 'none';
            return;
        }
        
        const matches = peopleList.filter(p => 
            p.name.toLowerCase().includes(query.toLowerCase())
        );
        
        if (matches.length === 0) {
            dropdown.style.display = 'none';
            return;
        }
        
        dropdown.innerHTML = matches.slice(0, 5).map(person => `
            <div style="padding: 10px; cursor: pointer; border-bottom: 1px solid #eee; display: flex; align-items: center; gap: 10px;"
                 onmouseover="this.style.background='#f5f5f5'"
                 onmouseout="this.style.background='white'"
                 onclick="selectPerson(${person.id}, '${person.name.replace(/'/g, "\\'")}')">
                ${person.avatar_url ? `<img src="${person.avatar_url}" style="width: 32px; height: 32px; border-radius: 50%;">` : '👤'}
                <div>
                    <div style="font-weight: 500;">${person.name}</div>
                    ${person.relationship ? `<div style="font-size: 11px; color: #666;">${person.relationship}</div>` : ''}
                </div>
            </div>
        `).join('');
        
        dropdown.style.display = 'block';
    }
    
    // Select person
    window.selectPerson = function(id, name) {
        if (selectedPeople.find(p => p.id === id)) {
            dropdown.style.display = 'none';
            peopleInput.value = '';
            return;
        }
        
        selectedPeople.push({ id, name });
        
        // Add chip
        const chip = document.createElement('span');
        chip.className = 'tag-chip person-tag';
        chip.innerHTML = `👤 ${name} <span class="tag-remove" onclick="removePerson(${id})">×</span>`;
        peopleInput.parentElement.insertBefore(chip, peopleInput);
        
        peopleInput.value = '';
        dropdown.style.display = 'none';
    };
    
    window.removePerson = function(id) {
        selectedPeople = selectedPeople.filter(p => p.id !== id);
        const chip = Array.from(peopleInput.parentElement.querySelectorAll('.person-tag'))
            .find(c => c.textContent.includes(selectedPeople.find(p => p.id === id)?.name || ''));
        if (chip) chip.remove();
    };
    
    // Event listeners
    peopleInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            showSuggestions(e.target.value);
        }, 300);
    });
    
    peopleInput.addEventListener('focus', () => {
        if (peopleInput.value) showSuggestions(peopleInput.value);
    });
    
    document.addEventListener('click', (e) => {
        if (!peopleInput.parentElement.contains(e.target)) {
            dropdown.style.display = 'none';
        }
    });
    
    // Load people on init
    await loadPeople();
}

/**
 * Location Autocomplete
 */
async function setupLocationAutocomplete() {
    const locationInput = document.querySelector('.tag-input[placeholder="Where are you?"]');
    if (!locationInput) return;
    
    let debounceTimer;
    
    // Create autocomplete dropdown
    const dropdown = document.createElement('div');
    dropdown.style.cssText = `
        position: absolute;
        background: white;
        border: 1px solid #ddd;
        border-radius: 8px;
        max-height: 200px;
        overflow-y: auto;
        z-index: 1000;
        display: none;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        width: 100%;
    `;
    locationInput.parentElement.appendChild(dropdown);
    
    // Search locations
    async function searchLocations(query) {
        if (!query || query.length < 2) {
            dropdown.style.display = 'none';
            return;
        }
        
        try {
            const response = await fetch(`/api/location/search?query=${encodeURIComponent(query)}&limit=5`);
            const data = await response.json();
            
            if (!data.results || data.results.length === 0) {
                dropdown.style.display = 'none';
                return;
            }
            
            dropdown.innerHTML = data.results.map(place => `
                <div style="padding: 10px; cursor: pointer; border-bottom: 1px solid #eee;"
                     onmouseover="this.style.background='#f5f5f5'"
                     onmouseout="this.style.background='white'"
                     onclick='selectLocation(${JSON.stringify(place).replace(/'/g, "\\'")}'>
                    <div style="font-weight: 500;">📍 ${place.name}</div>
                    <div style="font-size: 11px; color: #666;">${place.display_name}</div>
                </div>
            `).join('');
            
            dropdown.style.display = 'block';
        } catch (error) {
            console.error('Location search failed:', error);
            dropdown.style.display = 'none';
        }
    }
    
    // Select location
    window.selectLocation = function(place) {
        if (selectedPlaces.find(p => p.name === place.name)) {
            dropdown.style.display = 'none';
            locationInput.value = '';
            return;
        }
        
        selectedPlaces.push({
            name: place.name,
            lat: place.lat,
            lng: place.lng
        });
        
        // Add chip
        const chip = document.createElement('span');
        chip.className = 'tag-chip place-tag';
        chip.innerHTML = `📍 ${place.name} <span class="tag-remove" onclick="removePlace('${place.name.replace(/'/g, "\\'")}')">×</span>`;
        locationInput.parentElement.insertBefore(chip, locationInput);
        
        locationInput.value = '';
        dropdown.style.display = 'none';
    };
    
    window.removePlace = function(name) {
        selectedPlaces = selectedPlaces.filter(p => p.name !== name);
        const chip = Array.from(locationInput.parentElement.querySelectorAll('.place-tag'))
            .find(c => c.textContent.includes(name));
        if (chip) chip.remove();
    };
    
    // Event listeners
    locationInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            searchLocations(e.target.value);
        }, 500);
    });
    
    locationInput.addEventListener('focus', () => {
        if (locationInput.value) searchLocations(locationInput.value);
    });
    
    document.addEventListener('click', (e) => {
        if (!locationInput.parentElement.contains(e.target)) {
            dropdown.style.display = 'none';
        }
    });
}

/**
 * Regular Tags (non-people, non-location)
 */
function setupTagInput() {
    const tagInput = document.querySelector('.tag-input[placeholder="Add tags..."]');
    if (!tagInput) return;
    
    tagInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && tagInput.value.trim()) {
            e.preventDefault();
            
            const tag = tagInput.value.trim();
            if (selectedTags.includes(tag)) {
                tagInput.value = '';
                return;
            }
            
            selectedTags.push(tag);
            
            // Add chip
            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            chip.innerHTML = `${tag} <span class="tag-remove" onclick="removeTag('${tag.replace(/'/g, "\\'")}')">×</span>`;
            tagInput.parentElement.insertBefore(chip, tagInput);
            
            tagInput.value = '';
        }
    });
    
    window.removeTag = function(tag) {
        selectedTags = selectedTags.filter(t => t !== tag);
        const chip = Array.from(tagInput.parentElement.querySelectorAll('.tag-chip'))
            .find(c => c.textContent.trim().startsWith(tag));
        if (chip) chip.remove();
    };
}

/**
 * Enhanced Publish Function
 * Overrides the one in journal-api.js to collect all tags
 */
window.publishEntryEnhanced = async function() {
    const titleInput = document.querySelector('#newEntryModal .form-input');
    const contentArea = document.getElementById('entryContent');
    const privacyOption = document.querySelector('.privacy-option.selected');
    
    const title = titleInput?.value || 'Untitled Entry';
    const content = contentArea?.value || '';
    
    if (!content.trim()) {
        alert('Please add some content to your entry');
        return;
    }
    
    const entryData = {
        title,
        content,
        privacy_level: privacyOption?.dataset.privacy || 'private',
        photos: window.uploadedPhotos?.map(p => p.url) || [],
        people_ids: selectedPeople.map(p => p.id),
        place_tags: selectedPlaces,
        tags: selectedTags,
        journey_id: window.currentJourneyId || null,
        journey_stop_id: window.currentStopId || null
    };
    
    try {
        const session = window.zoeAuth?.getCurrentSession?.();
        const userId = session?.user_info?.user_id || session?.user_id;
        const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : '';
        const response = await fetch(`/api/journal/entries${qs}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(entryData)
        });
        
        if (!response.ok) throw new Error('Failed to create entry');
        
        alert('✅ Journal entry published!');
        
        // Optionally trigger a reload if loader is active
        if (typeof window.loadJournalEntries === 'function') {
            window.loadJournalEntries();
        }
        
        // Reset form
        closeNewEntry();
        if (titleInput) titleInput.value = '';
        if (contentArea) contentArea.value = '';
        
        // Clear tags
        selectedPeople = [];
        selectedPlaces = [];
        selectedTags = [];
        document.querySelectorAll('.tag-chip').forEach(chip => chip.remove());
        window.uploadedPhotos = [];
        window.currentJourneyId = null;
        window.currentStopId = null;
        
        localStorage.removeItem('journal_draft');
    } catch (error) {
        console.error('Failed to publish entry:', error);
        alert('❌ Failed to publish entry');
    }
};

/**
 * Show Entry Modal (Read View)
 */
window.showEntryModal = function(entry) {
    // Store entry reference for delete/edit operations
    window.currentEntry = entry;
    
    const modal = document.getElementById('readEntryModal');
    const modalBody = modal.querySelector('.read-modal-body');
    
    // Update image
    const modalImage = modal.querySelector('.read-modal-image');
    if (entry.photos && entry.photos.length > 0) {
        modalImage.src = entry.photos[0];
        modalImage.parentElement.style.display = 'block';
    } else {
        modalImage.parentElement.style.display = 'none';
    }
    
    // Update content
    const title = modal.querySelector('.read-modal-title');
    const meta = modal.querySelector('.read-modal-meta');
    const text = modal.querySelector('.read-modal-text');
    const tags = modal.querySelector('.entry-tags');
    
    if (title) title.textContent = entry.title;
    
    if (meta) {
        const date = new Date(entry.created_at);
        meta.innerHTML = `
            <span>📅 ${date.toLocaleDateString()}</span>
            <span>🕐 ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            ${entry.place_tags && entry.place_tags.length > 0 ? `<span>📍 ${entry.place_tags[0].name}</span>` : ''}
            <span>📖 ${entry.read_time_minutes || 1} min read</span>
        `;
    }
    
    if (text) {
        text.innerHTML = entry.content.replace(/\n/g, '<br>');
    }
    
    if (tags) {
        const allTags = [
            ...(entry.tags || []).map(t => `<span class="tag">${t}</span>`),
            ...(entry.people || []).map(p => `<span class="tag person-tag">👤 ${p.name}</span>`),
            ...(entry.place_tags || []).map(p => `<span class="tag place-tag">📍 ${p.name}</span>`)
        ];
        tags.innerHTML = allTags.join('');
    }
    
    modal.classList.add('active');
};

/**
 * Initialize all enhancements on modal open
 */
const originalOpenNewEntry = window.openNewEntry;
window.openNewEntry = function() {
    if (originalOpenNewEntry) originalOpenNewEntry();
    
    // Setup autocompletes
    setTimeout(() => {
        setupPeopleAutocomplete();
        setupLocationAutocomplete();
        setupTagInput();
    }, 200);
};

/**
 * Initialize on page load
 */
document.addEventListener('DOMContentLoaded', () => {
    console.log('🎨 Journal UI enhancements loaded');
    
    // Override publish button to use enhanced version
    const publishBtn = document.querySelector('.btn-primary');
    if (publishBtn) {
        publishBtn.onclick = publishEntryEnhanced;
    }
});




