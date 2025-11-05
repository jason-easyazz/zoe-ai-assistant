/**
 * Journal API Integration
 * Connects journal.html to backend APIs
 */

const API_BASE = '/api'; // Use nginx proxy

// Resolve authenticated user id from Zoe Auth (fallback to undefined)
function getUserId() {
    try {
        const session = window.zoeAuth?.getCurrentSession?.();
        return session?.user_info?.user_id || session?.user_id || undefined;
    } catch (_e) {
        return undefined;
    }
}

function withUserId(query = {}) {
    const uid = getUserId();
    return uid ? { user_id: uid, ...query } : { ...query };
}

// Global state
let uploadedPhotos = [];
let currentJourneyId = null;
let currentStopId = null;
let capabilities = {
    supportsJournal: false,
    supportsJourneys: false,
    supportsPrompts: false,
    supportsStreak: false
};

async function probe(pathWithQuery) {
    try {
        const res = await fetch(`${API_BASE}${pathWithQuery}`, { headers: { 'Accept': 'application/json' } });
        if (res.status === 200) return true;
        if (res.status === 401) return true; // exists but requires auth
        if (res.status === 405) return true; // method not allowed implies route exists
        return false;
    } catch (_e) {
        return false;
    }
}

async function detectFeatureAvailability() {
    // Ensure session has user_id cached for consistent user isolation
    try {
        if (!getUserId() && window.zoeAuth?.getCurrentUser) {
            const user = await window.zoeAuth.getCurrentUser();
            if (user && (user.user_info?.user_id || user.user_id) && window.zoeAuth?.setSession) {
                const current = window.zoeAuth.getSessionObject?.() || {};
                const merged = {
                    ...current,
                    user_id: current.user_id || user.user_id || user.user_info?.user_id,
                    user_info: user.user_info || { user_id: user.user_id }
                };
                window.zoeAuth.setSession(merged);
            }
        }
    } catch (_e) {}

    // Probe endpoints; treat 200/401/405 as available
    const [hasJournal, hasPrompts, hasStreak, hasJourneys] = await Promise.all([
        probe('/journal/entries?limit=1'),
        probe('/journal/prompts'),
        probe('/journal/stats/streak'),
        probe('/journeys')
    ]);
    capabilities.supportsJournal = !!hasJournal;
    capabilities.supportsPrompts = !!hasPrompts;
    capabilities.supportsStreak = !!hasStreak;
    capabilities.supportsJourneys = !!hasJourneys;
    return capabilities;
}

/**
 * API Helper Functions
 */
async function apiCall(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        }
    };
    
    const response = await fetch(url, { ...defaultOptions, ...options });
    
    if (!response.ok) {
        throw new Error(`API Error: ${response.statusText}`);
    }
    
    return response.json();
}

/**
 * Journal Entries
 */
async function loadJournalEntries(filters = {}) {
    // Attempt load even if probing disabled it (backend may be present)
    const params = new URLSearchParams({
        limit: 50,
        ...withUserId(filters)
    });
    
    try {
        const data = await apiCall(`/journal/entries?${params}`);
        // Accept multiple response shapes
        const entries = Array.isArray(data)
            ? data
            : (data.entries || data.items || data.data || []);
        if (!Array.isArray(entries)) {
            throw new Error('Unexpected response shape for journal entries');
        }
        displayTimelineEntries(entries);
        return entries;
    } catch (error) {
        console.error('Failed to load entries:', error);
        showError('Failed to load journal entries');
        return [];
    }
}

async function loadOnThisDay() {
    if (!capabilities.supportsJournal) return;
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const data = await apiCall(`/journal/entries/on-this-day${qs}`);
        const entries = Array.isArray(data)
            ? data
            : (data.entries || data.items || data.data || []);
        if (Array.isArray(entries) && entries.length > 0) {
            displayOnThisDay({ entries, date: data.date || new Date().toLocaleDateString() });
        }
    } catch (error) {
        console.error('Failed to load On This Day:', error);
    }
}

