"""
Zoe Knowledge Graph Engine
Hybrid SQLite + NetworkX for lightweight graph operations
"""
import networkx as nx
import sqlite3
import json
import pickle
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ZoeGraphEngine:
    """
    Hybrid graph engine combining SQLite persistence with NetworkX algorithms
    Optimized for Raspberry Pi 5 with low memory footprint
    """
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self.graph = nx.MultiDiGraph()  # Directed graph with multiple edges
        self.cache_path = "/app/data/graph_cache.pkl"
        
        # Initialize graph structure in SQLite
        self._init_db()
        
        # Load graph from SQLite
        self._load_from_db()
        
        logger.info(f"✅ Graph engine initialized: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")
    
    def _init_db(self):
        """Create graph tables in SQLite if they don't exist"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Unified nodes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type TEXT NOT NULL,
                node_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Edges table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_node TEXT NOT NULL,
                target_node TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_node) REFERENCES knowledge_nodes(node_id),
                FOREIGN KEY (target_node) REFERENCES knowledge_nodes(node_id)
            )
        """)
        
        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON knowledge_nodes(node_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_id ON knowledge_nodes(node_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON knowledge_edges(source_node)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON knowledge_edges(target_node)")
        
        conn.commit()
        conn.close()
    
    def _load_from_db(self):
        """Load graph structure from SQLite into NetworkX"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Load nodes from existing people table
            cursor.execute("SELECT id, name FROM people")
            for person_id, name in cursor.fetchall():
                node_id = f"person_{person_id}"
                self.graph.add_node(
                    node_id,
                    name=name,
                    type="person",
                    db_id=person_id
                )
            
            # Load nodes from projects table
            cursor.execute("SELECT id, name, description, status FROM projects")
            for project_id, name, description, status in cursor.fetchall():
                node_id = f"project_{project_id}"
                self.graph.add_node(
                    node_id,
                    name=name,
                    type="project",
                    description=description,
                    status=status,
                    db_id=project_id
                )
            
            # Load relationships from existing relationships table
            cursor.execute("""
                SELECT person1_id, person2_id, relationship_type 
                FROM relationships
            """)
            for p1, p2, rel_type in cursor.fetchall():
                self.graph.add_edge(
                    f"person_{p1}",
                    f"person_{p2}",
                    relationship=rel_type,
                    weight=1.0
                )
            
            # Load nodes from new knowledge_nodes table if exists
            try:
                cursor.execute("SELECT node_id, name, node_type, data_json FROM knowledge_nodes")
                for node_id, name, node_type, data_json in cursor.fetchall():
                    data = json.loads(data_json) if data_json else {}
                    self.graph.add_node(
                        node_id,
                        name=name,
                        type=node_type,
                        **data
                    )
            except sqlite3.OperationalError:
                # Table doesn't exist yet
                pass
            
            # Load edges from knowledge_edges table if exists
            try:
                cursor.execute("""
                    SELECT source_node, target_node, relationship_type, weight, metadata_json
                    FROM knowledge_edges
                """)
                for source, target, rel_type, weight, metadata_json in cursor.fetchall():
                    metadata = json.loads(metadata_json) if metadata_json else {}
                    self.graph.add_edge(
                        source,
                        target,
                        relationship=rel_type,
                        weight=weight,
                        **metadata
                    )
            except sqlite3.OperationalError:
                # Table doesn't exist yet
                pass
            
        finally:
            conn.close()
    
    def add_node(self, node_type: str, name: str, data: Dict = None) -> str:
        """Add node to graph (both SQLite and NetworkX)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            data = data or {}
            node_id = f"{node_type}_{name}_{datetime.now().timestamp()}"
            
            cursor.execute("""
                INSERT INTO knowledge_nodes (node_type, node_id, name, data_json, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (node_type, node_id, name, json.dumps(data)))
            
            conn.commit()
            
            # Add to in-memory graph
            self.graph.add_node(node_id, type=node_type, name=name, **data)
            
            logger.info(f"✅ Added node: {node_id}")
            return node_id
            
        finally:
            conn.close()
    
    def add_relationship(self, source: str, target: str, rel_type: str, weight: float = 1.0, metadata: Dict = None):
        """Create relationship between nodes"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            metadata = metadata or {}
            
            cursor.execute("""
                INSERT INTO knowledge_edges (source_node, target_node, relationship_type, weight, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (source, target, rel_type, weight, json.dumps(metadata)))
            
            conn.commit()
            
            # Add to in-memory graph
            self.graph.add_edge(source, target, relationship=rel_type, weight=weight, **metadata)
            
            logger.info(f"✅ Added edge: {source} --[{rel_type}]--> {target}")
            
        finally:
            conn.close()
    
    def find_path(self, node1: str, node2: str) -> Optional[List[str]]:
        """Find shortest path between two nodes"""
        try:
            path = nx.shortest_path(self.graph, node1, node2)
            return path
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            return None
    
    def search_by_proximity(self, start_node: str, max_depth: int = 2) -> List[str]:
        """Find all nodes within N hops of start node"""
        try:
            # BFS traversal up to max_depth
            nearby = nx.single_source_shortest_path_length(
                self.graph,
                start_node,
                cutoff=max_depth
            )
            return list(nearby.keys())
        except nx.NodeNotFound:
            return []
    
    def get_common_connections(self, node1: str, node2: str) -> List[str]:
        """Find nodes connected to both"""
        try:
            neighbors1 = set(self.graph.neighbors(node1))
            neighbors2 = set(self.graph.neighbors(node2))
            common = neighbors1.intersection(neighbors2)
            return list(common)
        except nx.NodeNotFound:
            return []
    
    def centrality_ranking(self, node_type: str = None, limit: int = 10) -> List[Tuple[str, float]]:
        """Find most important nodes by PageRank"""
        try:
            ranks = nx.pagerank(self.graph)
            
            if node_type:
                # Filter by type
                ranks = {k: v for k, v in ranks.items() 
                        if self.graph.nodes[k].get('type') == node_type}
            
            sorted_ranks = sorted(ranks.items(), key=lambda x: x[1], reverse=True)
            return sorted_ranks[:limit]
        except:
            return []
    
    def find_communities(self) -> List[List[str]]:
        """Find clusters/communities in the graph"""
        try:
            import networkx.algorithms.community as nx_comm
            communities = nx_comm.greedy_modularity_communities(
                self.graph.to_undirected()
            )
            return [[node for node in community] for community in communities]
        except:
            return []
    
    def suggest_connections(self, node_id: str) -> List[str]:
        """Suggest potential connections (2-hop neighbors)"""
        try:
            # Find 2-hop neighbors (friends of friends, etc.)
            two_hop = set()
            
            for neighbor in self.graph.neighbors(node_id):
                for second_neighbor in self.graph.neighbors(neighbor):
                    # Exclude self and direct connections
                    if (second_neighbor != node_id and 
                        second_neighbor not in self.graph.neighbors(node_id)):
                        two_hop.add(second_neighbor)
            
            return list(two_hop)
        except nx.NodeNotFound:
            return []
    
    def get_node_info(self, node_id: str) -> Optional[Dict]:
        """Get full information about a node"""
        try:
            if node_id in self.graph:
                return dict(self.graph.nodes[node_id])
            return None
        except:
            return None
    
    def get_related_by_type(self, node_id: str, target_type: str, max_distance: int = 2) -> List[str]:
        """Find all nodes of specific type within N hops"""
        try:
            nearby = nx.single_source_shortest_path_length(
                self.graph, node_id, cutoff=max_distance
            )
            
            return [
                n for n in nearby.keys() 
                if self.graph.nodes[n].get("type") == target_type
            ]
        except nx.NodeNotFound:
            return []
    
    def save_to_cache(self):
        """Save graph to pickle cache for fast loading"""
        try:
            with open(self.cache_path, 'wb') as f:
                pickle.dump(self.graph, f)
            logger.info(f"💾 Graph cached: {self.graph.number_of_nodes()} nodes")
        except Exception as e:
            logger.error(f"Failed to cache graph: {e}")
    
    def load_from_cache(self) -> bool:
        """Load graph from cache if available"""
        try:
            if os.path.exists(self.cache_path):
                with open(self.cache_path, 'rb') as f:
                    self.graph = pickle.load(f)
                logger.info(f"✅ Graph loaded from cache: {self.graph.number_of_nodes()} nodes")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """Get graph statistics"""
        try:
            return {
                "nodes": self.graph.number_of_nodes(),
                "edges": self.graph.number_of_edges(),
                "node_types": self._count_by_type(),
                "avg_degree": sum(dict(self.graph.degree()).values()) / max(1, self.graph.number_of_nodes()),
                "connected_components": nx.number_weakly_connected_components(self.graph)
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}
    
    def _count_by_type(self) -> Dict[str, int]:
        """Count nodes by type"""
        counts = {}
        for node in self.graph.nodes():
            node_type = self.graph.nodes[node].get('type', 'unknown')
            counts[node_type] = counts.get(node_type, 0) + 1
        return counts


# Global instance
graph_engine = ZoeGraphEngine()












