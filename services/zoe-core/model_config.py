from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
import sqlite3
import json
import logging
import os
import platform
from datetime import datetime

logger = logging.getLogger(__name__)

# Hardware Detection
def detect_hardware():
    """Detect hardware platform (Jetson or Pi5)"""
    # Check for Jetson
    if os.path.exists('/etc/nv_tegra_release'):
        return 'jetson'
    
    # Check for Raspberry Pi
    if os.path.exists('/proc/device-tree/model'):
        try:
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read()
                if 'Raspberry Pi 5' in model:
                    return 'pi5'
                elif 'Raspberry Pi' in model:
                    return 'pi5'  # Assume Pi5 for other Pi models
        except:
            pass
    
    # Check architecture as fallback
    arch = platform.machine()
    if 'aarch64' in arch or 'arm' in arch:
        return 'pi5'  # Default ARM to Pi
    
    return 'jetson'  # Default to Jetson for development

# Get hardware platform
HARDWARE_PLATFORM = os.getenv('HARDWARE_PLATFORM', detect_hardware())
logger.info(f"🖥️  Hardware platform: {HARDWARE_PLATFORM}")

class ModelCategory(Enum):
    FAST_LANE = "fast_lane"
    BALANCED = "balanced"
    HEAVY_REASONING = "heavy_reasoning"

@dataclass
class ModelConfig:
    name: str
    category: ModelCategory
    temperature: float
    top_p: float
    num_predict: int
    num_ctx: int
    repeat_penalty: float
    stop_tokens: list
    timeout: int
    description: str
    num_gpu: Optional[int] = None  # GPU layers: -1=auto, 0=CPU only, 1+=GPU layers, 99=all layers
    benchmark_score: Optional[float] = None
    tool_calling_score: Optional[float] = None
    response_time_avg: Optional[float] = None
    quality_score: Optional[float] = None
    warmth_score: Optional[float] = None
    intelligence_score: Optional[float] = None
    success_rate: Optional[float] = None
    total_calls: Optional[int] = None
    last_updated: Optional[str] = None