async function loadJournalPrompts() {
    if (!capabilities.supportsPrompts) return;
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const data = await apiCall(`/journal/prompts${qs}`);
        
        if (data.prompts && data.prompts.length > 0) {
            displayPrompts(data.prompts);
        }
    } catch (error) {
        console.error('Failed to load prompts:', error);
    }
}

async function createJournalEntry(entryData) {
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const response = await apiCall(`/journal/entries${qs}`, {
            method: 'POST',
            body: JSON.stringify(entryData)
        });
        
        showSuccess('Journal entry created!');
        loadJournalEntries(); // Reload timeline
        return response.entry;
    } catch (error) {
        console.error('Failed to create entry:', error);
        showError('Failed to create journal entry');
        throw error;
    }
}

async function loadStreakData() {
    if (!capabilities.supportsStreak) return;
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const data = await apiCall(`/journal/stats/streak${qs}`);
        displayStreak(data);
    } catch (error) {
        console.error('Failed to load streak:', error);
    }
}

/**
 * Photo Uploads
 */
async function uploadPhotos(files) {
    const formData = new FormData();
    
    for (const file of files) {
        formData.append('files', file);
    }
    const uid = getUserId();
    if (!uid) {
        console.error('❌ Photo upload blocked: missing user_id/session');
        showError('Authentication required. Please log in again.');
        throw new Error('Authentication required');
    }
    formData.append('user_id', uid);
    
    try {
        const sessionId = window.zoeAuth?.getSession?.();
        const response = await fetch(`${API_BASE}/media/upload`, {
            method: 'POST',
            body: formData,
            headers: sessionId ? { 'X-Session-ID': sessionId } : undefined
        });
        
        if (!response.ok) throw new Error('Upload failed');
        
        const photos = await response.json();
        uploadedPhotos = photos;
        return photos;
    } catch (error) {
        console.error('Photo upload failed:', error);
        showError('Failed to upload photos');
        throw error;
    }
}

/**
 * Journeys
 */
async function loadJourneys(status = null) {
    if (!capabilities.supportsJourneys) return [];
    const params = new URLSearchParams(withUserId({}));
    if (status) params.append('status', status);
    
    try {
        const data = await apiCall(`/journeys?${params}`);
        displayJourneys(data.journeys);
        return data.journeys;
    } catch (error) {
        console.error('Failed to load journeys:', error);
        showError('Failed to load journeys');
        return [];
    }
}

async function loadJourneyDetails(journeyId) {
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const journey = await apiCall(`/journeys/${journeyId}${qs}`);
        displayJourneyDetails(journey);
        return journey;
    } catch (error) {
        console.error('Failed to load journey details:', error);
        showError('Failed to load journey details');
    }
}

async function createJourneyCheckin(journeyId, checkinData) {
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const response = await apiCall(`/journeys/${journeyId}/checkin${qs}`, {
            method: 'POST',
            body: JSON.stringify(checkinData)
        });
        
        showSuccess('Journey check-in recorded!');
        loadJourneyDetails(journeyId); // Reload journey
        return response;
    } catch (error) {
        console.error('Failed to create check-in:', error);
        showError('Failed to create check-in');
        throw error;
    }
}

/**
 * Location Search
 */
async function searchLocation(query) {
    try {
        const data = await apiCall(`/location/search?query=${encodeURIComponent(query)}&limit=5`);
        return data.results || [];
    } catch (error) {
        console.error('Location search failed:', error);
        return [];
    }
}

/**
 * Display Functions
 */
