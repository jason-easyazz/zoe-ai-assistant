# Agent Zero Implementation Checklist

This checklist guides you through implementing the actual Agent Zero WebSocket/HTTP protocol to replace the placeholder implementation.

## Current Status: Placeholder Implementation ✅

The integration is architecturally complete with working:
- ✅ Module structure
- ✅ Intent system
- ✅ Safety guardrails  
- ✅ HTTP endpoints
- ✅ Docker services

**What's missing:** Real communication with Agent Zero's API

---

## Implementation Steps

### Phase 1: Research Agent Zero API

- [ ] Read Agent Zero documentation: https://github.com/frdel/agent-zero
- [ ] Understand Agent Zero's WebSocket/HTTP protocol
- [ ] Identify authentication method
- [ ] Map message format for:
  - [ ] Starting a chat session
  - [ ] Sending a query
  - [ ] Receiving responses
  - [ ] Handling streaming responses
  - [ ] Session management

**Notes:**
- Agent Zero may use WebSocket for real-time communication
- May require session tokens or API keys
- Responses might be streamed (progressive output)

---

### Phase 2: Implement Client Methods

File: `/home/zoe/assistant/modules/agent-zero/client.py`

#### 2.1 Connection & Authentication

```python
async def connect(self) -> bool:
    """Establish WebSocket connection to Agent Zero."""
    # TODO: Implement WebSocket connection
    # - Connect to ws://zoe-agent0/ws (or correct endpoint)
    # - Authenticate if required
    # - Store connection for reuse
    pass

async def disconnect(self):
    """Close WebSocket connection."""
    # TODO: Clean up connection
    pass
```

#### 2.2 Research Method

```python
async def research(self, query: str, depth: str = "thorough") -> Dict[str, Any]:
    """
    Execute research task via Agent Zero.
    
    Steps:
    1. Connect to Agent Zero
    2. Send research prompt
    3. Wait for/stream response
    4. Parse results
    5. Format for Zoe
    """
    # TODO: Replace placeholder implementation
    # Current: Returns fake data
    # Target: Returns real Agent Zero response
```

#### 2.3 Planning Method

```python
async def plan(self, task: str) -> Dict[str, Any]:
    """
    Create multi-step plan via Agent Zero.
    
    Prompt format:
    "Create a detailed step-by-step plan for: {task}
     Include: steps, estimated time, complexity"
    """
    # TODO: Implement
```

#### 2.4 Analysis Method

```python
async def analyze(self, target: str) -> Dict[str, Any]:
    """
    Analyze target via Agent Zero.
    
    Prompt format:
    "Analyze: {target}
     Provide: findings, issues, recommendations"
    """
    # TODO: Implement
```

#### 2.5 Comparison Method

```python
async def compare(self, item_a: str, item_b: str) -> Dict[str, Any]:
    """
    Compare two items via Agent Zero.
    
    Prompt format:
    "Compare {item_a} and {item_b}
     Provide: pros/cons of each, recommendation"
    """
    # TODO: Implement
```

---

### Phase 3: Test with Agent Zero UI

**Before coding, test manually:**

1. **Open Agent Zero UI:**
   ```bash
   open http://192.168.1.218:50001
   ```

2. **Configure Anthropic API Key:**
   - Go to Settings in Agent Zero UI
   - Add your Anthropic API key
   - Test with a simple query

3. **Capture Network Traffic:**
   - Open browser DevTools (F12)
   - Go to Network tab
   - Filter for WebSocket connections
   - Send a test query in Agent Zero UI
   - Observe the WebSocket messages

4. **Document Protocol:**
   - Message format (JSON structure)
   - Connection URL
   - Authentication headers
   - Response format
   - Error handling

---

### Phase 4: Implement & Test

#### 4.1 Update client.py

```python
import websockets
import json
import asyncio

class AgentZeroClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.ws_url = f"{base_url.replace('http', 'ws')}/ws"  # Adjust based on actual endpoint
        self.connection = None
        self.session_id = None
    
    async def connect(self):
        """Connect to Agent Zero WebSocket."""
        self.connection = await websockets.connect(self.ws_url)
        # TODO: Implement authentication if needed
        # TODO: Create/get session ID
    
    async def send_message(self, message: str) -> str:
        """Send message and get response."""
        if not self.connection:
            await self.connect()
        
        # TODO: Format message according to Agent Zero protocol
        payload = {
            "type": "chat",
            "message": message,
            "session_id": self.session_id
        }
        
        await self.connection.send(json.dumps(payload))
        
        # TODO: Handle streaming response
        response = await self.connection.recv()
        
        return json.loads(response)
```

#### 4.2 Test Individual Methods

```bash
# Test research
curl -X POST http://localhost:8101/tools/research \
  -H "Content-Type: application/json" \
  -d '{"query": "what is Zigbee?", "depth": "quick"}'

# Expected: Real response from Agent Zero/Claude
```

