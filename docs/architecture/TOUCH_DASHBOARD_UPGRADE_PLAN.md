# Touch Dashboard Upgrade Plan
**Created:** October 13, 2025  
**Status:** In Progress  
**Priority:** High

## 🎯 Objective
Upgrade the touch dashboard to match desktop functionality and fix critical UX issues.

---

## 🐛 Issues Identified

### 1. **Navigation Bar Inconsistency**
- **Problem:** Touch dashboard uses simple header-bar instead of standard nav-bar
- **Impact:** Inconsistent UX across pages, missing navigation to other sections
- **Current:** Simple header with time, weather, and buttons
- **Required:** Full nav-bar with mini-orb, nav-menu (Chat, Dashboard, Lists, Calendar, Journal, More)

### 2. **Edit Mode Location**
- **Problem:** Edit mode hidden in settings dropdown
- **Impact:** Hard to discover, requires multiple clicks
- **Current:** Settings dropdown → Edit Layout
- **Required:** Floating Action Button (FAB) like AG-UI chat interface

### 3. **Drag and Drop Not Working**
- **Problem:** Cannot drag widgets to rearrange
- **Impact:** Cannot customize layout
- **Investigation Needed:** Check if drag handlers are properly attached

### 4. **Widget Persistence Broken**
- **Problem:** Widget layouts not saving/loading
- **Impact:** Users lose customization on page reload
- **Investigation Needed:** Check localStorage and backend API integration

---

## 🎨 Design Goals

### 1. Unified Navigation
- **Mini-orb** (left): Zoe logo/logout
- **Nav-menu** (center): Chat | Dashboard | Lists | Calendar | Journal | More
- **Right section**: API status + Notifications button
- **Consistent** with desktop dashboard.html

### 2. Floating Edit Mode
- **FAB Button**: Bottom-right corner (like AG-UI)
- **Icon**: ✏️ (edit) / ✓ (done)
- **Position**: Fixed, always visible
- **Function**: Toggle edit mode on/off

### 3. Working Drag & Drop
- **Desktop**: Mouse drag events
- **Touch**: Touch events (touchstart, touchmove, touchend)
- **Visual feedback**: Dragging state, drop zones
- **Persistence**: Auto-save on drop

### 4. Reliable Persistence
- **Primary**: Backend API (`/api/user/layout`)
- **Fallback**: localStorage per device
- **Sync**: Automatic save on any layout change
- **Recovery**: Reset to default if corrupted

---

## 🔧 Implementation Plan

### Phase 1: Navigation Bar (HIGH PRIORITY)
**Estimated Time:** 30 minutes

1. **Replace header-bar with nav-bar**
   ```html
   <div class="nav-bar">
       <div class="nav-left">
           <div class="mini-orb" onclick="handleLogout()"></div>
           <div class="nav-menu">
               <a href="chat.html" class="nav-item">Chat</a>
               <a href="dashboard.html" class="nav-item active">Dashboard</a>
               <a href="lists.html" class="nav-item">Lists</a>
               <a href="calendar.html" class="nav-item">Calendar</a>
               <a href="journal.html" class="nav-item">Journal</a>
               <button class="more-nav-btn" onclick="openMoreOverlay()">More</button>
           </div>
       </div>
       <div class="nav-right">
           <div class="api-indicator" id="apiStatus">●</div>
           <div class="notifications-btn" id="notificationsBtn" onclick="toggleNotifications()">
               <span>🔔</span>
           </div>
       </div>
   </div>
   ```

2. **Copy nav-bar styles from desktop dashboard**
   - Position: fixed top
   - Backdrop blur
   - Responsive design
   - Active state indicators

3. **Add logout handler**
   ```javascript
   function handleLogout() {
       if (window.zoeAuth && typeof window.zoeAuth.logout === 'function') {
           window.zoeAuth.logout();
       }
   }
   ```

### Phase 2: Floating Edit Mode (HIGH PRIORITY)
**Estimated Time:** 20 minutes

1. **Add FAB button**
   ```html
   <button class="fab-edit-btn" id="fabEditBtn" onclick="toggleEditMode()">
       <span id="fabEditIcon">✏️</span>
   </button>
   ```