function displayTimelineEntries(entries) {
    const timelineView = document.getElementById('timelineView');
    
    // Group by month
    const groupedEntries = {};
    entries.forEach(entry => {
        const date = new Date(entry.created_at);
        const monthKey = date.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
        
        if (!groupedEntries[monthKey]) {
            groupedEntries[monthKey] = [];
        }
        groupedEntries[monthKey].push(entry);
    });
    
    // Clear existing entries (keep timeline line)
    const existingEntries = timelineView.querySelectorAll('.timeline-entry, .timeline-month-separator');
    existingEntries.forEach(el => el.remove());
    
    // Render grouped entries
    Object.keys(groupedEntries).forEach(month => {
        const separator = document.createElement('div');
        separator.className = 'timeline-month-separator';
        separator.innerHTML = `<div class="month-label">${month}</div>`;
        timelineView.appendChild(separator);
        
        groupedEntries[month].forEach((entry, index) => {
            const entryElement = createTimelineEntry(entry, index);
            timelineView.appendChild(entryElement);
        });
    });
}

function createTimelineEntry(entry, index) {
    const date = new Date(entry.created_at);
    const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const timeStr = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    
    const isToday = date.toDateString() === new Date().toDateString();
    
    const entryDiv = document.createElement('div');
    entryDiv.className = 'timeline-entry';
    
    const imageHtml = entry.photos && entry.photos.length > 0
        ? `<img src="${entry.photos[0]}" alt="${entry.title}" class="entry-image">`
        : '';
    
    const peopleHtml = entry.people && entry.people.length > 0
        ? entry.people.map(p => `<span class="tag person-tag">👤 ${p.name}</span>`).join('')
        : '';
    
    const placesHtml = entry.place_tags && entry.place_tags.length > 0
        ? entry.place_tags.map(p => `<span class="tag place-tag">📍 ${p.name}</span>`).join('') 
        : '';
    
    const tagsHtml = entry.tags && entry.tags.length > 0
        ? entry.tags.map(t => `<span class="tag">${t}</span>`).join('')
        : '';
    
    entryDiv.innerHTML = `
        <div class="entry-spacer"></div>
        <div class="timeline-dot-container">
            <div class="timeline-dot"></div>
            <div class="timeline-date">${dateStr}${isToday ? '<br>Today' : ''}</div>
        </div>
        <div class="entry-card" onclick="openEntry(${entry.id})">
            ${imageHtml}
            <div class="entry-content">
                <div class="entry-header">
                    <div>
                        <div class="entry-title">${entry.title}</div>
                        <div class="entry-time">${timeStr} · ${entry.read_time_minutes || 1} min read</div>
                    </div>
                </div>
                <div class="entry-text">${entry.content.substring(0, 150)}...</div>
                <div class="entry-footer">
                    <div class="entry-tags">
                        ${tagsHtml}
                        ${peopleHtml}
                        ${placesHtml}
                    </div>
                    <div class="privacy-badge">
                        <div class="privacy-icon">
                            <div class="privacy-${entry.privacy_level || 'private'}"></div>
                        </div>
                        <span>${(entry.privacy_level || 'private').replace('_', ' ')}</span>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    return entryDiv;
}

function displayOnThisDay(data) {
    // Create "On This Day" section at top of timeline
    const timelineView = document.getElementById('timelineView');
    const existingSection = document.getElementById('onThisDay');
    
    if (existingSection) {
        existingSection.remove();
    }
    
    const section = document.createElement('div');
    section.id = 'onThisDay';
    section.style.cssText = 'background: linear-gradient(135deg, rgba(123,97,255,0.1), rgba(90,224,224,0.1)); padding: 30px; border-radius: 16px; margin-bottom: 40px;';
    
    const entriesHtml = data.entries.slice(0, 3).map(entry => `
        <div style="background: rgba(255,255,255,0.8); padding: 20px; border-radius: 12px; margin-bottom: 15px; cursor: pointer;" onclick="openEntry(${entry.id})">
            <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                <h3 style="font-size: 18px; font-weight: 600; color: #7B61FF;">${entry.title}</h3>
                <span style="font-size: 12px; color: #666; font-weight: 500;">${entry.label}</span>
            </div>
            <p style="font-size: 14px; color: #666; line-height: 1.6;">${entry.content}</p>
        </div>
    `).join('');
    
    section.innerHTML = `
        <h2 style="font-size: 24px; font-weight: 600; margin-bottom: 20px; background: linear-gradient(135deg, #7B61FF, #5AE0E0); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            📅 On This Day - ${data.date}
        </h2>
        ${entriesHtml}
    `;
    
    timelineView.insertBefore(section, timelineView.firstChild);
}

function displayPrompts(prompts) {
    // Show prompts in a banner at the top
    const timelineView = document.getElementById('timelineView');
    const existingPrompts = document.getElementById('journalPrompts');
    
    if (existingPrompts) {
        existingPrompts.remove();
    }
    
    const promptsDiv = document.createElement('div');
    promptsDiv.id = 'journalPrompts';
    promptsDiv.style.cssText = 'background: linear-gradient(135deg, #7B61FF, #5AE0E0); padding: 20px; border-radius: 16px; margin-bottom: 30px; color: white;';
    
    const prompt = prompts[0]; // Show first prompt
    
    promptsDiv.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <div style="font-size: 14px; opacity: 0.9; margin-bottom: 5px;">✨ Journal Prompt</div>
                <div style="font-size: 18px; font-weight: 600;">${prompt.prompt_text}</div>
            </div>
            <button onclick="openEntryWithPrompt(${JSON.stringify(prompt).replace(/"/g, '&quot;')})" 
                    style="background: rgba(255,255,255,0.2); border: 2px solid white; padding: 12px 24px; border-radius: 12px; color: white; font-weight: 600; cursor: pointer; transition: all 0.3s;">
                Write About It →
            </button>
        </div>
    `;
    
    timelineView.insertBefore(promptsDiv, timelineView.firstChild);
}

