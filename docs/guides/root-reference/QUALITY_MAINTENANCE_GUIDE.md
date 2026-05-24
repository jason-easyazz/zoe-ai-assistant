# Quality Maintenance Guide for Zoe AI Assistant
**Date**: 2025-11-22  
**Purpose**: Maintain system quality, prevent regressions, ensure reliability

> Runtime note (May 2026): `zoe-core`, LiteLLM, and Dockerized llama.cpp are
> retired. The active backend is host-native `zoe-data`; local agent/model
> services run via user systemd units (`hermes-agent`, `openclaw-gateway`,
> `llama-server`, `kokoro-tts`). Use `docs/guides/OPERATOR_RUNBOOK.md` as the
> authoritative operations runbook.

---

## 📋 Table of Contents

1. [Daily Quality Checks](#daily-quality-checks)
2. [Before Every Commit](#before-every-commit)
3. [After Configuration Changes](#after-configuration-changes)
4. [Weekly Maintenance](#weekly-maintenance)
5. [Monthly Health Checks](#monthly-health-checks)
6. [Automated Quality Tools](#automated-quality-tools)
7. [Emergency Procedures](#emergency-procedures)

---

## 🎯 Daily Quality Checks (5 minutes)

### Quick System Health
```bash
cd /home/zoe/assistant

# 1. Check all services are running
docker ps | grep -E "zoe-|livekit|homeassistant"

# 2. Quick functionality test
python3 -c "
import requests
r = requests.post('http://localhost:8000/api/chat', 
                 json={'message': 'System check', 'user_id': 'admin'}, 
                 timeout=10)
print('✅ System OK' if r.status_code == 200 else '❌ System Issue')
"

# 3. Check for errors in logs (last hour)
journalctl --user -u zoe-data --since "1 hour ago" --no-pager | grep -i "error\|exception\|failed" | tail -10

# 4. Monitor disk space
df -h / | tail -1 | awk '{print "Disk Usage: " $5 " of " $2}'
```

**Expected Results**:
- ✅ Core Docker containers and user services running
- ✅ Chat API returns 200
- ✅ No critical errors in logs
- ✅ Disk usage <80%

---

## 🔍 Before Every Commit (Mandatory)

### Pre-Commit Checklist

```bash
# 1. Run automated pre-commit hooks (already installed)
git commit -m "Your message"
# Pre-commit hooks run automatically and will block bad commits

# 2. Manual checks before committing major changes:

# A. Validate project structure
python3 tools/audit/validate_structure.py

# B. Validate critical files exist
python3 tools/audit/validate_critical_files.py

# C. If docker-compose.yml changed:
docker compose config --quiet

# D. If model/user-service config changed:
systemctl --user status llama-server hermes-agent openclaw-gateway --no-pager

# E. Run relevant tests
pytest tests/unit/ -v  # For code changes
# OR
python3 tests/integration/test_all_systems.py  # For system changes
```

### What Pre-Commit Hooks Prevent

The installed pre-commit hook (`.git/hooks/pre-commit`) automatically checks:

1. **Docker Network Configuration**
   - Ensures `name: zoe-network` is present
   - Prevents network misconfiguration issues

2. **Prohibited File Patterns**
   - Blocks: `*_backup.*`, `*_old.*`, `*_v2.*`, `*_new.*`, `*_fixed.*`
   - Enforces: Use git branches, not file duplication

3. **Configuration Sync**
   - Validates docker-compose matches documentation
   - Catches configuration drift

**If pre-commit fails**: Fix the issues, don't bypass with `--no-verify`

---

## ⚙️ After Configuration Changes

### Docker Configuration Changes

```bash
# 1. Validate configuration
docker compose config --quiet

# 2. Check for breaking changes
git diff docker-compose.yml | grep -E "CTX_SIZE|MODEL|network|environment"

# 3. Restart affected services
docker compose restart <service-name>

# 4. Verify functionality
python3 tests/integration/test_all_systems.py

# 5. Monitor logs for 5 minutes
docker compose logs -f <service-name>
```

### Model / Agent Configuration Changes

```bash
# 1. Check active user services
systemctl --user status llama-server hermes-agent openclaw-gateway --no-pager

# 2. Test chat path
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "user_id": "test"}'

# 3. Verify local model and agent health
curl -sf http://localhost:11434/health
curl -sf http://localhost:18789/health
```

### Code Changes

```bash
# 1. Run unit tests
pytest tests/unit/test_<relevant_module>.py -v

# 2. Run integration tests
pytest tests/integration/ -v

# 3. Check focused backend syntax
python3 -m py_compile services/zoe-data/routers/chat.py

# 4. Test end-to-end flow
python3 tests/integration/test_conversation_quality.py
```

---

## 📅 Weekly Maintenance (30 minutes)

### Sunday Evening Routine

```bash
cd /home/zoe/assistant

# 1. Comprehensive System Test
python3 tests/integration/test_all_systems.py > weekly_test_$(date +%Y%m%d).log

# 2. Validate System Architecture
python3 tools/audit/validate_intelligent_architecture.py

# 3. Database Health Check
python3 tools/audit/validate_databases.py

# 4. Check for Configuration Drift
git status
git diff docker-compose.yml
git diff services/zoe-data

# 5. Review Logs for Patterns
journalctl --user -u zoe-data --since "168 hours ago" --no-pager | \
  grep -E "ERROR|WARNING" | \
  sort | uniq -c | sort -rn | head -20

# 6. Performance Check
echo "Checking response times..."
for i in {1..5}; do
  python3 -c "
import requests, time
start = time.time()
r = requests.post('http://localhost:8000/api/chat',
                 json={'message': 'Hello', 'user_id': 'test'},
                 timeout=10)
print(f'Test {$i}: {time.time()-start:.2f}s')
  "
done

# 7. Disk Cleanup (if needed)
docker system df  # Check Docker disk usage
# If >10GB:
# docker system prune -f --volumes  # Only after backup!

# 8. Backup Critical Configs
mkdir -p ~/backups/$(date +%Y%m%d)
cp docker-compose.yml ~/backups/$(date +%Y%m%d)/
cp .env ~/backups/$(date +%Y%m%d)/

# 9. Document Changes
# Update CHANGELOG.md if any changes made this week
```

**Expected Time**: 20-30 minutes  
**When to Run**: Sunday evening or Monday morning  
**Output**: Weekly test log + system health report

---

## 🏥 Monthly Health Checks (1 hour)

### First Sunday of Month

```bash
# 1. Comprehensive Audit
python3 tools/audit/comprehensive_project_audit.py > audit_$(date +%Y%m).md

# 2. Repository Health
python3 tools/reports/repo_health.py

# 3. Test Coverage Analysis
PYTHONPATH=/home/zoe/assistant/services/zoe-data pytest services/zoe-data/tests --cov=services/zoe-data --cov-report=html

# 4. Performance Benchmarking
python3 tools/test_zoe_performance.py

# 5. Security Audit
python3 tools/audit/check_authentication.py
python3 tools/audit/best_practices_check.py

# 6. Database Optimization
python3 tools/audit/analyze_databases.py

# 7. Review Governance Docs
# Read: docs/governance/CLEANUP_SAFETY.md
# Read: docs/governance/DOCKER_NETWORKING_RULES.md
# Update if needed

# 8. Update Dependencies (cautiously)
# docker compose pull  # Only if needed
# Test thoroughly after any updates

# 9. Full Backup
tar -czf ~/backups/zoe-full-$(date +%Y%m%d).tar.gz \
  /home/zoe/assistant \
  --exclude=node_modules \
  --exclude=.git \
  --exclude=__pycache__

# 10. Document Month's Changes
# Add section to CHANGELOG.md
```

---

## 🛠️ Automated Quality Tools

### Available Validation Tools

#### Structure & Safety
```bash
# Validate project structure against manifest
python3 tools/audit/validate_structure.py

# Ensure critical files exist
python3 tools/audit/validate_critical_files.py

# Check for prohibited patterns before delete
python3 tools/audit/validate_before_delete.py <filename>
```

#### Docker & Networks
```bash
# Validate Docker Compose configuration
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.modules.yml config --quiet
```

#### Database Health
```bash
# Validate database schemas and paths
python3 tools/audit/validate_databases.py

# Analyze database performance
python3 tools/audit/analyze_databases.py

# Check database paths in code
python3 tools/audit/check_database_paths.py
```

#### System Architecture
```bash
# Validate intelligent routing system
python3 tools/audit/validate_intelligent_architecture.py

# Validate intent system
python3 tools/intent/validate_intents.py
```

#### Comprehensive Tests
```bash
# All systems test
python3 tests/integration/test_all_systems.py

# P0 feature validation
python3 tests/integration/test_p0_validation.py

# Conversation quality test
python3 tests/integration/test_conversation_quality.py

# Natural language full system test
python3 tests/integration/test_natural_language_full_system.py
```

---

## 🚨 Emergency Procedures

### System Not Responding

```bash
# 1. Check service status
docker ps -a | grep zoe-
systemctl --user status zoe-data hermes-agent openclaw-gateway llama-server --no-pager

# 2. Check logs for errors
journalctl --user -u zoe-data --since "30 min ago" --no-pager
journalctl --user -u hermes-agent --since "30 min ago" --no-pager
journalctl --user -u llama-server --since "30 min ago" --no-pager

# 3. Restart services
systemctl --user restart zoe-data hermes-agent openclaw-gateway

# 4. If still failing, full restart
docker compose up -d zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge
systemctl --user restart llama-server hermes-agent openclaw-gateway kokoro-tts zoe-data

# 5. Monitor startup
journalctl --user -u zoe-data -f
```

### Performance Degradation

```bash
# 1. Check resource usage
docker stats --no-stream

# 2. Check llama.cpp performance
journalctl --user -u llama-server --since "30 min ago" --no-pager | grep "predicted_per_second"

# 3. Check local model server health
curl -sf http://localhost:11434/health

# 4. Restart llama-server if needed
systemctl --user restart llama-server
```

### Agent / Model Routing Failures

```bash
# 1. Check local gateways
curl -sf http://localhost:11434/health
curl -sf http://localhost:18789/health
systemctl --user status hermes-agent --no-pager

# 2. Check loaded local model
journalctl --user -u llama-server --since "1 hour ago" --no-pager | grep "model"

# 3. Restart affected user services
systemctl --user restart hermes-agent openclaw-gateway llama-server
```

### Rollback to Last Known Good State

```bash
# 1. Check git history
git log --oneline -10

# 2. Review recent changes
git diff HEAD~1

# 3. Rollback if needed
git checkout HEAD~1 docker-compose.yml

# 4. Restart services
docker compose up -d
systemctl --user restart zoe-data hermes-agent openclaw-gateway

# 5. Test
python3 tests/integration/test_all_systems.py
```

---

## 📊 Quality Metrics to Track

### System Health Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Service Uptime | >99% | `docker ps` |
| Chat Response Time | <2s | Response time in logs |
| Test Pass Rate | >95% | Weekly test runs |
| Error Rate | <1% | Error count in logs |
| Disk Usage | <80% | `df -h` |

### Code Quality Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Test Coverage | >80% | `pytest --cov` |
| Linter Score | >8.5/10 | `pylint` |
| Critical Files Present | 100% | `validate_critical_files.py` |
| Architecture Violations | 0 | `validate_intelligent_architecture.py` |

### Performance Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Simple Chat | <1s | Test script |
| Complex Query | <3s | Test script |
| Voice Response | <2s | Test script |
| Token Generation | >25/sec | llama.cpp logs |

---

## 🎯 Quality Gates

### Before Merging to Main

- ✅ All pre-commit hooks pass
- ✅ All unit tests pass (`pytest tests/unit/`)
- ✅ All integration tests pass (`pytest tests/integration/`)
- ✅ No critical linter errors
- ✅ Docker validation passes
- ✅ Manual testing completed
- ✅ CHANGELOG updated
- ✅ Documentation updated

### Before Production Deployment

- ✅ All quality gates passed
- ✅ Weekly test completed successfully
- ✅ Performance benchmarks met
- ✅ Security audit passed
- ✅ Backup completed
- ✅ Rollback plan documented
- ✅ Monitoring in place

---

## 📖 Documentation to Review

### Core Governance Docs
- `docs/governance/CLEANUP_SAFETY.md` - File safety procedures
- `docs/governance/DOCKER_NETWORKING_RULES.md` - Network configuration rules
- `docs/governance/CRITICAL_FILES.md` - Files that must never be deleted

### Architecture Docs
- `ARCHITECTURE_DIAGRAM.md` - System architecture overview
- `ARCHITECTURE_REVIEW.md` - Architecture validation
- `COMPREHENSIVE_SYSTEM_REVIEW.md` - Latest system status

### Testing Docs
- `tests/integration/test_all_systems.py` - Comprehensive test suite
- `docs/architecture/TEST_RESULTS_100_PERCENT.md` - Test results history

---

## 🚀 Quick Reference Commands

```bash
# Daily health check (30 seconds)
docker ps && python3 -c "import requests; print('✅' if requests.get('http://localhost:8000/health', timeout=5).status_code==200 else '❌')"

# Before commit (2 minutes)
python3 tools/audit/validate_structure.py && python3 tools/audit/validate_critical_files.py

# After config change (5 minutes)
docker compose config --quiet && docker compose restart <service> && docker compose logs -f <service>

# Weekly test (20 minutes)
python3 tests/integration/test_all_systems.py && docker system df

# Emergency restart
docker compose up -d && systemctl --user restart zoe-data && journalctl --user -u zoe-data -f
```

---

## 💡 Best Practices

1. **Never bypass pre-commit hooks** - They catch critical issues
2. **Test incrementally** - Small commits = easy rollback
3. **Keep backups** - Config files before major changes
4. **Monitor logs** - 5 minutes after any change
5. **Document changes** - Update CHANGELOG.md
6. **Use validation tools** - Before and after changes
7. **Follow governance docs** - They prevent production incidents
8. **Test on staging first** - If you have a staging environment
9. **Keep services updated** - But test thoroughly after updates
10. **Review metrics weekly** - Catch trends before they become issues

---

## 🆘 When Things Go Wrong

1. **Don't panic** - System has multiple safety layers
2. **Check logs first** - `docker logs <service> --tail 100`
3. **Use validation tools** - They often identify the issue
4. **Consult governance docs** - Similar issues may be documented
5. **Use git history** - See what changed recently
6. **Rollback if needed** - Better safe than broken
7. **Test after fix** - Ensure issue is resolved
8. **Document incident** - Help prevent future issues

---

## 📝 Maintenance Log Template

Keep a simple log of maintenance activities:

```markdown
# Maintenance Log

## 2025-11-22 - Weekly Check
- ✅ All services running
- ✅ Tests passing (95%)
- ⚠️ Disk usage at 75% (monitored)
- 📝 Updated runtime config
- ⏱️ Average response time: 0.8s

## 2025-11-15 - Configuration Update
- 🔧 Increased CTX_SIZE to 2048
- ✅ Validated network configuration
- ✅ All tests passing
- 📊 Performance improved 20%
```

---

**Last Updated**: 2025-11-22  
**Review Frequency**: Monthly or when major changes occur  
**Owner**: System Administrator

---

## 🎯 Summary

**Maintain quality by**:
1. Running quick daily checks (5 min)
2. Using pre-commit hooks (automatic)
3. Validating after changes (5-10 min)
4. Running weekly tests (30 min)
5. Performing monthly audits (1 hour)

**Tools are your friend** - Use them regularly to catch issues early!









