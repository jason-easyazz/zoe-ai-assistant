#!/usr/bin/env python3
"""
Module Validator
================

Validates module structure and safety before enabling.

Usage:
  python tools/validate_module.py zoe-music
  python tools/validate_module.py --all
"""

import click
import sys
import yaml
from pathlib import Path
from typing import Dict, List, Tuple


class ModuleValidator:
    """Validates module structure and safety."""
    
    def __init__(self, modules_dir: Path = None):
        self.modules_dir = modules_dir or Path("modules")
        self.errors = []
        self.warnings = []
        self.checks_passed = 0
        self.checks_failed = 0
    
    def validate_module(self, module_name: str) -> bool:
        """Validate a module. Returns True if valid."""
        self.errors = []
        self.warnings = []
        self.checks_passed = 0
        self.checks_failed = 0
        
        module_path = self.modules_dir / module_name
        
        if not module_path.exists():
            self.errors.append(f"Module directory not found: {module_path}")
            return False
        
        click.echo(f"\n🔍 Validating module: {module_name}")
        click.echo("=" * 60)
        
        # Run all validation checks
        self._check_required_files(module_path)
        self._check_dockerfile(module_path)
        self._check_docker_compose(module_path, module_name)
        self._check_main_py(module_path)
        self._check_requirements_txt(module_path)
        self._check_readme(module_path)
        self._check_intents(module_path)
        self._check_naming_conventions(module_name)
        self._check_security(module_path)
        
        # Print results
        click.echo("\n" + "=" * 60)
        
        if self.errors:
            click.echo(click.style(f"\n❌ VALIDATION FAILED", fg="red", bold=True))
            click.echo(f"\n{self.checks_failed} checks failed, {self.checks_passed} passed\n")
            
            click.echo("Errors:")
            for error in self.errors:
                click.echo(click.style(f"  ✗ {error}", fg="red"))
        else:
            click.echo(click.style(f"\n✅ VALIDATION PASSED", fg="green", bold=True))
            click.echo(f"\n{self.checks_passed} checks passed")
        
        if self.warnings:
            click.echo("\nWarnings:")
            for warning in self.warnings:
                click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
        
        click.echo()
        return len(self.errors) == 0
    
    def _pass_check(self, message: str):
        """Mark check as passed."""
        click.echo(click.style(f"  ✓ {message}", fg="green"))
        self.checks_passed += 1
    
    def _fail_check(self, message: str):
        """Mark check as failed."""
        click.echo(click.style(f"  ✗ {message}", fg="red"))
        self.errors.append(message)
        self.checks_failed += 1
    
    def _warn(self, message: str):
        """Add warning."""
        click.echo(click.style(f"  ⚠ {message}", fg="yellow"))
        self.warnings.append(message)
    
    def _check_required_files(self, module_path: Path):
        """Check for required files."""
        click.echo("\n📁 Required Files:")
        
        required = {
            "main.py": "FastAPI application",
            "Dockerfile": "Container configuration",
            "requirements.txt": "Python dependencies",
            "docker-compose.module.yml": "Service configuration",
            "README.md": "Documentation"
        }
        
        for filename, description in required.items():
            if (module_path / filename).exists():
                self._pass_check(f"{filename} - {description}")
            else:
                self._fail_check(f"Missing {filename} - {description}")
    
    def _check_dockerfile(self, module_path: Path):
        """Check Dockerfile."""
        click.echo("\n🐳 Dockerfile:")
        
        dockerfile = module_path / "Dockerfile"
        if not dockerfile.exists():
            return
        
        content = dockerfile.read_text()
        
        # Check for security issues
        if "sudo" in content.lower():
            self._warn("Dockerfile contains 'sudo' - may not be necessary")
        
        if "curl" in content or "wget" in content:
            self._pass_check("Has curl/wget for healthchecks")
        
        if "HEALTHCHECK" in content:
            self._pass_check("Defines healthcheck")
        else:
            self._warn("No HEALTHCHECK defined in Dockerfile")
    
    def _check_docker_compose(self, module_path: Path, module_name: str):
        """Check docker-compose.module.yml."""
        click.echo("\n🐋 Docker Compose:")
        
        compose_file = module_path / "docker-compose.module.yml"
        if not compose_file.exists():
            return
        
        try:
            compose = yaml.safe_load(compose_file.read_text())
            
            # Check services
            services = compose.get("services", {})
            if not services:
                self._fail_check("No services defined")
                return
            
            service_name = list(services.keys())[0]
            service = services[service_name]
            
            # Check container name matches
            container_name = service.get("container_name", "")
            if container_name == module_name or container_name == module_name.replace("_", "-"):
                self._pass_check(f"Container name matches: {container_name}")
            else:
                self._warn(f"Container name '{container_name}' doesn't match module '{module_name}'")
            
            # Check for zoe-network
            networks = service.get("networks", [])
            if "zoe-network" in networks:
                self._pass_check("On zoe-network")
            else:
                self._fail_check("NOT on zoe-network - module will be isolated!")
            
            # Check for network definition
            if "networks" in compose:
                net_def = compose["networks"].get("zoe-network", {})
                if net_def.get("name") == "zoe-network":
                    self._pass_check("Network properly defined with name")
                else:
                    self._fail_check("Network should have 'name: zoe-network'")
            
            # Check healthcheck
            if "healthcheck" in service:
                self._pass_check("Has healthcheck defined")
            else:
                self._warn("No healthcheck in docker-compose")
            
            # Check restart policy
            restart = service.get("restart", "")
            if restart in ["unless-stopped", "always"]:
                self._pass_check(f"Restart policy: {restart}")
            else:
                self._warn(f"Restart policy '{restart}' - consider 'unless-stopped'")
            
        except Exception as e:
            self._fail_check(f"Invalid YAML: {e}")
    
    def _check_main_py(self, module_path: Path):
        """Check main.py structure."""
        click.echo("\n🐍 main.py:")
        
        main_file = module_path / "main.py"
        if not main_file.exists():
            return
        
        content = main_file.read_text()
        
        # Check for FastAPI
        if "from fastapi import FastAPI" in content or "import fastapi" in content:
            self._pass_check("Uses FastAPI")
        else:
            self._fail_check("No FastAPI import found")
        
        # Check for health endpoint
        if '@app.get("/health")' in content or '@app.get(\'/health\')' in content:
            self._pass_check("Has /health endpoint")
        else:
            self._warn("No /health endpoint - add for monitoring")
        
        # Check for MCP tool endpoints
        if "/tools/" in content:
            self._pass_check("Has MCP tool endpoints")
        else:
            self._warn("No /tools/ endpoints - module may not be MCP-compatible")
        
        # Security checks
        if "os.system" in content or "subprocess.run" in content:
            self._warn("Uses os.system/subprocess - review for security")
        
        if "eval(" in content or "exec(" in content:
            self._fail_check("SECURITY: Uses eval/exec - NOT ALLOWED")
    
    def _check_requirements_txt(self, module_path: Path):
        """Check requirements.txt."""
        click.echo("\n📦 requirements.txt:")
        
        req_file = module_path / "requirements.txt"
        if not req_file.exists():
            return
        
        content = req_file.read_text()
        lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
        
        if lines:
            self._pass_check(f"Defines {len(lines)} dependencies")
        else:
            self._warn("Empty requirements.txt - is this intentional?")
        
        # Check for common packages
        if any('fastapi' in l.lower() for l in lines):
            self._pass_check("Includes FastAPI")
        
        if any('pydantic' in l.lower() for l in lines):
            self._pass_check("Includes Pydantic")
    
    def _check_readme(self, module_path: Path):
        """Check README.md."""
        click.echo("\n📖 README.md:")
        
        readme = module_path / "README.md"
        if not readme.exists():
            return
        
        content = readme.read_text()
        
        if len(content) > 500:
            self._pass_check("Has comprehensive documentation")
        elif len(content) > 100:
            self._pass_check("Has basic documentation")
        else:
            self._warn("README is very short - add more details")
        
        # Check for required sections
        sections = ["features", "installation", "usage", "tools"]
        found_sections = [s for s in sections if s.lower() in content.lower()]
        
        if len(found_sections) >= 3:
            self._pass_check(f"Has good structure ({len(found_sections)}/4 sections)")
        elif found_sections:
            self._warn(f"Missing some sections ({len(found_sections)}/4)")
    
    def _check_intents(self, module_path: Path):
        """Check intents (optional)."""
        click.echo("\n🎯 Intents (Optional):")
        
        intents_dir = module_path / "intents"
        if not intents_dir.exists():
            click.echo("  ℹ  No intents directory - module only provides MCP tools")
            return
        
        # Check for YAML files
        yaml_files = list(intents_dir.glob("*.yaml"))
        if yaml_files:
            self._pass_check(f"Has {len(yaml_files)} intent definition file(s)")
        else:
            self._warn("intents/ directory exists but no .yaml files")
        
        # Check for handlers.py
        handlers = intents_dir / "handlers.py"
        if handlers.exists():
            content = handlers.read_text()
            
            if "INTENT_HANDLERS" in content:
                self._pass_check("Has INTENT_HANDLERS mapping")
            else:
                self._fail_check("Missing INTENT_HANDLERS dict in handlers.py")
            
            if "async def" in content:
                self._pass_check("Uses async handlers")
        else:
            self._warn("intents/ directory but no handlers.py")
    
    def _check_naming_conventions(self, module_name: str):
        """Check naming conventions."""
        click.echo("\n📝 Naming:")
        
        # Check module name format
        if module_name.startswith("zoe-"):
            self._pass_check("Module name starts with 'zoe-'")
        else:
            self._warn("Module name should start with 'zoe-' for consistency")
        
        # Check for -mcp-bridge suffix
        if "-mcp-bridge" in module_name:
            click.echo("  ℹ  This is an external service bridge (has -mcp-bridge)")
        elif module_name.count("-") >= 1:
            self._pass_check("Uses kebab-case naming")
        else:
            self._warn("Consider using kebab-case (e.g., zoe-my-feature)")
    
    def _check_security(self, module_path: Path):
        """Security checks."""
        click.echo("\n🔒 Security:")
        
        # Check for .env or secrets
        if (module_path / ".env").exists():
            self._fail_check(".env file found - should not be in repo!")
        else:
            self._pass_check("No .env file in repo")
        
        # Check for private keys
        private_files = list(module_path.rglob("*.pem")) + list(module_path.rglob("*.key"))
        if private_files:
            self._fail_check(f"Private key files found: {[f.name for f in private_files]}")
        else:
            self._pass_check("No private keys in repo")
        
        # Check gitignore
        gitignore = module_path / ".gitignore"
        if gitignore.exists():
            self._pass_check("Has .gitignore")
        else:
            self._warn("No .gitignore - add to exclude sensitive files")