function displayStreak(data) {
    // Add streak indicator to header
    const navRight = document.querySelector('.nav-right');
    const existingStreak = document.getElementById('streakIndicator');
    
    if (existingStreak) {
        existingStreak.remove();
    }
    
    if (data.current_streak > 0) {
        const streakDiv = document.createElement('div');
        streakDiv.id = 'streakIndicator';
        streakDiv.style.cssText = 'background: rgba(255,165,0,0.1); color: #ff8c00; padding: 8px 16px; border-radius: 20px; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px;';
        streakDiv.innerHTML = `🔥 ${data.current_streak} day streak!`;
        streakDiv.title = `Longest: ${data.longest_streak} days`;
        
        navRight.insertBefore(streakDiv, navRight.firstChild);
    }
}

function displayJourneys(journeys) {
    const journeysView = document.getElementById('journeysView');
    
    // Separate current and past journeys
    const currentJourneys = journeys.filter(j => j.status === 'active' || j.status === 'planning');
    const pastJourneys = journeys.filter(j => j.status === 'completed');
    
    // Clear existing content
    journeysView.innerHTML = '';
    
    // Display current journeys
    if (currentJourneys.length > 0) {
        currentJourneys.forEach(journey => {
            const journeyHtml = createCurrentJourneyHtml(journey);
            journeysView.insertAdjacentHTML('beforeend', journeyHtml);
        });
    }
    
    // Display past journeys
    if (pastJourneys.length > 0) {
        journeysView.insertAdjacentHTML('beforeend', '<h3 class="section-header">Past Journeys</h3>');
        const gridHtml = '<div class="past-journeys-grid">' +
            pastJourneys.map(j => createPastJourneyCard(j)).join('') +
            '</div>';
        journeysView.insertAdjacentHTML('beforeend', gridHtml);
    }
}

function createCurrentJourneyHtml(journey) {
    return `
        <div class="current-journey">
            <div class="current-journey-header">
                <div>
                    <h2 class="journey-title">🧳 ${journey.title}</h2>
                    <p class="journey-subtitle">${journey.description || ''} · Started ${new Date(journey.start_date || journey.created_at).toLocaleDateString()}</p>
                </div>
                <button class="check-in-btn" onclick="openJourneyCheckin(${journey.id})">
                    <span>📍</span>
                    <span>Check In</span>
                </button>
            </div>
            <div id="journeyStops${journey.id}"></div>
        </div>
    `;
}

