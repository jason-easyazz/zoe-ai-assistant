# Manual UI Test - People System

**Run this test to verify the People UI is fully functional**

---

## Test Checklist

### 1. **Load Page**
- [ ] Navigate to: `https://zoe.the411.life/people.html`
- [ ] Check browser console (F12)
- [ ] Should see: `✅ Loaded X people`
- [ ] Should see people on the canvas and in sidebar

### 2. **Add Person (All Fields)**
- [ ] Click the floating `+` button (bottom right)
- [ ] Modal should open
- [ ] Fill in ALL fields:
  - [ ] Name: "Test User"
  - [ ] Category: "Inner Circle"
  - [ ] Birthday: Select a date
  - [ ] Phone: "555-123-4567"
  - [ ] Email: "test@example.com"
  - [ ] Address: "123 Main St"
  - [ ] Notes: "Test notes"
- [ ] Click "Add Person"
- [ ] Should see: `✅ Added Test User!` notification
- [ ] Person should appear on canvas and in sidebar

### 3. **Click Person (Sidebar)**
- [ ] Click on a person card in the left sidebar
- [ ] Detail panel should slide in from the right
- [ ] Should show all person information

### 4. **Click Person (Canvas)**
- [ ] Click on a person node in the center canvas
- [ ] Console should show: `Canvas click: showing person [name]`
- [ ] Detail panel should open
- [ ] Should show all person information

### 5. **Edit Person**
- [ ] With detail panel open, find the ✏️ Edit button
- [ ] Click ✏️ Edit
- [ ] All fields should become editable (input boxes)
- [ ] Icon should change to 💾
- [ ] Modify some fields:
  - [ ] Change name
  - [ ] Change phone
  - [ ] Change email
  - [ ] Add notes
- [ ] Click 💾 Save
- [ ] Should see: `✅ Updated [name]!` notification
- [ ] Detail panel should refresh with new data
- [ ] Edit mode should exit

### 6. **Verify Changes Persisted**
- [ ] Close detail panel (click X)
- [ ] Click the person again
- [ ] Should show updated information
- [ ] Reload the page
- [ ] Person should still have updated information

### 7. **Via Chat** (Open chat.html)
- [ ] Navigate to chat
- [ ] Type: "Who is Test User?"
- [ ] Should show person details
- [ ] Type: "Remember that Test User loves pizza"
- [ ] Should confirm note added
- [ ] Go back to people.html
- [ ] Click Test User
- [ ] Notes should include "loves pizza"

---

## Expected Behaviors

### ✅ **Working**:
- Click person in sidebar → Detail panel opens
- Click person on canvas → Detail panel opens
- Click Edit → Fields become editable
- Click Save → Changes persist to database
- Add person → All 7 fields save
- Notifications appear for success/error
- Console shows helpful logging

### ❌ **Should NOT happen**:
- Silent failures
- Errors in console
- Changes not saving
- Detail panel not opening
- Edit button not working

---

## Console Commands (For Debugging)

Open console (F12) and try these:

```javascript
// Check people loaded
console.log('People count:', people.length)
console.log('People:', people)

// Test showing a person
if (people.length > 0) {
    showPersonById(people[0].id)
}

// Test API
apiRequest('/api/people').then(r => console.log('API:', r))

// Check for functions
console.log('Functions defined:', {
    showPersonDetail: typeof showPersonDetail,
    showPersonById: typeof showPersonById,
    editPerson: typeof editPerson,
    savePersonChanges: typeof savePersonChanges,
    showNotification: typeof showNotification
})
```

All should return `"function"`.

---

## If Issues Persist

**Check these**:

1. **Hard refresh**: Ctrl+Shift+R (or Cmd+Shift+R on Mac)
2. **Clear cache**: Settings → Clear browsing data → Cached images
3. **Check console**: Any red errors?
4. **Check network**: F12 → Network tab → Any failed requests?

---

## Success Criteria

✅ Can add person with all 7 fields  
✅ Can click person in sidebar  
✅ Can click person on canvas  
✅ Detail panel opens reliably  
✅ Edit button works  
✅ Save button persists changes  
✅ No console errors  
✅ Changes survive page reload  

**If all pass**: 🎉 **People System is fully functional!**


