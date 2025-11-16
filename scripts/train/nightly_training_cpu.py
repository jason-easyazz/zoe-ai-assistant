#!/usr/bin/env python3
"""
Nightly CPU-Only Training for Raspberry Pi 5
Runs at 10pm, trains for 8-12 hours, ready by morning
"""

import sys
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

sys.path.append(str(PROJECT_ROOT / "services/zoe-core"))
sys.path.append('/app')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CPUNightlyTrainer:
    """CPU-optimized overnight training for Pi 5"""
    
    def __init__(self):
        self.adapter_dir = PROJECT_ROOT / "models/adapters"
        self.adapter_dir.mkdir(parents=True, exist_ok=True)
        self.min_examples = 20
        
    async def run_training(self):
        """Main training pipeline - CPU only"""
        
        logger.info("🌙 Starting nightly CPU training at %s", datetime.now())
        
        try:
            # Import training collector
            from training_engine.data_collector import training_collector
            
            # 1. Get training data
            stats = await training_collector.get_stats()
            examples_count = stats.get('today_count', 0)
            
            logger.info(f"📊 Found {examples_count} training examples")
            
            if examples_count < self.min_examples:
                logger.info(f"⏭️  Only {examples_count} examples, need {self.min_examples}. Skipping training.")
                await self._log_training_run(
                    examples_count=examples_count,
                    status="skipped",
                    reason=f"Not enough examples ({examples_count}/{self.min_examples})"
                )
                return
            
            # 2. Get training data
            training_data = await training_collector.get_training_data(limit=1000)
            
            logger.info(f"🏋️ Training on {len(training_data)} examples (CPU-only, will take 8-12 hours)")
            
            # 3. Run CPU training
            start_time = datetime.now()
            success = await self._train_cpu_lora(training_data)
            training_time = (datetime.now() - start_time).total_seconds() / 60
            
            if success:
                logger.info(f"✅ Training completed in {training_time:.1f} minutes")
                await self._log_training_run(
                    examples_count=len(training_data),
                    status="completed",
                    training_time=training_time
                )
            else:
                logger.error("❌ Training failed")
                await self._log_training_run(
                    examples_count=len(training_data),
                    status="failed",
                    training_time=training_time
                )
                
        except Exception as e:
            logger.error(f"❌ Training error: {e}", exc_info=True)
            await self._log_training_run(
                examples_count=0,
                status="error",
                reason=str(e)
            )
    
    async def _train_cpu_lora(self, training_data):
        """
        CPU-only LoRA training using Hugging Face Transformers
        Optimized for Raspberry Pi 5 (8GB RAM)
        """
        
        try:
            # Import here to avoid loading if not needed
            from transformers import (
                AutoModelForCausalLM, 
                AutoTokenizer,
                TrainingArguments,
                Trainer,
                DataCollatorForLanguageModeling
            )
            from peft import LoraConfig, get_peft_model, TaskType
            from datasets import Dataset
            import torch
            
            logger.info("📦 Loading base model...")
            
            # Use smallest model that works well - llama 1B
            model_name = "unsloth/Llama-3.2-1B-Instruct"  # Optimized version
            
            # Load tokenizer
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            tokenizer.pad_token = tokenizer.eos_token
            
            # Load model with CPU optimizations
            model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.float32,  # CPU uses float32
                device_map="cpu",
                low_cpu_mem_usage=True,
                use_cache=False,  # Saves memory
            )
            
            logger.info("🔧 Configuring LoRA...")
            
            # LoRA config - very lightweight
            lora_config = LoraConfig(
                r=8,  # Rank (smaller = faster on CPU)
                lora_alpha=16,
                target_modules=["q_proj", "v_proj"],  # Just 2 modules for speed
                lora_dropout=0.05,
                bias="none",
                task_type=TaskType.CAUSAL_LM
            )
            
            # Apply LoRA
            model = get_peft_model(model, lora_config)
            model.print_trainable_parameters()
            
            logger.info("📝 Preparing training data...")
            
            # Format training data
            formatted_data = []
            for example in training_data:
                # Use corrected output if available, else original
                output = example.get('corrected_response') or example.get('response', '')
                
                # Create instruction format
                text = f"<|user|>\n{example['message']}\n<|assistant|>\n{output}<|end|>"
                formatted_data.append({"text": text})
            
            # Create dataset
            dataset = Dataset.from_list(formatted_data)
            
            # Tokenize
            def tokenize_function(examples):
                return tokenizer(
                    examples["text"],
                    truncation=True,
                    max_length=512,  # Shorter for CPU training
                    padding="max_length"
                )
            
            tokenized_dataset = dataset.map(
                tokenize_function,
                batched=True,
                remove_columns=dataset.column_names
            )
            
            logger.info("🏃 Starting training (this will take 8-12 hours on CPU)...")
            
            # Training arguments - CPU optimized
            training_args = TrainingArguments(
                output_dir=str(self.adapter_dir / "temp"),
                num_train_epochs=3,
                per_device_train_batch_size=1,  # Small batch for CPU
                gradient_accumulation_steps=8,  # Effective batch = 8
                learning_rate=2e-4,
                weight_decay=0.01,
                warmup_steps=10,
                logging_steps=5,
                save_strategy="epoch",
                save_total_limit=2,
                fp16=False,  # CPU doesn't support fp16
                dataloader_num_workers=2,  # Use 2 CPU cores for data loading
                optim="adamw_torch",  # Standard optimizer for CPU
                gradient_checkpointing=True,  # Save memory
                max_steps=200,  # Limit training steps for overnight window
                load_best_model_at_end=False,
                report_to="none",  # No external logging
            )
            
            # Data collator
            data_collator = DataCollatorForLanguageModeling(
                tokenizer=tokenizer,
                mlm=False
            )
            
            # Trainer
            trainer = Trainer(
                model=model,
                args=training_args,
                train_dataset=tokenized_dataset,
                data_collator=data_collator,
            )
            
            # Train!
            logger.info("🏋️‍♀️ Training started at %s", datetime.now())
            logger.info("⏰ Expected completion: %s", datetime.now() + timedelta(hours=10))
            
            trainer.train()
            
            logger.info("💾 Saving adapter...")
            
            # Save adapter
            adapter_name = f"adapter_{datetime.now().strftime('%Y%m%d')}"
            adapter_path = self.adapter_dir / adapter_name
            model.save_pretrained(str(adapter_path))
            tokenizer.save_pretrained(str(adapter_path))
            
            logger.info(f"✅ Adapter saved to {adapter_path}")
            
            # Create current symlink
            current_link = self.adapter_dir / "current"
            if current_link.exists() or current_link.is_symlink():
                current_link.unlink()
            current_link.symlink_to(adapter_path)
            
            logger.info("🚀 Adapter deployed as 'current'")
            
            return True
            
        except Exception as e:
            logger.error(f"Training failed: {e}", exc_info=True)
            return False
    
    async def _log_training_run(self, examples_count, status, training_time=0, reason=""):
        """Log training run to database"""
        
        try:
            import sqlite3
            
            conn = sqlite3.connect('/app/data/training.db')
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO training_runs 
                (date, start_time, end_time, examples_count, status, training_time_minutes, notes)
                VALUES (?, datetime('now'), datetime('now'), ?, ?, ?, ?)
            """, (
                datetime.now().date().isoformat(),
                examples_count,
                status,
                training_time,
                reason
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to log training run: {e}")


async def main():
    """Run nightly training"""
    trainer = CPUNightlyTrainer()
    await trainer.run_training()
    logger.info("🌅 Training complete! Zoe is ready for tomorrow.")


if __name__ == "__main__":
    asyncio.run(main())












