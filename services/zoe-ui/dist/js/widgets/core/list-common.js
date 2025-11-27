/**
 * List Common - Shared components and utilities for list widgets
 * Supports hierarchical rendering, reminders, due dates, repeat patterns
 */

// Platform detection - cache result
let HARDWARE_PLATFORM = null;
let MAX_DEPTH = 5;
let ENABLE_ANIMATIONS = true;

// Initialize platform detection
async function detectPlatform() {
    if (HARDWARE_PLATFORM !== null) {
        return HARDWARE_PLATFORM;
    }
    
    try {
        const response = await fetch('/api/system/platform');
        HARDWARE_PLATFORM = await response.json();
        MAX_DEPTH = HARDWARE_PLATFORM === 'pi5' ? 3 : 5;
        ENABLE_ANIMATIONS = HARDWARE_PLATFORM !== 'pi5';
    } catch (error) {
        console.warn('Could not detect platform, defaulting to jetson');
        HARDWARE_PLATFORM = 'jetson';
        MAX_DEPTH = 5;
        ENABLE_ANIMATIONS = true;
    }
    
    return HARDWARE_PLATFORM;
}

// Initialize on load (but don't block)
detectPlatform().catch(() => {
    console.warn('Platform detection failed, using defaults');
});

// Expose to window for script tag loading
if (typeof window !== 'undefined') {
    window.listCommon = {
        detectPlatform,
        MAX_DEPTH,
        ENABLE_ANIMATIONS,
        createItemActionsMenu,
        createReminderPicker,
        createDatePicker,
        createRepeatPatternPicker,
        createInlineEdit,
        createSubItemIndicator,
        renderItemElement
    };
}

/**
 * Item Actions Menu - Dropdown menu for item actions
 */
function createItemActionsMenu(itemId, onSetReminder, onSetDueDate, onSetRepeat, onAddSubItem) {
    const menu = document.createElement('div');
    menu.className = 'item-actions-menu';
    menu.style.cssText = `
        position: absolute;
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(0, 0, 0, 0.1);
        border-radius: 8px;
        padding: 8px 0;
        min-width: 180px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        z-index: 1000;
        display: none;
    `;
    
    const actions = [
        { label: 'Set Reminder', icon: '⏰', action: () => onSetReminder(itemId) },
        { label: 'Set Due Date', icon: '📅', action: () => onSetDueDate(itemId) },
        { label: 'Repeat', icon: '🔄', action: () => onSetRepeat(itemId) },
        { label: 'Add Sub-item', icon: '➕', action: () => onAddSubItem(itemId) }
    ];
    
    actions.forEach(action => {
        const button = document.createElement('button');
        button.className = 'action-menu-item';
        button.style.cssText = `
            width: 100%;
            padding: 8px 16px;
            text-align: left;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 14px;
            color: #333;
            transition: background 0.2s;
        `;
        button.innerHTML = `${action.icon} ${action.label}`;
        button.addEventListener('click', (e) => {
            e.stopPropagation();
            action.action();
            menu.style.display = 'none';
        });
        button.addEventListener('mouseenter', () => {
            button.style.background = 'rgba(123, 97, 255, 0.1)';
        });
        button.addEventListener('mouseleave', () => {
            button.style.background = 'none';
        });
        menu.appendChild(button);
    });
    
    return menu;
}

/**
 * Reminder Picker - Time picker for reminders
 */