2. **FAB styles**
   ```css
   .fab-edit-btn {
       position: fixed;
       bottom: 32px;
       right: 32px;
       width: 64px;
       height: 64px;
       border-radius: 50%;
       background: var(--primary-gradient);
       color: white;
       border: none;
       font-size: 24px;
       cursor: pointer;
       box-shadow: 0 8px 24px rgba(123, 97, 255, 0.4);
       z-index: 1100;
       transition: all 0.3s ease;
   }
   
   .fab-edit-btn:hover {
       transform: scale(1.1);
       box-shadow: 0 12px 32px rgba(123, 97, 255, 0.6);
   }
   
   .fab-edit-btn.active {
       background: linear-gradient(135deg, #10b981 0%, #059669 100%);
   }
   ```

3. **Update toggleEditMode function**
   ```javascript
   function toggleEditMode() {
       editMode = !editMode;
       document.body.classList.toggle('edit-mode', editMode);
       
       const fabBtn = document.getElementById('fabEditBtn');
       const fabIcon = document.getElementById('fabEditIcon');
       
       if (editMode) {
           fabBtn.classList.add('active');
           fabIcon.textContent = '✓';
       } else {
           fabBtn.classList.remove('active');
           fabIcon.textContent = '✏️';
       }
   }
   ```

4. **Remove edit mode from settings dropdown**

### Phase 3: Fix Drag & Drop (CRITICAL)
**Estimated Time:** 45 minutes

1. **Verify drag event handlers**
   - Check `setupDragAndDrop()` is called
   - Verify event listeners are attached
   - Test desktop drag events
   - Test touch events

2. **Debug drag issues**
   ```javascript
   function setupDragAndDrop() {
       const widgets = document.querySelectorAll('.widget');
       const grid = document.getElementById('widgetGrid');
       
       console.log('🎯 Setting up drag & drop for', widgets.length, 'widgets');
       
       widgets.forEach(widget => {
           // Desktop
           widget.draggable = true;
           widget.addEventListener('dragstart', handleDragStart);
           widget.addEventListener('dragend', handleDragEnd);
           
           // Touch
           widget.addEventListener('touchstart', handleTouchStart, { passive: false });
           widget.addEventListener('touchmove', handleTouchMove, { passive: false });
           widget.addEventListener('touchend', handleTouchEnd, { passive: false });
       });
       
       // Grid events
       grid.addEventListener('dragover', handleDragOver);
       grid.addEventListener('drop', handleDrop);
   }
   ```

3. **Add visual feedback**
   - Dragging class on widget
   - Drop zones visible in edit mode
   - Smooth transitions

4. **Test on both desktop and touch**

### Phase 4: Fix Persistence (CRITICAL)
**Estimated Time:** 30 minutes

1. **Debug save/load cycle**
   ```javascript
   async function debugWidgetLayout() {
       const deviceId = getDeviceId();
       const session = getUserSession();
       
       console.log('🔍 Device ID:', deviceId);
       console.log('🔍 Session:', session);
       
       // Check localStorage
       const saved = localStorage.getItem(`${WIDGET_STORAGE_KEY}.${deviceId}`);
       console.log('🔍 LocalStorage:', saved);
       
       // Check backend
       if (session?.session_id) {
           const response = await fetch(`/api/user/layout?device_id=${deviceId}&layout_type=touch_dashboard`, {
               headers: { 'X-Session-ID': session.session_id }
           });
           const data = await response.json();
           console.log('🔍 Backend layout:', data);
       }
   }
   ```

2. **Fix save function**
   - Ensure device ID is stable
   - Verify session is active
   - Check API response
   - Fallback to localStorage

3. **Fix load function**
   - Load from backend first
   - Fallback to localStorage
   - Apply layout correctly
   - Re-setup drag handlers

4. **Add manual save button for debugging**

### Phase 5: Testing & Verification
**Estimated Time:** 30 minutes

1. **Navigation Testing**
   - [ ] All nav links work
   - [ ] Active state shows correctly
   - [ ] Notifications button works
   - [ ] Logout works

