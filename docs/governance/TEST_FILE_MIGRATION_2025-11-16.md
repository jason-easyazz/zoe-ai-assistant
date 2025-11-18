# Test File Migration - November 16, 2025

## Overview

As part of the security review fix plan, root-level test files have been moved to comply with the `.zoe/manifest.json` file organization rules.

## Files Moved

### Integration Tests → `tests/integration/`

1. `test_all_systems.py` → `tests/integration/test_all_systems.py`
2. `test_llamacpp_integration.py` → `tests/integration/test_llamacpp_integration.py`
3. `test_code_execution_direct.py` → `tests/integration/test_code_execution_direct.py`
4. `test_code_execution_chat.py` → `tests/integration/test_code_execution_chat.py`

### Unit Tests → `tests/unit/`

1. `test_architecture.py` → `tests/unit/test_architecture.py`

## Running Tests

### All Systems Test

**Old command:**
```bash
python3 test_all_systems.py
```

**New command:**
```bash
python3 tests/integration/test_all_systems.py
```

### Code Execution Tests

**Old command:**
```bash
python3 test_code_execution_direct.py
python3 test_code_execution_chat.py
```

**New command:**
```bash
python3 tests/integration/test_code_execution_direct.py
python3 tests/integration/test_code_execution_chat.py
```

### Architecture Test

**Old command:**
```bash
python3 test_architecture.py
```

**New command:**
```bash
python3 tests/unit/test_architecture.py
```

### LLaMA.cpp Integration Test

**Old command:**
```bash
python3 test_llamacpp_integration.py
```

**New command:**
```bash
python3 tests/integration/test_llamacpp_integration.py
```

## Documentation Updates Required

The following documentation files reference the old paths and should be updated:

- `docs/governance/CODEX_FEEDBACK_IMPLEMENTATION.md` (4 references)
- `docs/architecture/SYSTEM_TEST_RESULTS.md` (3 references)
- `docs/architecture/ARCHITECTURE_PROTECTION.md` (3 references)
- `docs/architecture/CODE_EXECUTION_TESTING.md` (1 reference)
- `docs/archive/jetson-development-2025-11/JETSON_SETUP_COMPLETE.md` (2 references)

## Rationale

The `.zoe/manifest.json` explicitly prohibits `test_*.py` files in the project root (lines 104-117):

```json
"prohibited_patterns": [
  "test_*.py in root"
]
```

This policy ensures:
1. **Clear organization**: Tests are grouped by type (unit/integration/e2e/performance)
2. **Tooling reliability**: Test discovery works consistently
3. **Manifest compliance**: No violations of the single-source-of-truth policy

## Verification

Verify no test files remain in root:

```bash
ls /home/zoe/assistant/test_*.py 2>/dev/null
# Should return no results
```

Verify files are in correct locations:

```bash
ls tests/integration/test_*.py | wc -l
# Should show 4+ files

ls tests/unit/test_*.py | wc -l
# Should show 6+ files
```

## Status

✅ All test files moved successfully
✅ No test files remain in root
✅ Manifest compliance achieved
⚠️ Documentation updates pending (see list above)

