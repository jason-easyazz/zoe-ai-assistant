#!/usr/bin/env python3
"""
Light RAG Performance Benchmarks
Comprehensive performance testing for Light RAG system
"""
import sys
import os
import time
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import tempfile
import random
import string

# Add the services directory to the path
sys.path.append(str(PROJECT_ROOT / "services/zoe-core"))

from light_rag_memory import LightRAGMemorySystem

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(PROJECT_ROOT / "logs/light_rag_benchmarks.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class LightRAGBenchmarks:
    """Comprehensive benchmark suite for Light RAG system"""
    
    def __init__(self, db_path=str(PROJECT_ROOT / "data" / "memory.db")):
        self.db_path = db_path
        self.results = {}
        self.light_rag_system = None
        
    def generate_test_data(self, num_people=100, num_projects=50, num_memories=500):
        """Generate test data for benchmarking"""
        logger.info(f"Generating test data: {num_people} people, {num_projects} projects, {num_memories} memories")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing test data
        cursor.execute("DELETE FROM memory_facts WHERE source = 'benchmark_test'")
        cursor.execute("DELETE FROM people WHERE name LIKE 'TestPerson_%'")
        cursor.execute("DELETE FROM projects WHERE name LIKE 'TestProject_%'")
        
        # Generate test people
        for i in range(num_people):
            name = f"TestPerson_{i}"
            relationship = random.choice(['friend', 'colleague', 'family', 'acquaintance'])
            profile = json.dumps({"relationship": relationship})
            cursor.execute("""
                INSERT INTO people (name, profile, facts)
                VALUES (?, ?, ?)
            """, (name, profile, json.dumps([f"Test person {i} for benchmarking"])))
        
        # Generate test projects
        for i in range(num_projects):
            name = f"TestProject_{i}"
            description = f"Test project {i} for benchmarking"
            status = random.choice(['active', 'completed', 'planned'])
            cursor.execute("""
                INSERT INTO projects (name, description, status)
                VALUES (?, ?, ?)
            """, (name, description, status))
        
        # Generate test memories
        for i in range(num_memories):
            entity_type = random.choice(['person', 'project', 'general'])
            if entity_type == 'person':
                entity_id = random.randint(1, num_people)
            elif entity_type == 'project':
                entity_id = random.randint(1, num_projects)
            else:
                entity_id = 0
            
            fact = f"Test memory {i}: {self._generate_random_text()}"
            category = random.choice(['general', 'interests', 'projects', 'important_dates'])
            importance = random.randint(1, 10)
            
            cursor.execute("""
                INSERT INTO memory_facts (entity_type, entity_id, fact, category, importance, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (entity_type, entity_id, fact, category, importance, 'benchmark_test'))
        
        conn.commit()
        conn.close()
        
        logger.info("Test data generation completed")
    
    def _generate_random_text(self, length=50):
        """Generate random text for testing"""
        words = ['Arduino', 'Python', 'garden', 'automation', 'electronics', 'project', 
                'workshop', 'learning', 'friend', 'family', 'colleague', 'meeting',
                'birthday', 'anniversary', 'vacation', 'work', 'hobby', 'interest']
        return ' '.join(random.choices(words, k=random.randint(3, 8)))
    
    def benchmark_initialization(self):
        """Benchmark system initialization"""
        logger.info("Benchmarking system initialization...")
        
        start_time = time.time()
        self.light_rag_system = LightRAGMemorySystem(self.db_path)
        init_time = time.time() - start_time
        
        self.results['initialization'] = {
            'time_seconds': init_time,
            'status': 'success'
        }
        
        logger.info(f"Initialization completed in {init_time:.3f} seconds")
    
    def benchmark_embedding_generation(self, num_embeddings=100):
        """Benchmark embedding generation performance"""
        logger.info(f"Benchmarking embedding generation for {num_embeddings} texts...")
        
        test_texts = [self._generate_random_text() for _ in range(num_embeddings)]
        
        start_time = time.time()
        embeddings = []
        for text in test_texts:
            embedding = self.light_rag_system.generate_embedding(text)
            embeddings.append(embedding)
        total_time = time.time() - start_time
        
        avg_time_per_embedding = total_time / num_embeddings
        
        self.results['embedding_generation'] = {
            'total_time_seconds': total_time,
            'avg_time_per_embedding': avg_time_per_embedding,
            'embeddings_per_second': num_embeddings / total_time,
            'num_embeddings': num_embeddings,
            'status': 'success'
        }
        
        logger.info(f"Generated {num_embeddings} embeddings in {total_time:.3f} seconds")
        logger.info(f"Average time per embedding: {avg_time_per_embedding:.3f} seconds")
        logger.info(f"Embeddings per second: {num_embeddings / total_time:.1f}")
    
    def benchmark_migration(self):
        """Benchmark migration performance"""
        logger.info("Benchmarking migration performance...")
        
        start_time = time.time()
        migration_result = self.light_rag_system.migrate_existing_memories()
        migration_time = time.time() - start_time
        
        self.results['migration'] = {
            'time_seconds': migration_time,
            'migrated_count': migration_result['migrated_count'],
            'error_count': migration_result['error_count'],
            'memories_per_second': migration_result['migrated_count'] / migration_time if migration_time > 0 else 0,
            'status': 'success'
        }
        
        logger.info(f"Migration completed in {migration_time:.3f} seconds")
        logger.info(f"Migrated {migration_result['migrated_count']} memories")
        logger.info(f"Memories per second: {migration_result['migrated_count'] / migration_time:.1f}")
    
    def benchmark_search_performance(self, num_searches=50):
        """Benchmark search performance"""
        logger.info(f"Benchmarking search performance with {num_searches} queries...")
        
        test_queries = [
            "Arduino projects", "Python programming", "garden automation",
            "electronics workshop", "friend birthday", "work meeting",
            "family vacation", "hobby interests", "learning new skills",
            "project collaboration"
        ]
        
        search_times = []
        result_counts = []
        
        for i in range(num_searches):
            query = random.choice(test_queries) + f" {random.randint(1, 100)}"
            
            start_time = time.time()
            results = self.light_rag_system.light_rag_search(query, limit=10, use_cache=False)
            search_time = time.time() - start_time
            
            search_times.append(search_time)
            result_counts.append(len(results))
        
        avg_search_time = sum(search_times) / len(search_times)
        min_search_time = min(search_times)
        max_search_time = max(search_times)
        avg_results = sum(result_counts) / len(result_counts)
        
        self.results['search_performance'] = {
            'num_searches': num_searches,
            'avg_search_time': avg_search_time,
            'min_search_time': min_search_time,
            'max_search_time': max_search_time,
            'avg_results_per_search': avg_results,
            'searches_per_second': num_searches / sum(search_times),
            'status': 'success'
        }
        
        logger.info(f"Average search time: {avg_search_time:.3f} seconds")
        logger.info(f"Search time range: {min_search_time:.3f} - {max_search_time:.3f} seconds")
        logger.info(f"Searches per second: {num_searches / sum(search_times):.1f}")
    
    def benchmark_caching_performance(self, num_searches=20):
        """Benchmark caching performance"""
        logger.info(f"Benchmarking caching performance with {num_searches} repeated queries...")
        
        test_query = "Arduino projects with friends"
        
        # First search (no cache)
        start_time = time.time()
        results1 = self.light_rag_system.light_rag_search(test_query, use_cache=True)
        first_search_time = time.time() - start_time
        
        # Repeated searches (with cache)
        cached_times = []
        for i in range(num_searches):
            start_time = time.time()
            results = self.light_rag_system.light_rag_search(test_query, use_cache=True)
            cached_time = time.time() - start_time
            cached_times.append(cached_time)
        
        avg_cached_time = sum(cached_times) / len(cached_times)
        cache_speedup = first_search_time / avg_cached_time if avg_cached_time > 0 else 0
        
        self.results['caching_performance'] = {
            'first_search_time': first_search_time,
            'avg_cached_time': avg_cached_time,
            'cache_speedup': cache_speedup,
            'num_cached_searches': num_searches,
            'status': 'success'
        }
        
        logger.info(f"First search time: {first_search_time:.3f} seconds")
        logger.info(f"Average cached search time: {avg_cached_time:.3f} seconds")
        logger.info(f"Cache speedup: {cache_speedup:.1f}x")
    
    def benchmark_memory_usage(self):
        """Benchmark memory usage"""
        logger.info("Benchmarking memory usage...")
        
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss
        
        # Perform various operations
        self.light_rag_system.migrate_existing_memories()
        results = self.light_rag_system.light_rag_search("test query", limit=50)
        stats = self.light_rag_system.get_system_stats()
        
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory
        
        self.results['memory_usage'] = {
            'initial_memory_mb': initial_memory / (1024 * 1024),
            'final_memory_mb': final_memory / (1024 * 1024),
            'memory_increase_mb': memory_increase / (1024 * 1024),
            'status': 'success'
        }
        
        logger.info(f"Memory usage: {memory_increase / (1024 * 1024):.1f} MB increase")
    
    def benchmark_concurrent_access(self, num_threads=5, searches_per_thread=10):
        """Benchmark concurrent access performance"""
        logger.info(f"Benchmarking concurrent access with {num_threads} threads...")
        
        import threading
        import queue
        
        results_queue = queue.Queue()
        errors_queue = queue.Queue()
        
        def search_worker():
            try:
                start_time = time.time()
                for _ in range(searches_per_thread):
                    query = f"test query {random.randint(1, 100)}"
                    results = self.light_rag_system.light_rag_search(query, limit=5)
                total_time = time.time() - start_time
                results_queue.put(total_time)
            except Exception as e:
                errors_queue.put(e)
        
        # Start threads
        threads = []
        start_time = time.time()
        
        for _ in range(num_threads):
            thread = threading.Thread(target=search_worker)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        total_time = time.time() - start_time
        
        # Collect results
        thread_times = []
        while not results_queue.empty():
            thread_times.append(results_queue.get())
        
        errors = []
        while not errors_queue.empty():
            errors.append(errors_queue.get())
        
        self.results['concurrent_access'] = {
            'num_threads': num_threads,
            'searches_per_thread': searches_per_thread,
            'total_time': total_time,
            'avg_thread_time': sum(thread_times) / len(thread_times) if thread_times else 0,
            'errors': len(errors),
            'status': 'success' if not errors else 'partial_success'
        }
        
        logger.info(f"Concurrent access completed in {total_time:.3f} seconds")
        logger.info(f"Errors: {len(errors)}")
    
    def run_all_benchmarks(self):
        """Run all benchmarks"""
        logger.info("=" * 60)
        logger.info("STARTING LIGHT RAG BENCHMARKS")
        logger.info("=" * 60)
        
        try:
            # Generate test data
            self.generate_test_data()
            
            # Run benchmarks
            self.benchmark_initialization()
            self.benchmark_embedding_generation()
            self.benchmark_migration()
            self.benchmark_search_performance()
            self.benchmark_caching_performance()
            self.benchmark_memory_usage()
            self.benchmark_concurrent_access()
            
            # Save results
            self.save_results()
            
            logger.info("=" * 60)
            logger.info("BENCHMARKS COMPLETED SUCCESSFULLY")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Benchmark failed: {e}")
            raise
    
    def save_results(self):
        """Save benchmark results to file"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_file = fstr(PROJECT_ROOT / "logs/light_rag_benchmarks_{timestamp}.json")
        
        with open(results_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        logger.info(f"Benchmark results saved to: {results_file}")
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print benchmark summary"""
        logger.info("\n" + "=" * 60)
        logger.info("BENCHMARK SUMMARY")
        logger.info("=" * 60)
        
        for benchmark_name, results in self.results.items():
            logger.info(f"\n{benchmark_name.upper()}:")
            for key, value in results.items():
                if key != 'status':
                    logger.info(f"  {key}: {value}")

def main():
    """Main benchmark function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Light RAG Performance Benchmarks')
    parser.add_argument('--db-path', default=str(PROJECT_ROOT / "data" / "memory.db"),
                       help='Path to memory database')
    parser.add_argument('--quick', action='store_true',
                       help='Run quick benchmarks with smaller datasets')
    
    args = parser.parse_args()
    
    # Create benchmarks instance
    benchmarks = LightRAGBenchmarks(args.db_path)
    
    # Adjust test data size for quick mode
    if args.quick:
        benchmarks.generate_test_data(num_people=20, num_projects=10, num_memories=50)
    
    # Run benchmarks
    benchmarks.run_all_benchmarks()

if __name__ == "__main__":
    main()
