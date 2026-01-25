#!/usr/bin/env python3
"""
Zoe Module Manager CLI
======================

Command-line interface for managing Zoe modules.

Commands:
  list      - List all available modules
  enable    - Enable a module
  disable   - Disable a module
  info      - Show module information
  validate  - Validate module structure

Examples:
  python tools/zoe_module.py list
  python tools/zoe_module.py enable music
  python tools/zoe_module.py disable developer
  python tools/zoe_module.py info music
"""

import click
import yaml
import json
from pathlib import Path
from typing import Dict, List, Optional
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class ModuleManager:
    """Manager for Zoe modules."""
    
    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self.modules_dir = self.project_root / "modules"
        self.config_file = self.project_root / "config" / "modules.yaml"
        self.compose_output = self.project_root / "docker-compose.modules.yml"
    
    def discover_modules(self) -> List[Dict]:
        """Discover all available modules."""
        if not self.modules_dir.exists():
            return []
        
        modules = []
        for module_path in self.modules_dir.iterdir():
            if not module_path.is_dir():
                continue
            
            # Check for module.yaml or module exists
            module_yaml = module_path / "module.yaml"
            docker_compose = module_path / "docker-compose.module.yml"
            
            if module_yaml.exists() or docker_compose.exists():
                module_info = {
                    "name": module_path.name,
                    "path": str(module_path),
                    "has_manifest": module_yaml.exists(),
                    "has_compose": docker_compose.exists()
                }
                
                # Load manifest if exists
                if module_yaml.exists():
                    try:
                        manifest = yaml.safe_load(module_yaml.read_text())
                        module_info["manifest"] = manifest
                        module_info["description"] = manifest.get("module", {}).get("description", "No description")
                        module_info["version"] = manifest.get("module", {}).get("version", "unknown")
                    except Exception as e:
                        module_info["description"] = f"Error reading manifest: {e}"
                else:
                    # Read from README if available
                    readme = module_path / "README.md"
                    if readme.exists():
                        lines = readme.read_text().split('\n')
                        # Try to extract description from first non-empty line after title
                        desc = "No description"
                        for line in lines[2:10]:  # Skip title, look in next lines
                            line = line.strip()
                            if line and not line.startswith('#') and not line.startswith('**'):
                                desc = line
                                break
                        module_info["description"] = desc
                    else:
                        module_info["description"] = "No description available"
                
                # Check if enabled
                module_info["enabled"] = self.is_module_enabled(module_path.name)
                
                modules.append(module_info)
        
        return sorted(modules, key=lambda x: x["name"])
    
    def is_module_enabled(self, module_name: str) -> bool:
        """Check if a module is enabled."""
        if not self.config_file.exists():
            return False
        
        try:
            config = yaml.safe_load(self.config_file.read_text())
            enabled_modules = config.get("enabled_modules", [])
            return module_name in enabled_modules
        except Exception:
            return False
    
    def get_enabled_modules(self) -> List[str]:
        """Get list of enabled module names."""
        if not self.config_file.exists():
            return []
        
        try:
            config = yaml.safe_load(self.config_file.read_text())
            return config.get("enabled_modules", [])
        except Exception:
            return []
    
    def enable_module(self, module_name: str) -> bool:
        """Enable a module."""
        # Ensure config directory exists
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load or create config
        if self.config_file.exists():
            config = yaml.safe_load(self.config_file.read_text())
        else:
            config = {"enabled_modules": [], "module_config": {}}
        
        # Add module if not already enabled
        if module_name not in config["enabled_modules"]:
            config["enabled_modules"].append(module_name)
            self.config_file.write_text(yaml.dump(config, default_flow_style=False))
            return True
        
        return False  # Already enabled
    
    def disable_module(self, module_name: str) -> bool:
        """Disable a module."""
        if not self.config_file.exists():
            return False
        
        config = yaml.safe_load(self.config_file.read_text())
        enabled_modules = config.get("enabled_modules", [])
        
        if module_name in enabled_modules:
            enabled_modules.remove(module_name)
            config["enabled_modules"] = enabled_modules
            self.config_file.write_text(yaml.dump(config, default_flow_style=False))
            return True
        
        return False  # Not enabled
    
    def get_module_info(self, module_name: str) -> Optional[Dict]:
        """Get detailed information about a module."""
        module_path = self.modules_dir / module_name
        if not module_path.exists():
            return None
        
        info = {
            "name": module_name,
            "path": str(module_path),
            "enabled": self.is_module_enabled(module_name)
        }
        
        # Read manifest
        module_yaml = module_path / "module.yaml"
        if module_yaml.exists():
            manifest = yaml.safe_load(module_yaml.read_text())
            info["manifest"] = manifest
        
        # Read compose file
        compose_file = module_path / "docker-compose.module.yml"
        if compose_file.exists():
            compose = yaml.safe_load(compose_file.read_text())
            info["services"] = list(compose.get("services", {}).keys())
            # Get first service for details
            first_service = list(compose.get("services", {}).values())[0] if compose.get("services") else {}
            info["port"] = first_service.get("ports", ["unknown"])[0] if first_service.get("ports") else "unknown"
            info["container"] = first_service.get("container_name", "unknown")
        
        # Count files
        py_files = list(module_path.rglob("*.py"))
        info["file_count"] = len(py_files)
        
        return info


# ============================================================
# CLI Commands
# ============================================================

@click.group()
def cli():
    """Zoe module management CLI."""
    pass