# Model configurations optimized for Samantha-level intelligence
MODEL_CONFIGS = {
    "phi3:mini": ModelConfig(
        name="phi3:mini",
        category=ModelCategory.FAST_LANE,
        temperature=0.7,
        top_p=0.9,
        num_predict=256,
        num_ctx=2048,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=30,
        description="Smallest, fastest CPU model - 3.8B parameters, excellent for chat",
        benchmark_score=85.0,
        tool_calling_score=40.0
    ),
    
    "llama3.2:3b": ModelConfig(
        name="llama3.2:3b",
        category=ModelCategory.FAST_LANE,
        temperature=0.7,
        top_p=0.9,
        num_predict=512,  # Increased from 256 for complete responses
        num_ctx=4096,  # Increased from 2048 for better context memory
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:", "<tool_call>"],  # Added tool_call stop token
        timeout=30,
        description="🏆 TESTED WINNER - Fast, stable, 0 hallucinations (3.19s avg, 75/100 quality)",
        benchmark_score=85.0,  # Updated from real testing
        tool_calling_score=35.0,
        response_time_avg=3.19,  # From actual testing
        quality_score=75.0  # From actual testing
    ),
    
    "gemma3n-e2b-gpu:latest": ModelConfig(
        name="gemma3n-e2b-gpu:latest",
        category=ModelCategory.FAST_LANE,
        temperature=0.7,
        top_p=0.9,
        num_predict=256,
        num_ctx=2048,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=45,
        description="GPU-optimized fast model - 4.5B parameters (has GPU allocation issues - use gemma3n-e2b-gpu-fixed instead)",
        benchmark_score=90.0,
        tool_calling_score=45.0
    ),
    
    "gemma3n-e2b-gpu-fixed": ModelConfig(
        name="gemma3n-e2b-gpu-fixed",
        category=ModelCategory.FAST_LANE,
        temperature=0.6,  # Lower temperature for less hallucination
        top_p=0.9,
        num_predict=512,  # Increased from 64 to allow full responses
        num_ctx=4096,  # Increased from 1024 for better context
        repeat_penalty=1.2,  # Increased from 1.1 to reduce repetition
        stop_tokens=["\n\n", "User:", "Human:", "Zoe:", "Response:", "Thought:"],  # Added more stop tokens
        timeout=45,  # Increased timeout for longer responses
        num_gpu=99,  # All GPU layers - works fast with proper configuration
        description="GPU-optimized ultra-fast model - 4.5B parameters, multimodal (vision)",
        benchmark_score=90.0,
        tool_calling_score=45.0
    ),
    
    "gemma3n:e4b": ModelConfig(
        name="gemma3n:e4b",
        category=ModelCategory.BALANCED,
        temperature=0.7,
        top_p=0.9,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=60,
        description="Multimodal model - image, audio, video understanding (from Gemma DevDay article)",
        benchmark_score=85.0,
        tool_calling_score=40.0
    ),
    
    "gemma3:27b": ModelConfig(
        name="gemma3:27b",
        category=ModelCategory.HEAVY_REASONING,
        temperature=0.8,
        top_p=0.9,
        num_predict=1024,
        num_ctx=8192,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=120,
        description="Large, very accurate model - 27B parameters (from Gemma DevDay article) - NOTE: Requires 11.3 GiB memory, use qwen3:8b or deepseek-r1:14b as alternative",
        benchmark_score=95.0,
        tool_calling_score=50.0
    ),
    
    "gemma2:2b": ModelConfig(
        name="gemma2:2b",
        category=ModelCategory.FAST_LANE,
        temperature=0.6,
        top_p=0.9,
        num_predict=256,
        num_ctx=2048,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=30,
        description="Small, fast model - 2B parameters (from Gemma DevDay article)",
        benchmark_score=75.0,
        tool_calling_score=30.0
    ),
    
    "gemma3:1b": ModelConfig(
        name="gemma3:1b",
        category=ModelCategory.FAST_LANE,
        temperature=0.7,
        top_p=0.9,
        num_predict=128,
        num_ctx=1024,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=30,
        description="Quick queries, casual chat - Claude's recommended fast model"
    ),
    
    "llama3.2:1b": ModelConfig(
        name="llama3.2:1b",
        category=ModelCategory.FAST_LANE,
        temperature=0.6,
        top_p=0.9,
        num_predict=128,
        num_ctx=1024,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=30,
        description="Current benchmark winner - fastest with 50% tool call rate",
        benchmark_score=95.0,
        tool_calling_score=50.0,
        response_time_avg=11.11
    ),
    
    "qwen2.5:1.5b": ModelConfig(
        name="qwen2.5:1.5b",
        category=ModelCategory.FAST_LANE,
        temperature=0.6,
        top_p=0.9,
        num_predict=128,
        num_ctx=1024,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=30,
        description="Fast responses - Claude's recommended fast model"
    ),
    
    "qwen2.5:3b": ModelConfig(
        name="qwen2.5:3b",
        category=ModelCategory.BALANCED,
        temperature=0.7,
        top_p=0.9,
        num_predict=256,
        num_ctx=2048,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=60,
        num_gpu=35,  # ✅ QWEN OPTIMIZED: ~35 layers for 3B model
        description="Balanced performance - Good function calling",
        tool_calling_score=75.0
    ),
    
    "qwen2.5:7b": ModelConfig(
        name="qwen2.5:7b",
        category=ModelCategory.BALANCED,
        temperature=0.7,
        top_p=0.9,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:", "<tool_call>"],  # Added tool_call stop token
        timeout=60,
        num_gpu=43,  # ✅ QWEN OPTIMIZED: ~43 layers for 7B model
        description="🥈 TESTED - Best tool calling (3.26s avg, 75/100 quality, Q4 quantization)",
        benchmark_score=92.0,
        tool_calling_score=90.0,
        response_time_avg=3.26,  # From actual testing (Q4)
        quality_score=75.0  # From actual testing
    ),
    
    "hermes3:8b-llama3.1-q4_K_M": ModelConfig(
        name="hermes3:8b-llama3.1-q4_K_M",
        category=ModelCategory.BALANCED,
        temperature=0.7,
        top_p=0.9,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=60,
        num_gpu=-1,  # ✅ HERMES OPTIMIZED: Auto-detect (uses all available efficiently)
        description="BEST function calling model - Hermes-3 Llama 3.1 8B Q4 quantization",
        benchmark_score=95.0,
        tool_calling_score=95.0
    ),
    
    "qwen2.5:14b": ModelConfig(
        name="qwen2.5:14b",
        category=ModelCategory.HEAVY_REASONING,
        temperature=0.7,
        top_p=0.9,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=60,
        description="Primary workhorse - Claude's recommended balanced model"
    ),
    
    "gemma3:4b": ModelConfig(
        name="gemma3:4b",
        category=ModelCategory.BALANCED,
        temperature=0.7,
        top_p=0.9,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=60,
        description="Good balance - Claude's recommended balanced model"
    ),
    
    "qwen3:8b": ModelConfig(
        name="qwen3:8b",
        category=ModelCategory.HEAVY_REASONING,
        temperature=0.8,
        top_p=0.9,
        num_predict=1024,
        num_ctx=8192,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=120,
        description="New flagship model - Claude's recommended heavy reasoning model"
    ),
    
    "deepseek-r1:14b": ModelConfig(
        name="deepseek-r1:14b",
        category=ModelCategory.HEAVY_REASONING,
        temperature=0.8,
        top_p=0.9,
        num_predict=1024,
        num_ctx=8192,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=120,
        description="Complex analysis - Claude's recommended heavy reasoning model"
    ),
    
    "mistral:7b": ModelConfig(
        name="mistral:7b",
        category=ModelCategory.HEAVY_REASONING,
        temperature=0.8,
        top_p=0.9,
        num_predict=1024,
        num_ctx=8192,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=120,
        description="General heavy tasks - Claude's recommended heavy reasoning model"
    ),
    
    # ========== vLLM AWQ Models (Jetson Optimized) ==========
    "llama-3.2-3b": ModelConfig(
        name="llama-3.2-3b",
        category=ModelCategory.FAST_LANE,
        temperature=0.7,
        top_p=0.95,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=30,
        description="Llama-3.2-3B-Instruct-AWQ - Fast conversation, voice responses (2GB)",
        benchmark_score=90.0,
        tool_calling_score=70.0,
        response_time_avg=0.5
    ),
    
    "qwen2.5-coder-7b": ModelConfig(
        name="qwen2.5-coder-7b",
        category=ModelCategory.BALANCED,
        temperature=0.5,
        top_p=0.95,
        num_predict=512,
        num_ctx=8192,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=60,
        description="Qwen2.5-Coder-7B-Instruct-AWQ - Tool calling, Home Assistant automation (4.5GB)",
        benchmark_score=95.0,
        tool_calling_score=98.0,
        response_time_avg=1.2
    ),
    
    "qwen2-vl-7b": ModelConfig(
        name="qwen2-vl-7b",
        category=ModelCategory.HEAVY_REASONING,
        temperature=0.6,
        top_p=0.95,
        num_predict=512,
        num_ctx=4096,
        repeat_penalty=1.1,
        stop_tokens=["\n\n", "User:", "Human:"],
        timeout=90,
        description="Qwen2-VL-7B-Instruct-AWQ - Vision analysis, photo understanding (5GB)",
        benchmark_score=92.0,
        tool_calling_score=75.0,
        response_time_avg=3.0
    )
}

