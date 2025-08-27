#!/bin/bash
# DYNAMIC_ROUTELLM_DISCOVERY.sh
# Location: scripts/development/dynamic_routellm_discovery.sh
# Purpose: Dynamically discover ALL available models from each provider

set -e

echo "🔍 DYNAMIC MODEL DISCOVERY FOR ROUTELLM"
echo "========================================"
echo ""
echo "This will dynamically query each AI provider to:"
echo "  🔄 Get their ACTUAL available models (not hardcoded)"
echo "  📊 Update the models list in real-time"
echo "  ✅ Enable providers with working models"
echo "  🧠 Configure intelligent routing"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Step 1: Run the existing model discovery system
echo -e "\n🔄 Running dynamic model discovery..."
docker exec zoe-core python3 << 'PYTHON'
import asyncio
import sys
import os
sys.path.append('/app')

# Import your existing discovery system
from llm_models import LLMModelManager

async def discover_all():
    """Use the existing dynamic discovery system"""
    print("Starting dynamic discovery of ALL models...")
    
    manager = LLMModelManager()
    
    # This will dynamically query each provider's API
    await manager.discover_all_models()
    
    # Show what was discovered
    print("\n📊 Discovery Results:")
    print("=" * 50)
    
    for provider, config in manager.models["providers"].items():
        if config["enabled"]:
            models = config.get("models", [])
            print(f"\n✅ {provider.upper()}: {len(models)} models found")
            for model in models[:3]:  # Show first 3
                print(f"   - {model}")
            if len(models) > 3:
                print(f"   ... and {len(models)-3} more")
        else:
            print(f"\n❌ {provider.upper()}: Disabled or no API key")
    
    print("\n" + "=" * 50)
    return manager.models

# Run the discovery
models = asyncio.run(discover_all())

# Show summary
enabled_count = sum(1 for p in models["providers"].values() if p["enabled"])
total_models = sum(len(p.get("models", [])) for p in models["providers"].values())

print(f"\n📈 Summary:")
print(f"   Enabled Providers: {enabled_count}")
print(f"   Total Models Available: {total_models}")
print(f"   Default Provider: {models.get('default_provider', 'Not set')}")
PYTHON

echo -e "\n✅ Dynamic discovery complete!"

# Step 2: Enhance the routing logic to use discovered models
echo -e "\n🧠 Updating routing logic with discovered models..."
cat > services/zoe-core/dynamic_router.py << 'EOF'
"""
Dynamic Router - Uses real discovered models
"""

import json
import os
from pathlib import Path
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

