// This will be our JavaScript patch

// Fix the saveEntryToBackend function to properly handle the response
async function saveEntryToBackend(entry) {
    try {
        const response = await fetch('/api/journal', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                title: entry.title,
                content: entry.content,
                mood: entry.mood,
                mood_score: entry.mood_score,
                photo: entry.photo,
                health_info: entry.health_info,
                user_id: 'default'
            })
        });
        
        if (response.ok) {
            const result = await response.json();
            console.log('Entry saved successfully:', result);
            // Reload entries from backend to ensure sync
            await loadEntriesFromBackend();
            return result;
        } else {
            console.error('Failed to save entry:', response.status);
            return null;
        }
    } catch (error) {
        console.error('Error saving journal entry:', error);
        return null;
    }
}

// Fix the saveEntry function to wait for backend save before continuing
async function saveEntry() {
    const title = document.getElementById('entryTitle').value.trim();
    const content = document.getElementById('entryContent').value.trim();
    
    if (!title && !content) {
        alert('Please add a title or write some content!');
        return;
    }
    
    // Get current mood analysis
    const moodData = analyzeTextMood((title + ' ' + content).toLowerCase());
    
    // Get health info
    const healthInfo = isHealthCheckEnabled ? {
        feeling_unwell: true,
        symptoms: document.getElementById('symptomsInput').value.trim()
    } : null;

    if (isEditMode) {
        // Update existing entry
        updateEntry(editingEntryId, title, content, moodData, healthInfo);
    } else {
        // Create new entry - save directly to backend first
        const newEntry = {
            id: Date.now(), // temporary ID
            title: title || `Entry ${new Date().toLocaleDateString()}`,
            content: content,
            mood: moodData.mood,
            mood_score: moodData.score,
            mood_confidence: moodData.confidence,
            photo: uploadedPhoto,
            health_info: healthInfo,
            createdAt: new Date()
        };

        // Save to backend first
        const savedEntry = await saveEntryToBackend(newEntry);
        
        if (savedEntry) {
            // Only add to local array if backend save succeeded
            newEntry.id = savedEntry.id; // Use backend ID
            const existingIndex = entries.findIndex(e => e.id === savedEntry.id);
            if (existingIndex === -1) {
                entries.unshift(newEntry);
            }
            showNotification('Entry saved! 📝');
        } else {
            showNotification('Failed to save entry. Please try again.', 'error');
            return;
        }
    }
    
    renderEntries();
    clearForm();
    exitEditMode();
}