@click.command()
@click.argument('module_name', required=False)
@click.option('--all', is_flag=True, help='Validate all modules')
def main(module_name, all):
    """Validate module structure and safety."""
    validator = ModuleValidator()
    
    if all:
        # Validate all modules
        modules_dir = Path("modules")
        if not modules_dir.exists():
            click.echo("No modules/ directory found")
            sys.exit(1)
        
        modules = [d.name for d in modules_dir.iterdir() if d.is_dir()]
        
        if not modules:
            click.echo("No modules found")
            sys.exit(0)
        
        click.echo(f"Validating {len(modules)} modules...\n")
        
        results = {}
        for mod in modules:
            results[mod] = validator.validate_module(mod)
        
        # Summary
        click.echo("\n" + "=" * 60)
        click.echo("SUMMARY")
        click.echo("=" * 60)
        
        passed = sum(1 for v in results.values() if v)
        failed = len(results) - passed
        
        for mod, result in results.items():
            status = click.style("✅ PASS", fg="green") if result else click.style("❌ FAIL", fg="red")
            click.echo(f"  {mod}: {status}")
        
        click.echo(f"\n{passed} passed, {failed} failed\n")
        
        sys.exit(0 if failed == 0 else 1)
    
    elif module_name:
        # Validate single module
        success = validator.validate_module(module_name)
        sys.exit(0 if success else 1)
    
    else:
        click.echo("Usage: validate_module.py MODULE_NAME")
        click.echo("       validate_module.py --all")
        sys.exit(1)


if __name__ == '__main__':
    main()
