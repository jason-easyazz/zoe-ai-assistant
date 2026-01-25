# Quick Start: Zoe Module System

**Get started with Zoe's modular architecture in 5 minutes.**

---

## 🚀 Using Modules (Users)

### List Available Modules
```bash
cd /home/zoe/assistant
python tools/zoe_module.py list
```

### Enable a Module
```bash
python tools/zoe_module.py enable zoe-music
```
→ Music features appear everywhere:
- Voice: "play some music"
- Chat: "search for Beatles"
- UI: Music widgets in dashboard

### Disable a Module
```bash
python tools/zoe_module.py disable zoe-music
```
→ Music features disappear completely

---

## 🛠️ Building Modules (Developers)

### Create Your First Module (10 minutes)

```bash
# 1. Copy template
cd modules/
cp -r zoe-music zoe-hello

# 2. Create backend
cat > zoe-hello/main.py << 'PYTHON'
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="Hello Module")

# Serve widgets
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/widget/manifest")
async def manifest():
    return FileResponse("static/manifest.json")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/tools/hello_world")
async def hello_world(user_id: str):
    return {"message": f"Hello {user_id}!"}
PYTHON

# 3. Create widget
mkdir -p zoe-hello/static/js
cat > zoe-hello/static/js/hello-widget.js << 'JS'
class HelloWidget {
    async init(container) {
        this.mcp = new MCPClient();
        await this.mcp.init();
        
        const result = await this.mcp.callTool('hello_world', {
            user_id: 'demo'
        });
        
        container.innerHTML = `<h1>${result.message}</h1>`;
    }
}

window.WidgetRegistry.register(HelloWidget, {
    id: 'hello-widget',
    name: 'Hello Widget',
    module: 'hello',
    icon: '👋'
});
JS

# 4. Create manifest
cat > zoe-hello/static/manifest.json << 'JSON'
{
  "module": "zoe-hello",
  "version": "1.0.0",
  "widgets": [{
    "id": "hello-widget",
    "name": "Hello Widget",
    "script": "/static/js/hello-widget.js",
    "icon": "👋"
  }],
  "mcp_tools": ["hello_world"]
}
JSON

# 5. Build
docker build -t zoe-hello .

# 6. Enable
python ../../tools/zoe_module.py enable zoe-hello

# 7. Test
curl http://localhost:8XXX/widget/manifest

# Done! Widget appears in UI
```

---

## 📖 Documentation

- **Complete Guide**: `docs/modules/SELF_CONTAINED_MODULES.md`
- **Requirements**: `docs/modules/MODULE_REQUIREMENTS.md`
- **Reference**: `modules/zoe-music/` (working example)

---

## ✅ System Requirements

**Prerequisites**:
- Docker and Docker Compose
- Python 3.11+
- Network access to `zoe-network`

**That's it!** The module system handles everything else.

---

## 🎯 Success Checklist

Building a new module:
- [ ] Backend with MCP tools
- [ ] Static files directory
- [ ] Widget manifest
- [ ] Widget JavaScript
- [ ] Dockerfile
- [ ] Run validation: `python tools/validate_module.py your-module`
- [ ] Enable: `python tools/zoe_module.py enable your-module`
- [ ] Test in UI

**Follow this checklist and you can't go wrong!**

---

**Questions?** Check `docs/modules/SELF_CONTAINED_MODULES.md`

**Ready to build?** Copy `modules/zoe-music` as your template!
