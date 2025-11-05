# Quick Journal Widget Guide

## Overview
The Quick Journal widget is a simple, intuitive way to create journal entries directly from your dashboard. It's designed for speed - just add a photo (optional), write a title, and start typing!

## Features

### 📷 Photo Upload
- Click the photo area to select an image from your device
- Preview your photo immediately
- Remove and replace photos easily
- Photos are automatically uploaded to your journal storage

### ✍️ Title & Content
- Add a descriptive title (or leave it as "Untitled Entry")
- Write freely in the text area
- No word limits or restrictions
- Auto-saves to your private journal

### 💾 Smart Saving
- Save button activates when you start typing
- Entries are saved with timestamp and word count
- Confirmation message when saved successfully
- Form clears automatically after saving

## How to Use

### Adding the Widget
1. Go to your Dashboard or Lists page
2. Click "Add Widget" (+ button)
3. Find "Quick Journal" in the productivity section
4. Click to add it to your layout

### Creating an Entry
1. **Optional:** Click the photo area to add an image
2. Type a title for your entry
3. Start writing in the text area
4. Click "Save Entry" when done
5. Your entry is saved to your journal!

### Clearing the Form
- Click the "Clear" button in the widget header to reset
- This removes all content and any uploaded photo
- Useful for starting fresh without saving

## Integration

### Backend
- Connects to `/api/journal/entries`
- Uses authenticated sessions for user isolation
- Photos uploaded to `/api/media/upload`
- All entries are private by default

### Access Your Entries
- View all entries on the Journal page (`/journal.html`)
- Entries include metadata: word count, read time, timestamp
- Search, filter, and browse your journal history

## Widget Settings

### Size Options
- Default: Medium (4x8 grid cells)
- Minimum: 3x5 grid cells
- Maximum: 8x12 grid cells
- Resizable from widget menu

### Display Locations
- ✅ Dashboard page
- ✅ Lists page
- ✅ Touch interface

## Tips

1. **Quick Capture**: The widget focuses on the text area automatically - just start typing!
2. **Photo First**: Add photos before typing to set the mood for your entry
3. **Daily Habit**: Keep the widget on your dashboard for easy daily journaling
4. **Mobile Friendly**: Works great on tablets and touch devices

## Privacy
- All entries are private by default
- User authentication required
- Your entries are isolated from other users
- Photos stored securely in your upload directory

## Technical Details
- Widget ID: `journal`
- File: `js/widgets/core/journal.js`
- Category: Productivity
- Icon: 📔

---

**Need Help?** Check the main journal documentation or contact support.