2. **Edit Mode Testing**
   - [ ] FAB button toggles edit mode
   - [ ] Controls appear/disappear correctly
   - [ ] Widget library opens
   - [ ] Settings removed from dropdown

3. **Drag & Drop Testing**
   - [ ] Can drag widgets (desktop)
   - [ ] Can drag widgets (touch)
   - [ ] Visual feedback works
   - [ ] Drop works correctly
   - [ ] Layout saves after drop

4. **Persistence Testing**
   - [ ] Layout saves on change
   - [ ] Layout loads on page load
   - [ ] Backend sync works
   - [ ] localStorage fallback works
   - [ ] Reset layout works

---

## 📋 Acceptance Criteria

### Must Have (Blocking)
- ✅ Navigation bar matches desktop (mini-orb + nav-menu)
- ✅ Edit mode as floating FAB button
- ✅ Drag & drop works on desktop
- ✅ Drag & drop works on touch
- ✅ Widget layouts persist across refreshes

### Should Have (Important)
- ✅ Smooth animations and transitions
- ✅ Visual feedback during drag
- ✅ Error handling for persistence
- ✅ Auto-save on all changes

### Nice to Have (Enhancement)
- ⏳ Undo/redo for layout changes
- ⏳ Layout presets (work, home, minimal)
- ⏳ Multi-device sync indicator

---

## 🚀 Rollout Plan

### Step 1: Create Backup
```bash
cp /home/zoe/assistant/services/zoe-ui/dist/touch/dashboard.html \
   /home/zoe/assistant/services/zoe-ui/dist/touch/dashboard.html.backup
```

### Step 2: Implement Changes
1. Update navigation bar
2. Add FAB edit button
3. Fix drag & drop
4. Fix persistence

### Step 3: Test Thoroughly
- Test on Raspberry Pi touchscreen
- Test on desktop browser
- Test on mobile device
- Test all user flows

### Step 4: Deploy
- Verify no console errors
- Check performance
- Monitor for issues

### Step 5: Documentation
- Update user guide
- Add screenshots
- Document new features

---

## 🔄 Rollback Plan

If critical issues found:

1. **Immediate Rollback**
   ```bash
   cp /home/zoe/assistant/services/zoe-ui/dist/touch/dashboard.html.backup \
      /home/zoe/assistant/services/zoe-ui/dist/touch/dashboard.html
   ```

2. **Clear browser cache**
   ```javascript
   localStorage.clear();
   ```

3. **Investigate issues**
   - Check console errors
   - Review server logs
   - Test in isolation

---

## 📊 Success Metrics

### Functionality
- 100% of widgets draggable
- 100% layout persistence rate
- 0 navigation errors

### Performance
- < 100ms drag response time
- < 500ms layout save time
- < 1s page load with layout

### User Experience
- Discoverable edit mode (FAB visible)
- Consistent navigation (matches desktop)
- Reliable persistence (no data loss)

---

## 🎯 Timeline

**Total Estimated Time:** 2.5 hours

- **Navigation Bar:** 30 min
- **FAB Edit Mode:** 20 min  
- **Drag & Drop Fix:** 45 min
- **Persistence Fix:** 30 min
- **Testing:** 30 min

**Target Completion:** Same day

---

## 📝 Notes

### Key Decisions
1. **FAB over settings dropdown** - More discoverable, matches AG-UI pattern
2. **Backend-first persistence** - Better for multi-device, localStorage fallback
3. **Touch + mouse events** - Support all input methods
4. **Unified nav-bar** - Consistent UX across all pages

### Technical Debt
- Current touch dashboard has custom header-bar (inconsistent)
- Edit mode was hard to find (UX issue)
- Persistence system not well tested
- Missing drag & drop visual feedback

### Future Enhancements
- Layout templates/presets
- Undo/redo system
- Widget grouping/folders
- Collaborative layouts (family dashboard)

---

**Status:** 🚧 Ready to implement  
**Owner:** Cursor AI Assistant  
**Review Required:** User acceptance testing