function createReminderPicker(onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'picker-overlay';
    overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        backdrop-filter: blur(4px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2000;
    `;
    
    const picker = document.createElement('div');
    picker.className = 'reminder-picker';
    picker.style.cssText = `
        background: white;
        border-radius: 12px;
        padding: 24px;
        min-width: 300px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
    `;
    
    const title = document.createElement('h3');
    title.textContent = 'Set Reminder';
    title.style.cssText = 'margin: 0 0 16px 0; font-size: 18px; color: #333;';
    
    const dateInput = document.createElement('input');
    dateInput.type = 'date';
    dateInput.style.cssText = `
        width: 100%;
        padding: 10px;
        margin-bottom: 12px;
        border: 1px solid #ddd;
        border-radius: 6px;
        font-size: 14px;
    `;
    dateInput.valueAsDate = new Date();
    
    const timeInput = document.createElement('input');
    timeInput.type = 'time';
    timeInput.style.cssText = `
        width: 100%;
        padding: 10px;
        margin-bottom: 16px;
        border: 1px solid #ddd;
        border-radius: 6px;
        font-size: 14px;
    `;
    timeInput.value = '09:00';
    
    const buttonContainer = document.createElement('div');
    buttonContainer.style.cssText = 'display: flex; gap: 8px; justify-content: flex-end;';
    
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = `
        padding: 8px 16px;
        border: 1px solid #ddd;
        background: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
    `;
    cancelBtn.addEventListener('click', () => overlay.remove());
    
    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = 'Set';
    confirmBtn.style.cssText = `
        padding: 8px 16px;
        border: none;
        background: #7B61FF;
        color: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
    `;
    confirmBtn.addEventListener('click', () => {
        const date = dateInput.value;
        const time = timeInput.value;
        const reminderTime = `${date} ${time}:00`;
        onConfirm(reminderTime);
        overlay.remove();
    });
    
    buttonContainer.appendChild(cancelBtn);
    buttonContainer.appendChild(confirmBtn);
    
    picker.appendChild(title);
    picker.appendChild(dateInput);
    picker.appendChild(timeInput);
    picker.appendChild(buttonContainer);
    
    overlay.appendChild(picker);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.remove();
        }
    });
    
    document.body.appendChild(overlay);
}

/**
 * Date Picker - Date/time picker for due dates
 */
function createDatePicker(onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'picker-overlay';
    overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        backdrop-filter: blur(4px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2000;
    `;
    
    const picker = document.createElement('div');
    picker.className = 'date-picker';
    picker.style.cssText = `
        background: white;
        border-radius: 12px;
        padding: 24px;
        min-width: 300px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
    `;
    
    const title = document.createElement('h3');
    title.textContent = 'Set Due Date';
    title.style.cssText = 'margin: 0 0 16px 0; font-size: 18px; color: #333;';
    
    const dateInput = document.createElement('input');
    dateInput.type = 'date';
    dateInput.style.cssText = `
        width: 100%;
        padding: 10px;
        margin-bottom: 12px;
        border: 1px solid #ddd;
        border-radius: 6px;
        font-size: 14px;
    `;
    
    const timeInput = document.createElement('input');
    timeInput.type = 'time';
    timeInput.style.cssText = `
        width: 100%;
        padding: 10px;
        margin-bottom: 16px;
        border: 1px solid #ddd;
        border-radius: 6px;
        font-size: 14px;
    `;
    
    const buttonContainer = document.createElement('div');
    buttonContainer.style.cssText = 'display: flex; gap: 8px; justify-content: flex-end;';
    
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = `
        padding: 8px 16px;
        border: 1px solid #ddd;
        background: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
    `;
    cancelBtn.addEventListener('click', () => overlay.remove());
    
    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = 'Set';
    confirmBtn.style.cssText = `
        padding: 8px 16px;
        border: none;
        background: #7B61FF;
        color: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
    `;
    confirmBtn.addEventListener('click', () => {
        onConfirm(dateInput.value, timeInput.value || null);
        overlay.remove();
    });
    
    buttonContainer.appendChild(cancelBtn);
    buttonContainer.appendChild(confirmBtn);
    
    picker.appendChild(title);
    picker.appendChild(dateInput);
    picker.appendChild(timeInput);
    picker.appendChild(buttonContainer);
    
    overlay.appendChild(picker);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.remove();
        }
    });
    
    document.body.appendChild(overlay);
}

/**
 * Repeat Pattern Picker
 */