function createPastJourneyCard(journey) {
    return `
        <div class="journey-card" onclick="viewJourney(${journey.id})">
            ${journey.cover_photo ? `<img src="${journey.cover_photo}" class="journey-image">` : ''}
            <div class="journey-content">
                <div class="journey-location">📍 ${journey.title}</div>
                <div class="journey-dates">${new Date(journey.start_date || journey.created_at).toLocaleDateString()} - ${journey.end_date ? new Date(journey.end_date).toLocaleDateString() : 'Ongoing'}</div>
                <div class="journey-entries">${journey.entry_count} entries · ${journey.stop_count} stops · ${journey.progress_percentage}% complete</div>
            </div>
        </div>
    `;
}

/**
 * UI Interaction Functions
 */
window.openEntry = async function(entryId) {
    try {
        const uid = getUserId();
        const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const entry = await apiCall(`/journal/${entryId}${qs}`);
        showEntryModal(entry.entry || entry);
    } catch (error) {
        console.error('Failed to open entry:', error);
        showError('Failed to load entry');
    }
};

window.openEntryWithPrompt = function(prompt) {
    openNewEntry();
    
    // Pre-fill form with prompt data
    setTimeout(() => {
        if (prompt.auto_fill) {
            const titleInput = document.querySelector('#newEntryModal .form-input');
            if (titleInput && prompt.auto_fill.title) {
                titleInput.value = prompt.auto_fill.title;
            }
        }
        
        currentJourneyId = prompt.context?.journey_id || null;
        currentStopId = prompt.context?.stop_id || null;
    }, 100);
};

window.viewJourney = function(journeyId) {
    loadJourneyDetails(journeyId);
};

window.openJourneyCheckin = function(journeyId) {
    currentJourneyId = journeyId;
    openNewEntry();
    
    // Mark as journey check-in
    setTimeout(() => {
        const titleInput = document.querySelector('#newEntryModal .form-input');
        if (titleInput) {
            titleInput.placeholder = 'Check-in title...';
        }
    }, 100);
};

/**
 * Form Submission
 */
window.publishEntry = async function() {
    const titleInput = document.querySelector('#newEntryModal .form-input');
    const contentArea = document.getElementById('entryContent');
    const privacyOption = document.querySelector('.privacy-option.selected');
    
    const title = titleInput?.value || 'Untitled Entry';
    const content = contentArea?.value || '';
    
    if (!content.trim()) {
        showError('Please add some content to your entry');
        return;
    }
    
    const entryData = {
        title,
        content,
        privacy_level: privacyOption?.dataset.privacy || 'private',
        photos: uploadedPhotos.map(p => p.url),
        journey_id: currentJourneyId,
        journey_stop_id: currentStopId,
        tags: [] // TODO: Collect from tag inputs
    };
    
    try {
        await createJournalEntry(entryData);
        closeNewEntry();
        
        // Reset form
        if (titleInput) titleInput.value = '';
        if (contentArea) contentArea.value = '';
        uploadedPhotos = [];
        currentJourneyId = null;
        currentStopId = null;
    } catch (error) {
        // Error already shown in createJournalEntry
    }
};

/**
 * Notification Helpers
 */
function showSuccess(message) {
    // TODO: Implement toast notifications
    console.log('✅', message);
    alert(message);
}

function showError(message) {
    // TODO: Implement toast notifications
    console.error('❌', message);
    alert(message);
}

/**
 * Initialize on page load
 */
document.addEventListener('DOMContentLoaded', () => {
    detectFeatureAvailability().then(() => {
        // Only invoke features supported by the backend
        loadJournalEntries();
        loadOnThisDay();
        loadJournalPrompts();
        loadStreakData();
        loadJourneys();
    });
});




