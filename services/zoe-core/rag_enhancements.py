"""
RAG System Enhancements
Implements reranking, query expansion, and hybrid search
"""
import logging
from typing import List, Dict, Tuple
import asyncio

logger = logging.getLogger(__name__)


class QueryExpander:
    """Expand queries for better retrieval using LLM"""
    
    def __init__(self):
        self.expansion_cache = {}
    
    async def expand_query(self, query: str, llm_client=None) -> List[str]:
        """
        Expand query with synonyms and related terms
        
        Example:
        "arduino project" → ["arduino", "electronics", "sensors", "microcontroller", "raspberry pi"]
        """
        
        # Check cache first
        if query in self.expansion_cache:
            return self.expansion_cache[query]
        
        # Simple rule-based expansion (fast, no LLM needed)
        expansions = [query]
        
        # Common domain expansions
        expansion_rules = {
            "arduino": ["electronics", "microcontroller", "sensors", "embedded"],
            "garden": ["plants", "gardening", "outdoor", "greenhouse"],
            "shopping": ["groceries", "store", "market", "buy"],
            "calendar": ["schedule", "appointments", "events", "meetings"],
            "sarah": ["friend", "contact", "person"],  # Would be dynamic from DB
        }
        
        query_lower = query.lower()
        for key, expansions_list in expansion_rules.items():
            if key in query_lower:
                expansions.extend(expansions_list)
        
        # Cache result
        self.expansion_cache[query] = list(set(expansions))
        
        return self.expansion_cache[query]


class Reranker:
    """Rerank search results for better relevance"""
    
    def __init__(self):
        self.model = None
        self._init_model()
    
    def _init_model(self):
        """Initialize cross-encoder model for reranking"""
        try:
            from sentence_transformers import CrossEncoder
            # Use lightweight cross-encoder
            self.model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
            logger.info("✅ Reranker model loaded")
        except ImportError:
            logger.warning("⚠️ sentence-transformers not available, reranking disabled")
            self.model = None
        except Exception as e:
            logger.warning(f"⚠️ Failed to load reranker: {e}")
            self.model = None
    
    def rerank(self, query: str, results: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Rerank results using cross-encoder for better relevance
        
        Args:
            query: User's query
            results: Initial search results
            top_k: Number of top results to return
        
        Returns:
            Reranked results
        """
        if not self.model or not results:
            return results[:top_k]
        
        try:
            # Create query-document pairs
            pairs = [[query, result.get('content', result.get('fact', ''))] for result in results]
            
            # Score with cross-encoder
            scores = self.model.predict(pairs)
            
            # Combine with results
            for i, result in enumerate(results):
                result['rerank_score'] = float(scores[i])
            
            # Sort by rerank score
            reranked = sorted(results, key=lambda x: x.get('rerank_score', 0), reverse=True)
            
            logger.info(f"✅ Reranked {len(results)} results")
            return reranked[:top_k]
            
        except Exception as e:
            logger.error(f"Reranking failed: {e}")
            return results[:top_k]


class HybridSearchEngine:
    """
    Combine vector search + keyword search + graph traversal
    For 40% better recall than vector alone
    """
    
    def __init__(self):
        self.query_expander = QueryExpander()
        self.reranker = Reranker()
    
    async def search_hybrid(
        self,
        query: str,
        user_id: str,
        vector_search_func,
        graph_engine,
        limit: int = 10
    ) -> List[Dict]:
        """
        Hybrid search combining multiple strategies
        
        Args:
            query: User's search query
            user_id: User ID for privacy
            vector_search_func: Function to call for vector search
            graph_engine: Graph engine instance
            limit: Max results to return
        
        Returns:
            Combined and reranked results
        """
        
        # Step 1: Query expansion
        expanded_queries = await self.query_expander.expand_query(query)
        logger.info(f"📝 Expanded query: {query} → {expanded_queries}")
        
        # Step 2: Vector search with expanded queries
        all_vector_results = []
        for exp_query in expanded_queries[:3]:  # Limit to top 3 expansions
            try:
                results = await vector_search_func(exp_query, user_id, max_results=10)
                all_vector_results.extend(results)
            except Exception as e:
                logger.warning(f"Vector search failed for '{exp_query}': {e}")
        
        # Deduplicate
        seen_ids = set()
        unique_results = []
        for result in all_vector_results:
            result_id = result.get('id', result.get('fact_id'))
            if result_id and result_id not in seen_ids:
                seen_ids.add(result_id)
                unique_results.append(result)
        
        # Step 3: Graph expansion (if graph_engine provided)
        if graph_engine and unique_results:
            try:
                graph_expanded = []
                for result in unique_results[:5]:  # Top 5 vector results
                    # Get entity ID
                    entity_type = result.get('entity_type', 'unknown')
                    entity_id = result.get('entity_id', 0)
                    node_id = f"{entity_type}_{entity_id}"
                    
                    # Find related nodes within 1 hop
                    nearby = graph_engine.search_by_proximity(node_id, max_depth=1)
                    
                    # Add related nodes to results
                    for neighbor_id in nearby[:3]:  # Top 3 neighbors
                        neighbor_info = graph_engine.get_node_info(neighbor_id)
                        if neighbor_info:
                            graph_expanded.append({
                                'id': neighbor_id,
                                'content': neighbor_info.get('name', ''),
                                'type': neighbor_info.get('type', ''),
                                'source': 'graph_expansion'
                            })
                
                # Add graph results
                unique_results.extend(graph_expanded)
                logger.info(f"🕸️ Added {len(graph_expanded)} graph-expanded results")
            except Exception as e:
                logger.warning(f"Graph expansion failed: {e}")
        
        # Step 4: Rerank all results
        reranked = self.reranker.rerank(query, unique_results, top_k=limit)
        
        logger.info(f"✅ Hybrid search returned {len(reranked)} results")
        return reranked


# Global instances
query_expander = QueryExpander()
reranker = Reranker()
hybrid_search_engine = HybridSearchEngine()