@cli.command()
@click.option('--detailed', '-d', is_flag=True, help='Show detailed information')
def list(detailed):
    """List all available modules."""
    manager = ModuleManager()
    modules = manager.discover_modules()
    
    if not modules:
        click.echo("No modules found in modules/ directory")
        return
    
    click.echo(f"\n📦 Available Zoe Modules ({len(modules)} total)\n")
    click.echo("=" * 80)
    
    for module in modules:
        status_icon = "✓" if module["enabled"] else "○"
        status_text = click.style("enabled", fg="green") if module["enabled"] else click.style("disabled", fg="red")
        
        click.echo(f"\n{status_icon} {click.style(module['name'], bold=True)} [{status_text}]")
        click.echo(f"   {module['description']}")
        
        if detailed:
            if module.get("version"):
                click.echo(f"   Version: {module['version']}")
            click.echo(f"   Path: {module['path']}")
            if module.get("has_compose"):
                click.echo("   ✓ Docker compose available")
            if module.get("has_manifest"):
                click.echo("   ✓ Module manifest available")
    
    click.echo("\n" + "=" * 80)
    enabled_count = sum(1 for m in modules if m["enabled"])
    click.echo(f"\n{enabled_count} enabled, {len(modules) - enabled_count} disabled\n")


@cli.command()
@click.argument('module_name')
def enable(module_name):
    """Enable a module."""
    manager = ModuleManager()
    
    # Check if module exists
    module_path = manager.modules_dir / module_name
    if not module_path.exists():
        click.echo(f"❌ Module '{module_name}' not found", err=True)
        click.echo(f"Available modules: {', '.join([m['name'] for m in manager.discover_modules()])}")
        sys.exit(1)
    
    # Enable the module
    if manager.enable_module(module_name):
        click.echo(f"✓ Enabled {module_name}")
        click.echo(f"\nNext steps:")
        click.echo(f"  1. Generate compose: python tools/generate_module_compose.py")
        click.echo(f"  2. Restart services: docker compose -f docker-compose.yml -f docker-compose.modules.yml up -d")
    else:
        click.echo(f"ℹ  {module_name} is already enabled")


@cli.command()
@click.argument('module_name')
def disable(module_name):
    """Disable a module."""
    manager = ModuleManager()
    
    if manager.disable_module(module_name):
        click.echo(f"✓ Disabled {module_name}")
        click.echo(f"\nNext steps:")
        click.echo(f"  1. Generate compose: python tools/generate_module_compose.py")
        click.echo(f"  2. Restart services: docker compose -f docker-compose.yml -f docker-compose.modules.yml up -d")
    else:
        click.echo(f"ℹ  {module_name} is not enabled")


@cli.command()
@click.argument('module_name')
def info(module_name):
    """Show detailed information about a module."""
    manager = ModuleManager()
    info = manager.get_module_info(module_name)
    
    if not info:
        click.echo(f"❌ Module '{module_name}' not found", err=True)
        sys.exit(1)
    
    click.echo(f"\n{'=' * 80}")
    click.echo(f"{click.style(info['name'], bold=True)}")
    click.echo(f"{'=' * 80}\n")
    
    status_text = click.style("ENABLED", fg="green") if info["enabled"] else click.style("DISABLED", fg="red")
    click.echo(f"Status: {status_text}")
    click.echo(f"Path: {info['path']}")
    click.echo(f"Files: {info['file_count']} Python files")
    
    if info.get("container"):
        click.echo(f"\nDocker:")
        click.echo(f"  Container: {info['container']}")
        click.echo(f"  Port: {info.get('port', 'unknown')}")
    
    if info.get("manifest"):
        manifest = info["manifest"]
        module_data = manifest.get("module", {})
        click.echo(f"\nManifest:")
        click.echo(f"  Description: {module_data.get('description', 'N/A')}")
        click.echo(f"  Version: {module_data.get('version', 'N/A')}")
        click.echo(f"  Author: {module_data.get('author', 'N/A')}")
        
        # Show capabilities if defined
        if "capabilities" in module_data:
            click.echo(f"\nCapabilities:")
            for cap in module_data["capabilities"]:
                click.echo(f"  - {cap.get('capability_type', 'unknown')}")
    
    click.echo(f"\n{'=' * 80}\n")


@cli.command()
def status():
    """Show overall module system status."""
    manager = ModuleManager()
    modules = manager.discover_modules()
    
    click.echo(f"\n📊 Zoe Module System Status\n")
    click.echo(f"Modules directory: {manager.modules_dir}")
    click.echo(f"Config file: {manager.config_file}")
    click.echo(f"Generated compose: {manager.compose_output}")
    
    enabled = [m for m in modules if m["enabled"]]
    disabled = [m for m in modules if not m["enabled"]]
    
    click.echo(f"\n✓ Enabled modules ({len(enabled)}):")
    for m in enabled:
        click.echo(f"  - {m['name']}")
    
    click.echo(f"\n○ Disabled modules ({len(disabled)}):")
    for m in disabled:
        click.echo(f"  - {m['name']}")
    
    # Check if containers are running
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True
        )
        running_containers = result.stdout.split('\n')
        
        click.echo(f"\n🐳 Running containers:")
        for m in enabled:
            container_name = m['name'].replace('_', '-')
            is_running = any(container_name in c for c in running_containers)
            status = click.style("running", fg="green") if is_running else click.style("stopped", fg="yellow")
            click.echo(f"  - {m['name']}: {status}")
    except Exception as e:
        click.echo(f"\n⚠️  Could not check container status: {e}")
    
    click.echo()


if __name__ == '__main__':
    cli()