class ModelSelector:
    """Enhanced intelligent model selection with quality monitoring and learning integration"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.performance_history = {}
        self.db_path = db_path
        self.hardware = HARDWARE_PLATFORM
        
        # Platform-specific model selection
        if self.hardware == 'jetson':
            # Jetson Orin NX: GPU-accelerated models
            self.current_model = "hermes3:8b-llama3.1-q4_K_M"  # PRIMARY - BEST function calling (GPU)
            self.fallback_chain = [
                "hermes3:8b-llama3.1-q4_K_M",  # PRIMARY (4.9 GB, GPU)
                "qwen2.5:7b",                   # Excellent alternative (4.7 GB, GPU)
                "phi3:mini",                    # Fast fallback (2.2 GB, CPU)
                "llama3.2:3b",                 # Reliable fallback (2.0 GB, CPU)
            ]
            logger.info(f"🚀 Jetson mode: GPU-accelerated models enabled")
        else:
            # Raspberry Pi 5: CPU-optimized models
            self.current_model = "phi3:mini"  # PRIMARY for Pi - fast on CPU
            self.fallback_chain = [
                "phi3:mini",                    # PRIMARY (2.2 GB, CPU-optimized)
                "llama3.2:3b",                 # Alternative (2.0 GB)
                "qwen2.5:3b",                   # Balanced (2.0 GB)
                "gemma2:2b",                    # Lightweight (1.6 GB)
            ]
            logger.info(f"🥧 Raspberry Pi mode: CPU-optimized models enabled")
        
        # Initialize database for quality tracking
        self._init_quality_database()
        
        # Load existing performance data
        self._load_performance_data()
    
    def _init_quality_database(self):
        """Initialize SQLite database for quality metrics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create model_quality table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS model_quality (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    response_time REAL,
                    success BOOLEAN,
                    quality_score REAL,
                    warmth_score REAL,
                    intelligence_score REAL,
                    tool_calling_score REAL,
                    query_type TEXT,
                    user_id TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Quality database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize quality database: {e}")
    
    def _load_performance_data(self):
        """Load existing performance data from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Load performance data
            cursor.execute('''
                SELECT model_name, AVG(response_time), AVG(CASE WHEN success THEN 1 ELSE 0 END), COUNT(*)
                FROM model_quality
                GROUP BY model_name
            ''')
            
            for row in cursor.fetchall():
                model_name, avg_time, success_rate, total_calls = row
                if model_name in MODEL_CONFIGS:
                    config = MODEL_CONFIGS[model_name]
                    config.response_time_avg = avg_time
                    config.success_rate = success_rate
                    config.total_calls = total_calls
            
            conn.close()
            logger.info("Performance data loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load performance data: {e}")
    
    def select_model(self, query_type: str = "conversation") -> str:
        """Select the best model for the given query type"""
        try:
            if query_type == "action":
                return self._get_best_action_model()
            elif query_type == "memory-retrieval":
                return self._get_best_memory_model()
            elif query_type == "conversation":
                return self._get_best_conversation_model()
            else:
                return self.current_model
        except Exception as e:
            logger.error(f"Model selection failed: {e}")
            return self.current_model
    
    def _get_best_fast_model(self) -> str:
        """Get the best performing fast model"""
        fast_models = [name for name, config in MODEL_CONFIGS.items() 
                      if config.category == ModelCategory.FAST_LANE]
        
        # Sort by benchmark score if available
        scored_models = [(name, MODEL_CONFIGS[name].benchmark_score or 0) for name in fast_models]
        scored_models.sort(key=lambda x: x[1], reverse=True)
        
        return scored_models[0][0] if scored_models else "llama3.2:1b"
    
    def _get_best_balanced_model(self) -> str:
        """Get the best performing balanced model"""
        balanced_models = [name for name, config in MODEL_CONFIGS.items() 
                          if config.category == ModelCategory.BALANCED]
        
        # Prefer models with good tool calling scores
        scored_models = [(name, MODEL_CONFIGS[name].tool_calling_score or 0) for name in balanced_models]
        scored_models.sort(key=lambda x: x[1], reverse=True)
        
        return scored_models[0][0] if scored_models else "qwen2.5:7b"
    
    def _get_best_conversation_model(self) -> str:
        """Get the best model for general conversation - tested on Jetson 2025-11-18"""
        # 🏆 WINNER: Llama-3.2-3B (1.9GB, 3.19s avg, 75/100 quality, 0 hallucinations)
        # Tested with multi-turn conversations - stable, fast, no fabrications
        # See: MODEL_TEST_ANALYSIS.md for full benchmark results
        if "llama3.2:3b" in MODEL_CONFIGS:
            return "llama3.2:3b"
        
        # Fallback: phi3:mini (also tested, reliable)
        return "phi3:mini"
    
    def get_routing_model(self) -> str:
        """Get lightweight CPU model for routing decisions (always phi3:mini)"""
        return "phi3:mini"
    
    def _get_best_action_model(self) -> str:
        """Get the best model for action execution (prioritizes tool calling ability)"""
        # 🎯 CRITICAL: Use Qwen models for tool calling - they have 90-95 score vs gemma's 45!
        # Priority order based on tool_calling_score AND availability:
        # 1. qwen2.5:7b (score: 90, AVAILABLE in Ollama)
        # 2. qwen2.5:14b (score: 95, but requires 11GB+ memory) 
        # 3. qwen2.5:3b (score: 75)
        # 4. hermes3 (score: 50, good fallback)
        
        qwen_preference = ["qwen2.5:7b", "qwen2.5:14b", "qwen2.5:3b", "hermes3:8b-llama3.1-q4_K_M"]
        for model in qwen_preference:
            if model in MODEL_CONFIGS:
                logger.info(f"✅ Selected {model} for action execution (tool_calling_score: {MODEL_CONFIGS[model].tool_calling_score})")
                return model
        
        # Fallback: Sort all models by tool calling score
        action_models = [name for name, config in MODEL_CONFIGS.items() 
                        if config.tool_calling_score and config.tool_calling_score > 70]  # Only high-scoring models
        
        if action_models:
            scored_models = [(name, MODEL_CONFIGS[name].tool_calling_score) for name in action_models]
            scored_models.sort(key=lambda x: x[1], reverse=True)
            logger.info(f"✅ Fallback to {scored_models[0][0]} for actions (tool_calling_score: {scored_models[0][1]})")
            return scored_models[0][0]
        
        # Last resort: Try qwen even if not in configs
        working_models = ["qwen2.5:7b", "qwen2.5:3b", "phi3:mini"]
        for model in working_models:
            if model in MODEL_CONFIGS:
                return model
        
        logger.warning("⚠️ No good action model found, using phi3:mini")
        return "phi3:mini"
    
    def _get_best_memory_model(self) -> str:
        """Get the best model for memory retrieval"""
        # For memory, prefer balanced models
        return self._get_best_balanced_model()
    
    def get_model_config(self, model_name: str) -> ModelConfig:
        """Get configuration for a specific model"""
        return MODEL_CONFIGS.get(model_name, MODEL_CONFIGS["llama3.2:1b"])
    
    def update_performance(self, model_name: str, response_time: float, success: bool):
        """Update performance metrics for a model"""
        if model_name not in self.performance_history:
            self.performance_history[model_name] = {
                "response_times": [],
                "success_rate": 0,
                "total_calls": 0
            }
        
        history = self.performance_history[model_name]
        history["response_times"].append(response_time)
        history["total_calls"] += 1
        
        # Update success rate
        if success:
            history["success_rate"] = (history["success_rate"] * (history["total_calls"] - 1) + 1) / history["total_calls"]
        else:
            history["success_rate"] = (history["success_rate"] * (history["total_calls"] - 1)) / history["total_calls"]
        
        # Keep only last 100 response times
        if len(history["response_times"]) > 100:
            history["response_times"] = history["response_times"][-100:]
    
    def get_fallback_model(self, failed_model: str) -> str:
        """Get the next model in the fallback chain"""
        try:
            current_index = self.fallback_chain.index(failed_model)
            if current_index + 1 < len(self.fallback_chain):
                return self.fallback_chain[current_index + 1]
        except ValueError:
            pass
        
        return self.fallback_chain[0]  # Default to first fallback
    
    async def record_quality_metrics(self, model_name: str, response_time: float, success: bool, 
                                   quality_scores: dict, query_type: str, user_id: str):
        """Record detailed quality metrics to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO model_quality 
                (model_name, response_time, success, quality_score, warmth_score, 
                 intelligence_score, tool_calling_score, query_type, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                model_name, response_time, success,
                quality_scores.get('quality', 0),
                quality_scores.get('warmth', 0),
                quality_scores.get('intelligence', 0),
                quality_scores.get('tool_calling', 0),
                query_type, user_id
            ))
            
            conn.commit()
            conn.close()
            
            # Update in-memory config
            if model_name in MODEL_CONFIGS:
                config = MODEL_CONFIGS[model_name]
                config.quality_score = quality_scores.get('quality', 0)
                config.warmth_score = quality_scores.get('warmth', 0)
                config.intelligence_score = quality_scores.get('intelligence', 0)
                config.last_updated = str(datetime.now())
            
            logger.info(f"Quality metrics recorded for {model_name}")
        except Exception as e:
            logger.error(f"Failed to record quality metrics: {e}")
    
    def get_quality_analysis(self) -> dict:
        """Get aggregated quality analysis"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get summary statistics
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_calls,
                    AVG(quality_score) as avg_quality,
                    AVG(warmth_score) as avg_warmth,
                    AVG(intelligence_score) as avg_intelligence,
                    COUNT(DISTINCT model_name) as models_tracked
                FROM model_quality
            ''')
            
            summary = cursor.fetchone()
            
            # Get detailed metrics per model
            cursor.execute('''
                SELECT 
                    model_name,
                    COUNT(*) as total_calls,
                    AVG(CASE WHEN success THEN 1 ELSE 0 END) as success_rate,
                    AVG(response_time) as avg_response_time,
                    AVG(quality_score) as quality_score,
                    AVG(warmth_score) as warmth_score,
                    AVG(intelligence_score) as intelligence_score,
                    AVG(tool_calling_score) as tool_calling_score,
                    MAX(timestamp) as last_updated
                FROM model_quality
                GROUP BY model_name
            ''')
            
            detailed_metrics = {}
            for row in cursor.fetchall():
                model_name = row[0]
                detailed_metrics[model_name] = {
                    "total_calls": row[1],
                    "success_rate": row[2],
                    "avg_response_time": row[3],
                    "quality_score": row[4],
                    "warmth_score": row[5],
                    "intelligence_score": row[6],
                    "tool_calling_score": row[7],
                    "last_updated": row[8]
                }
            
            conn.close()
            
            return {
                "summary": {
                    "total_calls": summary[0],
                    "avg_quality_score": summary[1],
                    "avg_warmth_score": summary[2],
                    "avg_intelligence_score": summary[3],
                    "models_tracked": summary[4]
                },
                "detailed_metrics": detailed_metrics,
                "timestamp": str(datetime.now())
            }
        except Exception as e:
            logger.error(f"Failed to get quality analysis: {e}")
            return {"error": str(e)}

# Global model selector instance
model_selector = ModelSelector()

# Quality Analyzer for real-time response analysis
class QualityAnalyzer:
    """Analyze response quality in real-time"""
    
    @staticmethod
    def analyze_response(response_text: str, query_type: str) -> dict:
        """Analyze response quality and return scores"""
        try:
            # Basic quality analysis
            quality_score = 5.0  # Default
            warmth_score = 5.0   # Default
            intelligence_score = 5.0  # Default
            tool_calling_score = 0.0  # Default
            
            # Check for tool calls
            if "[TOOL_CALL:" in response_text:
                tool_calling_score = 10.0
                quality_score = 8.0
            
            # Check for warmth indicators
            warmth_indicators = ["😊", "!", "warm", "friendly", "helpful", "great", "wonderful"]
            warmth_count = sum(1 for indicator in warmth_indicators if indicator.lower() in response_text.lower())
            warmth_score = min(10.0, 5.0 + warmth_count)
            
            # Check for intelligence indicators
            intelligence_indicators = ["analysis", "insight", "recommendation", "suggestion", "consider", "think"]
            intelligence_count = sum(1 for indicator in intelligence_indicators if indicator.lower() in response_text.lower())
            intelligence_score = min(10.0, 5.0 + intelligence_count)
            
            return {
                "quality": quality_score,
                "warmth": warmth_score,
                "intelligence": intelligence_score,
                "tool_calling": tool_calling_score
            }
        except Exception as e:
            logger.error(f"Quality analysis failed: {e}")
            return {"quality": 5.0, "warmth": 5.0, "intelligence": 5.0, "tool_calling": 0.0}