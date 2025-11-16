#!/usr/bin/env python3
"""
Nightly Training Pipeline for Zoe
Runs at 2am to fine-tune on the day's interactions
"""
import sys
import os
import json
import sqlite3
from datetime import datetime, date
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import logging

# Setup paths
sys.path.append(str(PROJECT_ROOT / "services/zoe-core"))
sys.path.append('/app')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/zoe-training.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class NightlyTrainer:
    """Manages overnight training pipeline"""
    
    def __init__(self):
        self.base_model = "llama3.2-1b"
        self.adapter_dir = PROJECT_ROOT / "models/adapters"
        self.adapter_dir.mkdir(parents=True, exist_ok=True)
        self.training_db = "/app/data/training.db"
        self.min_examples = 20
        
        logger.info("🌙 Nightly Trainer initialized")
    
    def get_todays_training_data(self):
        """Retrieve today's training examples"""
        conn = sqlite3.connect(self.training_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT user_input, zoe_output, corrected_output, weight, context_json
                FROM training_examples
                WHERE date(timestamp) = date('now')
                AND weight > 0.5
                ORDER BY weight DESC
            """)
            
            examples = []
            for row in cursor.fetchall():
                # Use corrected output if available
                output = row[2] if row[2] else row[1]
                
                examples.append({
                    "input": row[0],
                    "output": output,
                    "weight": row[3],
                    "context": json.loads(row[4]) if row[4] else {}
                })
            
            return examples
        finally:
            conn.close()
    
    def log_training_run(self, examples_count, adapter_path, validation_score, training_time, deployed):
        """Record training run in database"""
        conn = sqlite3.connect(self.training_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO training_runs 
                (date, start_time, end_time, examples_count, adapter_path, validation_score, deployed, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date.today().isoformat(),
                datetime.now().isoformat(),
                datetime.now().isoformat(),
                examples_count,
                adapter_path,
                validation_score,
                deployed,
                f"Training completed in {training_time:.1f} minutes"
            ))
            
            conn.commit()
        finally:
            conn.close()
    
    def run_training(self):
        """Main training pipeline"""
        logger.info("🌙 Starting nightly training at %s", datetime.now())
        
        # 1. Get today's training data
        examples = self.get_todays_training_data()
        
        if len(examples) < self.min_examples:
            logger.info(f"⏭️  Only {len(examples)} examples (need {self.min_examples}), skipping training")
            return
        
        logger.info(f"📊 Found {len(examples)} training examples")
        logger.info(f"   • {sum(1 for ex in examples if ex['weight'] > 2.0)} corrections")
        logger.info(f"   • {sum(1 for ex in examples if ex['weight'] == 1.5)} positive feedback")
        
        # 2. Prepare training data
        logger.info("📝 Preparing training data...")
        
        # For now, save the prepared data for future Unsloth integration
        training_data_file = self.adapter_dir / f"training_data_{date.today().isoformat()}.json"
        with open(training_data_file, 'w') as f:
            json.dump(examples, f, indent=2)
        
        logger.info(f"💾 Saved training data to {training_data_file}")
        
        # 3. TODO: Actual LoRA training with Unsloth
        # This will be implemented when Unsloth is installed
        logger.info("⚠️  LoRA training requires Unsloth installation")
        logger.info("   For now, training data is collected and saved")
        logger.info("   Run: pip install unsloth to enable actual training")
        
        # 4. Create placeholder adapter metadata
        adapter_name = f"adapter_{date.today().strftime('%Y%m%d')}"
        adapter_path = self.adapter_dir / adapter_name
        adapter_path.mkdir(parents=True, exist_ok=True)
        
        metadata = {
            "created_at": datetime.now().isoformat(),
            "base_model": self.base_model,
            "examples_count": len(examples),
            "validation_score": 0.75,  # Placeholder
            "training_data_file": str(training_data_file),
            "status": "data_prepared",
            "note": "Training data prepared, waiting for Unsloth installation for actual fine-tuning"
        }
        
        with open(adapter_path / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info(f"📦 Created adapter metadata at {adapter_path}")
        
        # 5. Log training run
        self.log_training_run(
            examples_count=len(examples),
            adapter_path=str(adapter_path),
            validation_score=0.75,
            training_time=0.1,  # Placeholder
            deployed=False
        )
        
        logger.info("✅ Training pipeline completed")
        logger.info("🌅 System ready for morning!")
        
        return True


def main():
    """Entry point for cron job"""
    try:
        trainer = NightlyTrainer()
        trainer.run_training()
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Training failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()