class DynamicRouter:
    def __init__(self):
        self.models_file = Path("/app/data/llm_models.json")
        self.load_discovered_models()
        
    def load_discovered_models(self):
        """Load the dynamically discovered models"""
        if self.models_file.exists():
            with open(self.models_file) as f:
                data = json.load(f)
                self.providers = data.get("providers", {})
                self.default_provider = data.get("default_provider", "ollama")
        else:
            self.providers = {}
            self.default_provider = "ollama"
    
    def get_best_model_for_complexity(self, complexity: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Select best model based on complexity and what's actually available
        Uses REAL discovered models, not hardcoded lists
        """
        
        # Refresh discovered models
        self.load_discovered_models()
        
        # Define provider preference by complexity
        # But only use what's ACTUALLY available
        if complexity == "simple":
            # Prefer fast, cheap models
            provider_order = ["ollama", "groq", "cohere", "mistral", "openai", "google", "anthropic"]
            size_preference = "small"  # Prefer smaller models
        elif complexity == "medium":
            # Balance speed and quality
            provider_order = ["ollama", "mistral", "openai", "google", "anthropic", "groq"]
            size_preference = "medium"
        else:  # complex
            # Best quality
            provider_order = ["anthropic", "openai", "google", "mistral", "ollama", "groq"]
            size_preference = "large"
        
        # Try providers in preference order
        for provider_name in provider_order:
            provider = self.providers.get(provider_name, {})
            
            if not provider.get("enabled"):
                continue
                
            models = provider.get("models", [])
            if not models:
                continue
            
            # Select model based on size preference
            selected_model = self.select_model_by_size(models, size_preference, provider_name)
            
            if selected_model:
                logger.info(f"Selected {provider_name}/{selected_model} for {complexity} query")
                return provider_name, selected_model
        
        # Fallback to any available model
        for provider_name, provider in self.providers.items():
            if provider.get("enabled") and provider.get("models"):
                return provider_name, provider["models"][0]
        
        return None, None
    
    def select_model_by_size(self, models: list, size_pref: str, provider: str) -> Optional[str]:
        """
        Select model based on size preference
        Uses heuristics based on model names (since they're dynamically discovered)
        """
        if not models:
            return None
        
        # Size indicators in model names
        small_indicators = ["small", "tiny", "1b", "3b", "7b", "haiku", "turbo", "light", "mini"]
        medium_indicators = ["medium", "13b", "30b", "mixtral", "sonnet", "pro"]
        large_indicators = ["large", "70b", "opus", "ultra", "gpt-4", "claude-3"]
        
        if size_pref == "small":
            # Look for small models first
            for model in models:
                model_lower = model.lower()
                if any(ind in model_lower for ind in small_indicators):
                    return model
            # If no small model, take the last one (usually smaller)
            return models[-1] if models else None
            
        elif size_pref == "large":
            # Look for large models first
            for model in models:
                model_lower = model.lower()
                if any(ind in model_lower for ind in large_indicators):
                    return model
            # If no large model, take the first one (usually larger)
            return models[0] if models else None
            
        else:  # medium
            # Look for medium models
            for model in models:
                model_lower = model.lower()
                if any(ind in model_lower for ind in medium_indicators):
                    return model
            # Take middle model
            if len(models) > 1:
                return models[len(models) // 2]
            return models[0] if models else None
    
    def refresh_discovery(self):
        """Trigger a new discovery of models"""
        import subprocess
        result = subprocess.run(
            ["python3", "-c", "from llm_models import LLMModelManager; import asyncio; m = LLMModelManager(); asyncio.run(m.discover_all_models())"],
            capture_output=True,
            text=True,
            cwd="/app"
        )
        if result.returncode == 0:
            self.load_discovered_models()
            return True
        return False

# Global instance
dynamic_router = DynamicRouter()
EOF

# Step 3: Create a test script to verify dynamic routing
echo -e "\n🧪 Creating test script..."
cat > /tmp/test_dynamic_routing.py << 'EOF'
#!/usr/bin/env python3
import sys
sys.path.append('/app')
from dynamic_router import dynamic_router

print("\n🧪 Testing Dynamic Routing with Discovered Models")
print("=" * 50)

# Test different complexity levels
test_cases = [
    ("simple", "What's 2+2?"),
    ("medium", "Explain how neural networks work"),
    ("complex", "Design a distributed microservices architecture")
]

for complexity, query in test_cases:
    provider, model = dynamic_router.get_best_model_for_complexity(complexity)
    print(f"\n{complexity.upper()} Query: '{query[:50]}...'")
    if provider and model:
        print(f"  → Route to: {provider}/{model}")
    else:
        print(f"  → No model available")

print("\n" + "=" * 50)

# Show all available models
print("\n📋 All Discovered Models:")
for provider_name, provider in dynamic_router.providers.items():
    if provider.get("enabled"):
        models = provider.get("models", [])
        if models:
            print(f"\n{provider_name.upper()}:")
            for model in models:
                print(f"  - {model}")
EOF

docker exec zoe-core python3 /tmp/test_dynamic_routing.py

# Step 4: Create UI to trigger discovery
echo -e "\n🎨 Creating discovery trigger in UI..."
cat >> services/zoe-ui/dist/settings.html << 'HTML'

<script>
// Add dynamic discovery button
document.addEventListener('DOMContentLoaded', function() {
    // Find or create button container
    let container = document.querySelector('.settings-section') || document.body;
    
    // Add discovery button
    let discoveryBtn = document.createElement('button');
    discoveryBtn.className = 'btn-primary';
    discoveryBtn.innerHTML = '🔍 Discover AI Models Dynamically';
    discoveryBtn.style.margin = '20px';
    discoveryBtn.onclick = async function() {
        discoveryBtn.disabled = true;
        discoveryBtn.innerHTML = '⏳ Discovering models...';
        
        try {
            let response = await fetch('/api/settings/discover-models', {
                method: 'POST'
            });
            let data = await response.json();
            
            alert(`Discovery complete!\nFound ${data.total_models} models across ${data.enabled_providers} providers`);
            location.reload();
        } catch (error) {
            alert('Discovery failed: ' + error.message);
        }
        
        discoveryBtn.disabled = false;
        discoveryBtn.innerHTML = '🔍 Discover AI Models Dynamically';
    };
    
    // Add to page
    container.appendChild(discoveryBtn);
});
</script>
HTML

# Step 5: Add discovery endpoint to backend
echo -e "\n🔧 Adding discovery endpoint..."
cat >> services/zoe-core/routers/settings.py << 'PYTHON'

@router.post("/discover-models")
async def discover_models():
    """Dynamically discover all available models from providers"""
    import sys
    sys.path.append('/app')
    from llm_models import LLMModelManager
    
    manager = LLMModelManager()
    await manager.discover_all_models()
    
    # Count results
    enabled = sum(1 for p in manager.models["providers"].values() if p["enabled"])
    total = sum(len(p.get("models", [])) for p in manager.models["providers"].values())
    
    return {
        "status": "success",
        "enabled_providers": enabled,
        "total_models": total,
        "providers": {
            name: {
                "enabled": p["enabled"],
                "model_count": len(p.get("models", []))
            }
            for name, p in manager.models["providers"].items()
        }
    }
PYTHON

# Restart services
echo -e "\n🔄 Restarting services..."
docker compose restart zoe-core
sleep 5

echo ""
echo "✅ DYNAMIC ROUTELLM DISCOVERY COMPLETE!"
echo "======================================"
echo ""
echo "The system now:"
echo "  🔍 Dynamically discovers models from each provider's API"
echo "  📊 Updates available models in real-time"
echo "  🧠 Routes based on ACTUAL available models"
echo "  🔄 Can refresh discovery anytime"
echo ""
echo "Available Actions:"
echo "  1. View discovered models: cat data/llm_models.json | jq"
echo "  2. Trigger new discovery: Click button in Settings UI"
echo "  3. Test routing: docker exec zoe-core python3 /tmp/test_dynamic_routing.py"
echo ""
echo "The system will use the REAL models returned by each provider's API!"