function createRepeatPatternPicker(onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'picker-overlay';
    overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        backdrop-filter: blur(4px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2000;
    `;
    
    const picker = document.createElement('div');
    picker.className = 'repeat-picker';
    picker.style.cssText = `
        background: white;
        border-radius: 12px;
        padding: 24px;
        min-width: 300px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
    `;
    
    const title = document.createElement('h3');
    title.textContent = 'Repeat Pattern';
    title.style.cssText = 'margin: 0 0 16px 0; font-size: 18px; color: #333;';
    
    const patterns = ['daily', 'weekly', 'monthly', 'yearly', 'custom'];
    let selectedPattern = null;
    
    patterns.forEach(pattern => {
        const label = document.createElement('label');
        label.style.cssText = 'display: block; margin-bottom: 12px; cursor: pointer;';
        
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'repeat-pattern';
        radio.value = pattern;
        radio.style.cssText = 'margin-right: 8px;';
        radio.addEventListener('change', () => {
            selectedPattern = pattern;
        });
        
        const text = document.createElement('span');
        text.textContent = pattern.charAt(0).toUpperCase() + pattern.slice(1);
        
        label.appendChild(radio);
        label.appendChild(text);
        picker.appendChild(label);
    });
    
    const intervalLabel = document.createElement('label');
    intervalLabel.textContent = 'Interval:';
    intervalLabel.style.cssText = 'display: block; margin-top: 16px; margin-bottom: 8px; font-size: 14px;';
    
    const intervalInput = document.createElement('input');
    intervalInput.type = 'number';
    intervalInput.min = '1';
    intervalInput.value = '1';
    intervalInput.style.cssText = `
        width: 100%;
        padding: 8px;
        border: 1px solid #ddd;
        border-radius: 6px;
        font-size: 14px;
    `;
    
    picker.appendChild(intervalLabel);
    picker.appendChild(intervalInput);
    
    const buttonContainer = document.createElement('div');
    buttonContainer.style.cssText = 'display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;';
    
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = `
        padding: 8px 16px;
        border: 1px solid #ddd;
        background: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
    `;
    cancelBtn.addEventListener('click', () => overlay.remove());
    
    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = 'Set';
    confirmBtn.style.cssText = `
        padding: 8px 16px;
        border: none;
        background: #7B61FF;
        color: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
    `;
    confirmBtn.addEventListener('click', () => {
        if (selectedPattern) {
            const pattern = {
                type: selectedPattern,
                interval: parseInt(intervalInput.value) || 1
            };
            onConfirm(pattern);
        }
        overlay.remove();
    });
    
    buttonContainer.appendChild(cancelBtn);
    buttonContainer.appendChild(confirmBtn);
    picker.appendChild(buttonContainer);
    
    overlay.appendChild(picker);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.remove();
        }
    });
    
    document.body.appendChild(overlay);
}

/**
 * Inline Edit - Click-to-edit input for list names
 */
function createInlineEdit(element, onSave) {
    element.style.cursor = 'pointer';
    element.addEventListener('click', () => {
        const originalText = element.textContent;
        const input = document.createElement('input');
        input.type = 'text';
        input.value = originalText;
        input.style.cssText = `
            width: 100%;
            padding: 4px 8px;
            border: 2px solid #7B61FF;
            border-radius: 4px;
            font-size: inherit;
            font-family: inherit;
            background: white;
            outline: none;
        `;
        
        element.textContent = '';
        element.appendChild(input);
        input.focus();
        input.select();
        
        const save = () => {
            const newText = input.value.trim();
            if (newText && newText !== originalText) {
                onSave(newText);
            } else {
                element.textContent = originalText;
            }
        };
        
        input.addEventListener('blur', save);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                save();
            } else if (e.key === 'Escape') {
                element.textContent = originalText;
            }
        });
    });
}

/**
 * Sub Item Indicator - Expand/collapse chevron icon
 */
function createSubItemIndicator(isExpanded, onToggle) {
    const indicator = document.createElement('span');
    indicator.className = 'sub-item-indicator';
    indicator.innerHTML = isExpanded ? '▼' : '▶';
    indicator.style.cssText = `
        display: inline-block;
        width: 16px;
        height: 16px;
        line-height: 16px;
        text-align: center;
        cursor: pointer;
        user-select: none;
        margin-right: 4px;
        transition: ${ENABLE_ANIMATIONS ? 'transform 0.2s' : 'none'};
        color: #666;
        font-size: 10px;
    `;
    
    indicator.addEventListener('click', (e) => {
        e.stopPropagation();
        onToggle();
    });
    
    return indicator;
}

/**
 * Render item with hierarchical indentation
 */
function renderItemElement(item, level, isExpanded, onToggleExpand, onAction, onComplete, onAddSubItem) {
    const itemEl = document.createElement('div');
    itemEl.className = 'list-item';
    itemEl.dataset.itemId = item.id;
    itemEl.style.cssText = `
        padding: 8px 12px;
        padding-left: ${12 + (level * 24)}px;
        border-bottom: 1px solid rgba(0, 0, 0, 0.05);
        position: relative;
        cursor: pointer;
        transition: background 0.2s;
    `;
    
    // Expand/collapse indicator for items with sub-items
    if (item.sub_items && item.sub_items.length > 0) {
        const indicator = createSubItemIndicator(isExpanded, onToggleExpand);
        itemEl.appendChild(indicator);
    } else {
        const spacer = document.createElement('span');
        spacer.style.cssText = 'display: inline-block; width: 16px; margin-right: 4px;';
        itemEl.appendChild(spacer);
    }
    
    // Checkbox
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = item.completed || false;
    checkbox.style.cssText = 'margin-right: 8px; cursor: pointer;';
    checkbox.addEventListener('change', () => {
        onComplete(item.id, checkbox.checked);
    });
    checkbox.addEventListener('click', (e) => e.stopPropagation());
    itemEl.appendChild(checkbox);
    
    // Item text
    const text = document.createElement('span');
    text.textContent = item.text;
    text.style.cssText = `
        flex: 1;
        text-decoration: ${item.completed ? 'line-through' : 'none'};
        opacity: ${item.completed ? 0.6 : 1};
        color: ${item.completed ? '#999' : '#333'};
    `;
    itemEl.appendChild(text);
    
    // Reminder/due date indicators
    const indicators = document.createElement('span');
    indicators.style.cssText = 'margin-left: 8px; font-size: 12px;';
    if (item.reminder_time) {
        const reminderIcon = document.createElement('span');
        reminderIcon.textContent = '⏰';
        reminderIcon.title = `Reminder: ${item.reminder_time}`;
        indicators.appendChild(reminderIcon);
    }
    if (item.due_date) {
        const dueIcon = document.createElement('span');
        dueIcon.textContent = '📅';
        dueIcon.title = `Due: ${item.due_date}`;
        indicators.appendChild(dueIcon);
    }
    itemEl.appendChild(indicators);
    
    // Actions menu button
    const actionsBtn = document.createElement('button');
    actionsBtn.textContent = '⋯';
    actionsBtn.style.cssText = `
        position: absolute;
        right: 12px;
        top: 50%;
        transform: translateY(-50%);
        background: none;
        border: none;
        cursor: pointer;
        font-size: 18px;
        color: #999;
        padding: 4px 8px;
        opacity: 0;
        transition: opacity 0.2s;
    `;
    
    itemEl.addEventListener('mouseenter', () => {
        actionsBtn.style.opacity = '1';
    });
    itemEl.addEventListener('mouseleave', () => {
        actionsBtn.style.opacity = '0';
    });
    
    actionsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        onAction(item.id);
    });
    
    itemEl.appendChild(actionsBtn);
    
    itemEl.addEventListener('click', (e) => {
        if (e.target !== checkbox && e.target !== actionsBtn) {
            itemEl.style.background = 'rgba(123, 97, 255, 0.05)';
            setTimeout(() => {
                itemEl.style.background = '';
            }, 200);
        }
    });
    
    return itemEl;
}

// Export functions
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        detectPlatform,
        MAX_DEPTH,
        ENABLE_ANIMATIONS,
        createItemActionsMenu,
        createReminderPicker,
        createDatePicker,
        createRepeatPatternPicker,
        createInlineEdit,
        createSubItemIndicator,
        renderItemElement
    };
}

