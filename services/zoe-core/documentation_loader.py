"""Documentation Loader for Zack - Portable Version"""
from pathlib import Path
from typing import Dict

class DocumentationLoader:
    def __init__(self, base_path="/app/documentation/core"):
        self.base_path = Path(base_path)
        self.documents = {}
        self.load_all_documents()
    
    def load_all_documents(self):
        """Load all documentation files"""
        for file in self.base_path.glob("*.md"):
            try:
                with open(file, 'r') as f:
                    # Remove any hardcoded IPs while loading
                    content = f.read()
                    # Replace common hardcoded IPs with placeholders
                    content = content.replace("192.168.1.60", "[your-pi-ip]")
                    content = content.replace("192.168.1.", "[your-network].")
                    self.documents[file.name] = content
            except:
                pass
    
    def get_context_for_zack(self, query: str) -> str:
        """Get portable context for Zack"""
        context = []
        if "zack-master-prompt.md" in self.documents:
            context.append("=== ZACK INSTRUCTIONS (PORTABLE) ===\n")
            context.append(self.documents["zack-master-prompt.md"][:1500])
        return "\n".join(context)

zack_doc_loader = DocumentationLoader()
