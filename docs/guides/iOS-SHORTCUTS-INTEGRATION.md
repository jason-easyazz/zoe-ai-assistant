# iOS Shortcuts Integration Guide for Zoe

## Overview

Zoe's PWA fully supports iOS Shortcuts, allowing you to:
- Launch Zoe with voice commands via Siri
- Create custom shortcuts for quick actions
- Add Zoe to iOS Share Sheet for quick saves
- Trigger Zoe actions from other apps

## 🎯 What iOS Shortcuts Can Do

### 1. "Talk to Zoe" - Voice Chat Shortcut
Launch voice conversation instantly from Siri or home screen

### 2. "Add to Zoe" - Quick Capture
Save notes, reminders, or shopping items to Zoe from anywhere

### 3. "Open Zoe Calendar" - Quick Access
Jump directly to today's calendar events

### 4. "Show Zoe Dashboard" - Status View
See your dashboard with calendar, lists, and weather

### 5. Share from Other Apps
Share text, images, or links from Safari, Messages, Photos → Zoe

---

## 📱 Setting Up iOS Shortcuts

### Step 1: Install Zoe as PWA

1. Open Safari and go to your Zoe URL (e.g., `https://zoe.yourname.com`)
2. Tap the **Share** button (square with arrow up)
3. Scroll down and tap **"Add to Home Screen"**
4. Name it "Zoe" and tap **Add**
5. Zoe icon now appears on your home screen

### Step 2: Download the Shortcuts App

If you don't have it already:
- App Store → Search "Shortcuts"
- Download the official Apple Shortcuts app

### Step 3: Create Your First Shortcut

#### Option A: "Talk to Zoe" Shortcut

1. Open **Shortcuts** app
2. Tap **+** (top right) to create new shortcut
3. Tap **Add Action**
4. Search for "Open URL"
5. Enter URL: `https://zoe.yourname.com/chat.html?voice=true`
6. Tap **Next**
7. Name it: **"Talk to Zoe"**
8. Tap **Done**

**Now you can:**
- Say "Hey Siri, Talk to Zoe" to launch voice chat
- Tap the shortcut from Shortcuts widget
- Add to home screen for quick access

#### Option B: "Add to Zoe" Quick Capture

1. Open **Shortcuts** app
2. Create new shortcut
3. Add these actions:
   - **Ask for Input** (Prompt: "What to add to Zoe?")
   - **Open URL**: `https://zoe.yourname.com/chat.html?q=[Input]`
     (Replace `[Input]` with the Ask for Input variable)
4. Name it: **"Add to Zoe"**

**Usage:**
- "Hey Siri, Add to Zoe"
- Siri asks: "What to add to Zoe?"
- You say: "Buy milk and eggs"
- Zoe opens with your message ready

#### Option C: "Open Zoe Calendar" Direct Link

1. Create new shortcut
2. Add action: **Open URL**
3. URL: `https://zoe.yourname.com/calendar.html`
4. Name it: **"Open Zoe Calendar"**

**Siri command:** "Hey Siri, Open Zoe Calendar"

---

## 🔗 Using Zoe App Shortcuts (Built-in)

Zoe's manifest includes built-in shortcuts that appear when you:
- **Long-press the Zoe app icon** on your home screen
- See quick actions menu

Built-in shortcuts:
- 🗨️ **Talk to Zoe** - Start voice conversation
- 📅 **Calendar** - View events
- ✅ **Lists** - Manage shopping/tasks
- 🏠 **Dashboard** - See overview

---

## 📤 iOS Share Sheet Integration

Share content from any app directly to Zoe:

### Setup:
1. Open Safari, Mail, Messages, Photos, etc.
2. Select text, image, or link
3. Tap **Share** button
4. Scroll to find **"Zoe"** in the share sheet
5. Content opens in Zoe

### What You Can Share:
- 📝 **Text** - Saves as note or adds to lists
- 🖼️ **Images** - Uploads to journal
- 🔗 **URLs** - Saves link for later
- 📄 **Files** - Attaches to journal entry

---

## 🎙️ Siri Voice Commands

Once shortcuts are created, these work:

### Direct Commands:
- "Hey Siri, Talk to Zoe"
- "Hey Siri, Add to Zoe"
- "Hey Siri, Open Zoe Calendar"
- "Hey Siri, Show Zoe Dashboard"

### Custom Commands:
You can rename shortcuts to whatever you want:
- "Hey Siri, Chat with my assistant"
- "Hey Siri, Quick note"
- "Hey Siri, What's my schedule"

---

## 🔧 Advanced Shortcuts

### Multi-Step Shortcuts

#### "Morning Routine with Zoe"
```
1. Open URL: https://zoe.yourname.com/dashboard.html
2. Wait 2 seconds
3. Open URL: https://zoe.yourname.com/calendar.html
4. Show Notification: "Good morning! Here's your day"
```

#### "Add Shopping Item"
```
1. Ask for Input: "What do you need from the store?"
2. Set Variable: Item = [Input]
3. Open URL: https://zoe.yourname.com/lists.html?add=[Item]&list=Shopping
```

#### "Quick Journal Entry"
```
1. Ask for Input: "How are you feeling?"
2. Set Variable: Mood = [Input]
3. Get Current Date
4. Open URL: https://zoe.yourname.com/journal.html?entry=[Mood]&date=[Date]
```

---

## 📲 URL Scheme Reference

Zoe supports these URL parameters for shortcuts:

### Chat Page
- `?voice=true` - Auto-start voice mode
- `?q=message` - Pre-fill message
- `?mode=assistant` - Set AI mode

### Calendar Page
- `?date=2025-01-15` - Jump to specific date
- `?view=day|week|month` - Set view mode

### Lists Page
- `?list=Shopping` - Open specific list
- `?add=item` - Add item to active list
- `?category=Groceries` - Filter by category

### Journal Page
- `?entry=text` - Pre-fill entry
- `?mood=happy|sad|neutral` - Set mood
- `?date=today` - Today's entry

### Dashboard Page
- `?widget=calendar|lists|weather` - Focus on widget

---

## 🎨 Customization Tips

### Change Shortcut Icon
1. Edit any shortcut
2. Tap the icon (top right)
3. Choose color and glyph
4. Make it match Zoe's purple/teal theme!

### Add to Home Screen
1. Edit shortcut
2. Tap **⋯** menu
3. Select **"Add to Home Screen"**
4. Now it's a direct launcher like an app!

### Create Shortcut Folder
Group all Zoe shortcuts in a folder:
- Long-press home screen
- Create folder named "Zoe Actions"
- Drag shortcuts into it

---

## ⚡ Quick Start Examples

### 1. Fastest Setup (2 minutes)
```
1. Create "Talk to Zoe" shortcut
2. Test: "Hey Siri, Talk to Zoe"
3. Done! You now have voice access to Zoe
```

### 2. Complete Setup (5 minutes)
```
1. Install Zoe PWA to home screen
2. Create "Talk to Zoe" shortcut
3. Create "Add to Zoe" shortcut  
4. Create "Open Calendar" shortcut
5. Long-press Zoe icon → Test built-in shortcuts
6. Done! You have full iOS integration
```

---

## 🐛 Troubleshooting

### Shortcut doesn't work
- ✅ Check URL is correct (include https://)
- ✅ Make sure Zoe PWA is installed
- ✅ Test URL in Safari first
- ✅ Check Shortcuts app permissions in Settings

### Siri doesn't recognize command
- ✅ Say the exact shortcut name
- ✅ Rename shortcut to simpler phrase
- ✅ Check Siri is enabled in Settings

### Share sheet doesn't show Zoe
- ✅ Make sure PWA is installed (not just bookmarked)
- ✅ Open Zoe once after installation
- ✅ Check Safari → Share → Edit Actions

---

## 📚 Related Documentation

- [PWA Installation Testing](./PWA-INSTALLATION-TESTING.md)

---

## 🎉 Success!

You now have full iOS Shortcuts integration with Zoe! You can:
- ✅ Launch Zoe with voice commands
- ✅ Quick capture from anywhere
- ✅ Share content from other apps
- ✅ Create custom automations
- ✅ Access Zoe faster than ever

**Pro tip:** Create an "Ask Zoe" shortcut that listens for speech, sends it to Zoe, and reads the response back - making Zoe work like a true voice assistant!

