#!/usr/bin/env python3
"""
Zoe Model Manager CLI
Manage Ollama models and LoRA adapters (inspired by Eclaire's model-cli)
"""
import sys
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict


class ModelManager:
    """Manage Ollama models and LoRA adapters"""
    
    def __init__(self):
        # Auto-detect project root (works for both Pi and Nano)
        project_root = Path(__file__).parent.parent.resolve()
        self.adapters_dir = project_root / "models/adapters"
        self.adapters_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = project_root / "config/models.json"
    
    def list_models(self):
        """List all available Ollama models"""
        print("\n🤖 Available Ollama Models:\n")
        print("┌─" + "─" * 40 + "─┬─" + "─" * 15 + "─┬─" + "─" * 20 + "─┐")
        print(f"│ {'Model Name':<40} │ {'Size':<15} │ {'Status':<20} │")
        print("├─" + "─" * 40 + "─┼─" + "─" * 15 + "─┼─" + "─" * 20 + "─┤")
        
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True
            )
            
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    model_name = parts[0]
                    size = parts[2] if len(parts) > 2 else "Unknown"
                    status = "🟢 Available"
                    print(f"│ {model_name:<40} │ {size:<15} │ {status:<20} │")
        except Exception as e:
            print(f"│ Error: {str(e):<75} │")
        
        print("└─" + "─" * 40 + "─┴─" + "─" * 15 + "─┴─" + "─" * 20 + "─┘\n")
    
    def list_adapters(self):
        """List all LoRA adapters"""
        print("\n🧩 LoRA Adapters:\n")
        print("┌─" + "─" * 30 + "─┬─" + "─" * 15 + "─┬─" + "─" * 12 + "─┬─" + "─" * 15 + "─┐")
        print(f"│ {'Adapter Name':<30} │ {'Date':<15} │ {'Score':<12} │ {'Status':<15} │")
        print("├─" + "─" * 30 + "─┼─" + "─" * 15 + "─┼─" + "─" * 12 + "─┼─" + "─" * 15 + "─┤")
        
        adapters = list(self.adapters_dir.glob("adapter_*"))
        current_link = self.adapters_dir / "current"
        
        if not adapters:
            print(f"│ {'No adapters found':<30} │ {'':<15} │ {'':<12} │ {'':<15} │")
        
        for adapter in sorted(adapters):
            name = adapter.name
            date_str = name.split('_')[1] if '_' in name else "Unknown"
            is_current = current_link.exists() and current_link.resolve() == adapter.resolve()
            status = "🟢 ACTIVE" if is_current else "○ Available"
            
            # Try to read validation score from metadata
            score = "N/A"
            metadata_file = adapter / "metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file) as f:
                        metadata = json.load(f)
                        score = f"{metadata.get('validation_score', 0):.1%}"
                except:
                    pass
            
            print(f"│ {name:<30} │ {date_str:<15} │ {score:<12} │ {status:<15} │")
        
        print("└─" + "─" * 30 + "─┴─" + "─" * 15 + "─┴─" + "─" * 12 + "─┴─" + "─" * 15 + "─┘\n")
    
    def pull_model(self, model_name: str):
        """Pull/download a model from Ollama registry"""
        print(f"\n📥 Pulling model: {model_name}")
        
        try:
            subprocess.run(
                ["ollama", "pull", model_name],
                check=True
            )
            print(f"✅ Successfully pulled {model_name}\n")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to pull model: {e}\n")
            sys.exit(1)
    
    def set_default(self, model_name: str):
        """Set default model for Zoe"""
        project_root = Path(__file__).parent.parent.resolve()
        config_file = project_root / "config/default_model.txt"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        config_file.write_text(model_name)
        print(f"✅ Set default model to: {model_name}\n")
    
    def deploy_adapter(self, adapter_name: str):
        """Deploy a specific adapter as current"""
        adapter_path = self.adapters_dir / adapter_name
        current_link = self.adapters_dir / "current"
        
        if not adapter_path.exists():
            print(f"❌ Adapter not found: {adapter_name}\n")
            sys.exit(1)
        
        # Backup old current
        if current_link.exists():
            backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_path = self.adapters_dir / backup_name
            os.rename(current_link, backup_path)
            print(f"📦 Backed up old adapter to: {backup_name}")
        
        # Create new symlink
        if current_link.exists():
            current_link.unlink()
        current_link.symlink_to(adapter_path)
        
        print(f"✅ Deployed adapter: {adapter_name}\n")
    
    def show_info(self):
        """Show current configuration"""
        print("\n📊 Zoe Model Configuration:\n")
        
        # Default model
        default_model = "llama3.2-1b"
        config_file = Path("/home/zoe/assistant/config/default_model.txt")
        if config_file.exists():
            default_model = config_file.read_text().strip()
        
        print(f"Default Model: {default_model}")
        
        # Current adapter
        current_link = self.adapters_dir / "current"
        if current_link.exists():
            current_adapter = current_link.resolve().name
            print(f"Active Adapter: {current_adapter}")
        else:
            print("Active Adapter: None (using base model)")
        
        print()


def main():
    """CLI entry point"""
    if len(sys.argv) < 2:
        print("""
Zoe Model Manager
=================

Usage:
  ./tools/model-manager.py list              List available Ollama models
  ./tools/model-manager.py list-adapters     List LoRA adapters
  ./tools/model-manager.py pull <model>      Download a model from Ollama
  ./tools/model-manager.py set-default <model>  Set default model
  ./tools/model-manager.py deploy-adapter <name>  Deploy specific adapter
  ./tools/model-manager.py info              Show current configuration

Examples:
  ./tools/model-manager.py pull llama3.2:3b
  ./tools/model-manager.py set-default gemma3:1b
  ./tools/model-manager.py deploy-adapter adapter_20251010
""")
        sys.exit(0)
    
    manager = ModelManager()
    command = sys.argv[1]
    
    if command == "list":
        manager.list_models()
    elif command == "list-adapters":
        manager.list_adapters()
    elif command == "pull":
        if len(sys.argv) < 3:
            print("❌ Error: Model name required")
            print("Usage: ./tools/model-manager.py pull <model_name>")
            sys.exit(1)
        manager.pull_model(sys.argv[2])
    elif command == "set-default":
        if len(sys.argv) < 3:
            print("❌ Error: Model name required")
            sys.exit(1)
        manager.set_default(sys.argv[2])
    elif command == "deploy-adapter":
        if len(sys.argv) < 3:
            print("❌ Error: Adapter name required")
            sys.exit(1)
        manager.deploy_adapter(sys.argv[2])
    elif command == "info":
        manager.show_info()
    else:
        print(f"❌ Unknown command: {command}")
        print("Run without arguments to see usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()












