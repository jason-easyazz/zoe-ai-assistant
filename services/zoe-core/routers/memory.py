from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
import sys
sys.path.append('/app')
from memory_system import MemorySystem

router = APIRouter(prefix="/api/memory", tags=["memory"])
memory = MemorySystem()

class PersonCreate(BaseModel):
    name: str
    facts: Optional[List[str]] = []

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = ""

class RelationshipCreate(BaseModel):
    person1: str
    person2: str
    relationship: str

class MemorySearch(BaseModel):
    query: str

@router.post("/person")
async def create_person(person: PersonCreate):
    """Add a new person to memory"""
    try:
        result = memory.add_person(person.name, person.facts)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/person/{name}")
async def get_person(name: str):
    """Get all information about a person"""
    context = memory.get_person_context(name)
    if not context["found"]:
        raise HTTPException(status_code=404, detail="Person not found")
    return context

@router.post("/project")
async def create_project(project: ProjectCreate):
    """Add a new project to memory"""
    try:
        result = memory.add_project(project.name, project.description)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/relationship")
async def create_relationship(rel: RelationshipCreate):
    """Create relationship between people"""
    result = memory.add_relationship(rel.person1, rel.person2, rel.relationship)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@router.post("/search")
async def search_memories(search: MemorySearch):
    """Search all memories"""
    return memory.search_memories(search.query)