#### 4.3 Test via Voice

```
"Zoe, research smart light bulbs"
```

Expected: Real Agent Zero research results

---

### Phase 5: Error Handling

Implement robust error handling for:

- [ ] Connection failures
- [ ] Timeouts (research taking > 2 minutes)
- [ ] Agent Zero unavailable
- [ ] API rate limits
- [ ] Invalid responses
- [ ] Network errors
- [ ] Session expiration

Example:

```python
try:
    result = await client.research(query)
except TimeoutError:
    return {
        "success": False,
        "error": "Research timed out (> 2 minutes)",
        "suggestion": "Try a simpler query"
    }
except ConnectionError:
    return {
        "success": False,
        "error": "Agent Zero unavailable",
        "suggestion": "Check if Agent Zero container is running"
    }
```

---

### Phase 6: Optimization

- [ ] **Connection Pooling** - Reuse WebSocket connections
- [ ] **Response Caching** - Cache research results to reduce API costs
- [ ] **Streaming Responses** - Show progress for long-running tasks
- [ ] **Cost Tracking** - Log Anthropic API usage per query
- [ ] **Timeout Tuning** - Adjust timeouts based on query complexity

---

## Testing Checklist

### Unit Tests

- [ ] Test connection establishment
- [ ] Test authentication
- [ ] Test message sending
- [ ] Test response parsing
- [ ] Test error handling
- [ ] Test timeout handling

### Integration Tests

- [ ] Research query end-to-end
- [ ] Planning query end-to-end
- [ ] Analysis query end-to-end
- [ ] Comparison query end-to-end

### Voice Tests

- [ ] "research smart lights" → Real response
- [ ] "plan home automation" → Real response
- [ ] "analyze my setup" → Real response
- [ ] "compare Zigbee and Z-Wave" → Real response

### Safety Tests

- [ ] Grandma mode blocks code execution
- [ ] Developer mode allows code execution (sandboxed)
- [ ] File operations restricted correctly
- [ ] System commands whitelisted correctly

---

## Monitoring & Debugging

### Log Files

```bash
# Bridge logs
docker logs -f agent-zero-bridge

# Agent Zero logs  
docker logs -f zoe-agent0

# Zoe Core logs
docker logs -f zoe-core
```

### Useful Commands

```bash
# Check if Agent Zero is responding
curl http://zoe-agent0:80

# Check bridge health
curl http://localhost:8101/health

# Test research endpoint
curl -X POST http://localhost:8101/tools/research \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "depth": "quick"}'
```

---

## Cost Considerations

**Anthropic Claude API Pricing:**
- Input: ~$3 per million tokens
- Output: ~$15 per million tokens

**Typical Usage:**
- Research query: 10k-50k tokens ($0.03-$0.75)
- Complex research: 50k-200k tokens ($0.75-$3.00)

**Optimization:**
- Cache results for common queries
- Use "quick" depth for simple questions
- Monitor usage via Anthropic console

---

## Resources

### Documentation
- [Agent Zero GitHub](https://github.com/frdel/agent-zero)
- [Anthropic API Docs](https://docs.anthropic.com/)
- [WebSockets in Python](https://websockets.readthedocs.io/)

### Your Files
- `modules/agent-zero/client.py` - Implement here
- `modules/agent-zero/main.py` - Bridge server (already done)
- `modules/agent-zero/safety.py` - Safety guardrails (already done)

### Test Files
- `modules/agent-zero/TEST_RESULTS.md` - Current test results
- `modules/agent-zero/QUICKSTART.md` - Quick reference

---

## When You're Done

### Verify Implementation

- [ ] All 4 methods implemented (research, plan, analyze, compare)
- [ ] Error handling in place
- [ ] Timeouts configured
- [ ] Logging added
- [ ] Tests passing

### Update Documentation

- [ ] Update README with new capabilities
- [ ] Document any API quirks discovered
- [ ] Add troubleshooting section for common issues
- [ ] Update cost estimates based on real usage

### Deploy & Monitor

- [ ] Test in production with real users
- [ ] Monitor Anthropic API costs
- [ ] Track response times
- [ ] Gather user feedback

---

## Need Help?

If you get stuck:

1. **Check Agent Zero logs:** `docker logs zoe-agent0`
2. **Check bridge logs:** `docker logs agent-zero-bridge`
3. **Review Agent Zero docs:** https://github.com/frdel/agent-zero
4. **Test Agent Zero UI directly:** http://192.168.1.218:50001

The placeholder implementation provides the correct structure - you just need to fill in the actual Agent Zero API calls!

---

**Good luck with the implementation!** The hard part (architecture, integration, safety) is already done. Now it's just about connecting to Agent Zero's API.
