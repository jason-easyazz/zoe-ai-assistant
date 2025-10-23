# Zoe UI Version History

## Version 1.0.0 - Clean Gridstack Implementation (Current)
**Release Date:** October 22, 2025

### Major Changes
- Complete rewrite using native Gridstack.js
- Removed all custom drag/drop/resize implementations
- Industry-standard widget management system
- Proper size constraints per widget type

### Features
✅ **Native Gridstack Resize** - Drag corners and sides to resize widgets
✅ **Widget Deletion** - Red X button appears on hover in edit mode
✅ **Add Widgets** - Purple + FAB button in edit mode
✅ **Size Constraints** - Each widget has min/max sizes enforced
✅ **Responsive Layout** - Adapts to desktop, tablet, mobile (12/6/4 columns)
✅ **Touch Support** - Full touch drag and resize on mobile devices
✅ **Layout Persistence** - Saves layout to localStorage automatically

### Widget Size Specifications

| Widget | Min Size | Default | Max Size |
|--------|----------|---------|----------|
| Time | 3×3 | 4×4 | 8×5 |
| Weather | 3×3 | 4×4 | 6×5 |
| Tasks | 3×4 | 4×6 | 12×12 |
| Events | 3×4 | 4×6 | 12×12 |
| Shopping | 3×4 | 4×5 | 8×10 |
| Notes | 3×4 | 4×5 | 12×10 |
| Lists (all) | 3×4 | 4×6 | 12×12 |
| Home | 3×3 | 3×3 | 5×4 |
| System | 3×3 | 3×3 | 6×5 |

### Architecture
- **dashboard.js** - Main dashboard controller class
- **widget-base.js** - Base WidgetModule class
- **widget-system.js** - WidgetManager for registration
- **widgets/core/** - Individual widget implementations
- **Gridstack.js** - Native library (v10.3.1)

### Known Issues
- Add widget debugging logs active (will be removed in v1.0.1)
- Widget library needs UX improvements

### Breaking Changes from Pre-1.0
- Custom drag handles removed
- Custom size selector modal removed
- Custom +/- resize buttons removed
- Old layout format not compatible (will auto-migrate)

---

## Version 0.x - Legacy Custom Implementation (Deprecated)
**Status:** Completely replaced in v1.0.0

The previous implementation used custom CSS Grid with manual drag/drop handling.
This has been completely replaced with Gridstack.js for better reliability and maintainability.

---

## Versioning Strategy

Going forward, Zoe UI will follow semantic versioning:

- **MAJOR** (x.0.0) - Breaking changes, major rewrites
- **MINOR** (1.x.0) - New features, non-breaking changes
- **PATCH** (1.0.x) - Bug fixes, minor improvements

### Roadmap
- **v1.0.1** - Remove debug logs, polish UX
- **v1.1.0** - Widget library improvements, themes
- **v1.2.0** - Widget settings/configuration
- **v2.0.0** - Multi-page dashboards, shared layouts

