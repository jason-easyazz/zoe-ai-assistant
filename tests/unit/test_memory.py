"""Unit tests for Memory System"""
import unittest
import sys
import os
sys.path.append('/home/pi/zoe/services/zoe-core')
from memory_system import MemorySystem

class TestMemorySystem(unittest.TestCase):
    def setUp(self):
        self.memory = MemorySystem(db_path=":memory:")
    
    def test_add_person(self):
        result = self.memory.add_person("John Doe", ["Works at Google", "Likes coffee"])
        self.assertIsNotNone(result["id"])
        self.assertEqual(result["name"], "John Doe")
        self.assertEqual(result["facts_added"], 2)
    
    def test_add_project(self):
        result = self.memory.add_project("Zoe Development", "AI Assistant project")
        self.assertIsNotNone(result["id"])
        self.assertEqual(result["name"], "Zoe Development")
    
    def test_add_relationship(self):
        self.memory.add_person("Alice")
        self.memory.add_person("Bob")
        result = self.memory.add_relationship("Alice", "Bob", "colleague")
        self.assertTrue(result["success"])
    
    def test_search_memories(self):
        self.memory.add_person("Jane", ["Loves Python programming"])
        results = self.memory.search_memories("Python")
        self.assertGreater(len(results), 0)
        self.assertIn("Python", results[0]["fact"])

if __name__ == "__main__":
    unittest.main()
