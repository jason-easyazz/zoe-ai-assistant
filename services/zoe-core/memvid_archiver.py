"""
memvid Learning Archive System (Phase 6: Self-Evolution)
Archive historical interactions for infinite learning corpus
"""

import sqlite3
import json
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from memvid.encoder import MemvidEncoder
from memvid.retriever import MemvidRetriever

logger = logging.getLogger(__name__)


class MemvidArchiver:
    """Archive Zoe's historical data to memvid format for learning"""
    
    def __init__(self, 
                 db_path: str = "/app/data/zoe.db",
                 memory_db_path: str = "/app/data/memory.db",
                 archive_dir: str = "/app/data/learning-archives"):
        self.db_path = db_path
        self.memory_db_path = memory_db_path
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        
        # Archive only data older than this
        self.archive_threshold_days = 90
    
    async def archive_chats_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive completed chat conversations (>90 days old) to memvid
        
        What's archived: Past chat patterns, response quality, user preferences
        What stays active: None (all chats become historical learning data)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Calculate quarter date range
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = f"{year}-{start_month:02d}-01"
        end_date = f"{year}-{end_month:02d}-31"
        
        # Get all chat messages from quarter (if older than threshold)
        cutoff_date = (datetime.now() - timedelta(days=self.archive_threshold_days)).isoformat()
        
        cursor.execute("""
            SELECT cm.id, cs.user_id, cm.role, cm.content, cm.metadata, cm.created_at, cs.id as session_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.created_at BETWEEN ? AND ?
              AND cm.created_at < ?
            ORDER BY cm.created_at
        """, (start_date, end_date, cutoff_date))
        
        messages = []
        for row in cursor.fetchall():
            metadata = json.loads(row[4]) if row[4] else {}
            messages.append({
                "message_id": row[0],
                "user_id": row[1],
                "role": row[2],
                "content": row[3],
                "metadata": metadata,
                "timestamp": row[5],
                "session_id": row[6],
                "data_type": "chat",
                "quarter": f"{year}-Q{quarter}"
            })
        
        conn.close()
        
        if not messages:
            return {
                "archived": 0,
                "message": f"No chats to archive for {year}-Q{quarter}",
                "dry_run": dry_run
            }
        
        if dry_run:
            return {
                "dry_run": True,
                "would_archive": len(messages),
                "quarter": f"{year}-Q{quarter}",
                "oldest": messages[0]['timestamp'],
                "newest": messages[-1]['timestamp']
            }
        
        # Create memvid encoder
        encoder = MemvidEncoder(enable_docker=False)
        
        # Add all messages as text
        full_text = "\n\n".join([json.dumps(msg) for msg in messages])
        encoder.add_text(full_text)
        
        # Build video
        output_file = str(self.archive_dir / f"chats-{year}-Q{quarter}.mp4")
        index_file = str(self.archive_dir / f"chats-{year}-Q{quarter}.idx")
        
        result = encoder.build_video(
            output_file=output_file,
            index_file=index_file,
            show_progress=False,
            auto_build_docker=False,
            allow_fallback=True
        )
        
        # Verify archive created
        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024*1024)
            
            # Delete from database (now safely archived)
            message_ids = [m['message_id'] for m in messages]
            conn = sqlite3.connect(self.db_path)
            placeholders = ','.join(['?'] * len(message_ids))
            conn.execute(f"DELETE FROM chat_messages WHERE id IN ({placeholders})", message_ids)
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "archived": len(messages),
                "file": output_file,
                "index_file": index_file,
                "size_mb": size_mb,
                "quarter": f"{year}-Q{quarter}",
                "deleted_from_db": len(message_ids)
            }
        else:
            return {
                "success": False,
                "error": "Video file not created"
            }
    
    async def archive_journal_entries_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive historical journal entries (>90 days old) to memvid
        
        What's archived: Past entries with mood, emotional patterns, life events
        What stays active: Recent 90 days for quick access
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = f"{year}-{start_month:02d}-01"
        end_date = f"{year}-{end_month:02d}-31"
        
        cutoff_date = (datetime.now() - timedelta(days=self.archive_threshold_days)).isoformat()
        
        cursor.execute("""
            SELECT id, user_id, title, content, mood, mood_score, tags, 
                   weather, location, created_at
            FROM journal_entries
            WHERE created_at BETWEEN ? AND ?
              AND created_at < ?
            ORDER BY created_at
        """, (start_date, end_date, cutoff_date))
        
        entries = []
        for row in cursor.fetchall():
            entries.append({
                "entry_id": row[0],
                "user_id": row[1],
                "title": row[2],
                "content": row[3],
                "mood": row[4],
                "mood_score": row[5],
                "tags": row[6],
                "weather": row[7],
                "location": row[8],
                "timestamp": row[9],
                "data_type": "journal",
                "quarter": f"{year}-Q{quarter}"
            })
        
        conn.close()
        
        if not entries:
            return {"archived": 0, "message": f"No journals to archive for {year}-Q{quarter}", "dry_run": dry_run}
        
        if dry_run:
            return {
                "dry_run": True,
                "would_archive": len(entries),
                "quarter": f"{year}-Q{quarter}"
            }
        
        # Encode to memvid
        encoder = MemvidEncoder(enable_docker=False)
        full_text = "\n\n".join([json.dumps(e) for e in entries])
        encoder.add_text(full_text)
        
        output_file = str(self.archive_dir / f"journals-{year}-Q{quarter}.mp4")
        index_file = str(self.archive_dir / f"journals-{year}-Q{quarter}.idx")
        
        result = encoder.build_video(output_file, index_file, show_progress=False, 
                                     auto_build_docker=False, allow_fallback=True)
        
        if os.path.exists(output_file):
            # Delete archived entries
            entry_ids = [e['entry_id'] for e in entries]
            conn = sqlite3.connect(self.db_path)
            placeholders = ','.join(['?'] * len(entry_ids))
            conn.execute(f"DELETE FROM journal_entries WHERE id IN ({placeholders})", entry_ids)
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "archived": len(entries),
                "file": output_file,
                "size_mb": os.path.getsize(output_file) / (1024*1024)
            }
        
        return {"success": False, "error": "Video not created"}
    
    async def archive_task_completions_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive completed task patterns (>90 days old) to memvid
        
        What's archived: Task completion patterns, timing, success rates
        What stays active: Current/active tasks
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = f"{year}-{start_month:02d}-01"
        end_date = f"{year}-{end_month:02d}-31"
        
        cutoff_date = (datetime.now() - timedelta(days=self.archive_threshold_days)).isoformat()
        
        # Get completed list items
        cursor.execute("""
            SELECT li.id, li.list_id, li.task_text, li.priority, li.completed,
                   li.completed_at, li.created_at, l.name as list_name, l.user_id
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE li.completed = 1
              AND li.completed_at BETWEEN ? AND ?
              AND li.completed_at < ?
            ORDER BY li.completed_at
        """, (start_date, end_date, cutoff_date))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "item_id": row[0],
                "list_name": row[7],
                "user_id": row[8],
                "task_text": row[2],
                "priority": row[3],
                "completed_at": row[5],
                "created_at": row[6],
                "time_to_complete_hours": self._calculate_hours(row[6], row[5]),
                "data_type": "task_completion",
                "quarter": f"{year}-Q{quarter}"
            })
        
        conn.close()
        
        if not tasks:
            return {"archived": 0, "message": f"No completed tasks for {year}-Q{quarter}", "dry_run": dry_run}
        
        if dry_run:
            return {"dry_run": True, "would_archive": len(tasks), "quarter": f"{year}-Q{quarter}"}
        
        # Encode to memvid
        encoder = MemvidEncoder(enable_docker=False)
        full_text = "\n\n".join([json.dumps(t) for t in tasks])
        encoder.add_text(full_text)
        
        output_file = str(self.archive_dir / f"tasks-{year}-Q{quarter}.mp4")
        index_file = str(self.archive_dir / f"tasks-{year}-Q{quarter}.idx")
        
        result = encoder.build_video(output_file, index_file, show_progress=False, 
                                     auto_build_docker=False, allow_fallback=True)
        
        if os.path.exists(output_file):
            # Delete archived items
            item_ids = [t['item_id'] for t in tasks]
            conn = sqlite3.connect(self.db_path)
            placeholders = ','.join(['?'] * len(item_ids))
            conn.execute(f"DELETE FROM list_items WHERE id IN ({placeholders}) AND completed = 1", item_ids)
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "archived": len(tasks),
                "file": output_file,
                "size_mb": os.path.getsize(output_file) / (1024*1024)
            }
        
        return {"success": False, "error": "Video not created"}
    
    async def archive_behavioral_patterns_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive discovered behavioral patterns (lights at 8pm, milk weekly, etc.)
        
        What's archived: Pattern insights, learned behaviors, recurring actions
        What stays active: Current active patterns
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = f"{year}-{start_month:02d}-01"
        end_date = f"{year}-{end_month:02d}-31"
        
        # Get pattern insights
        cursor.execute("""
            SELECT id, user_id, pattern_type, pattern_description, frequency, 
                   last_seen, examples, created_at
            FROM pattern_insights
            WHERE created_at BETWEEN ? AND ?
            ORDER BY created_at
        """, (start_date, end_date))
        
        patterns = []
        for row in cursor.fetchall():
            patterns.append({
                "pattern_id": row[0],
                "user_id": row[1],
                "pattern_type": row[2],
                "pattern_description": row[3],
                "frequency": row[4],
                "last_seen": row[5],
                "examples": row[6],
                "discovered_at": row[7],
                "data_type": "behavioral_pattern",
                "quarter": f"{year}-Q{quarter}"
            })
        
        conn.close()
        
        if not patterns:
            return {"archived": 0, "message": "No patterns to archive", "dry_run": dry_run}
        
        if dry_run:
            return {"dry_run": True, "would_archive": len(patterns)}
        
        # Encode patterns
        encoder = MemvidEncoder(enable_docker=False)
        full_text = "\n\n".join([json.dumps(p) for p in patterns])
        encoder.add_text(full_text)
        
        output_file = str(self.archive_dir / f"patterns-{year}-Q{quarter}.mp4")
        index_file = str(self.archive_dir / f"patterns-{year}-Q{quarter}.idx")
        
        encoder.build_video(output_file, index_file, show_progress=False, 
                           auto_build_docker=False, allow_fallback=True)
        
        if os.path.exists(output_file):
            return {
                "success": True,
                "archived": len(patterns),
                "file": output_file,
                "size_mb": os.path.getsize(output_file) / (1024*1024)
            }
        
        return {"success": False}
    
    async def archive_all_data_types(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive ALL historical data types for comprehensive learning
        
        Archives ONLY historical/completed data (>90 days old):
        - Completed chats (ALL - become historical)
        - Journal entries (keep recent 90 days active)
        - Completed tasks (keep active tasks in SQLite)
        - Behavioral patterns discovered
        - Past events (keep future events active)
        """
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Archiving {year}-Q{quarter}...")
        
        results = {}
        
        # Archive each data type (Phases 6A + 6D)
        results['chats'] = await self.archive_chats_quarterly(year, quarter, dry_run)
        results['journals'] = await self.archive_journal_entries_quarterly(year, quarter, dry_run)
        results['tasks'] = await self.archive_task_completions_quarterly(year, quarter, dry_run)
        results['patterns'] = await self.archive_behavioral_patterns_quarterly(year, quarter, dry_run)
        
        # Multi-modal archives (Phase 6D)
        results['photos'] = await self.archive_photos_metadata_quarterly(year, quarter, dry_run)
        results['voice'] = await self.archive_voice_interactions_quarterly(year, quarter, dry_run)
        results['home_events'] = await self.archive_home_events_quarterly(year, quarter, dry_run)
        
        # Calculate totals
        total_archived = sum(r.get('archived', r.get('would_archive', 0)) for r in results.values())
        total_size_mb = sum(r.get('size_mb', 0) for r in results.values())
        
        return {
            "quarter": f"{year}-Q{quarter}",
            "dry_run": dry_run,
            "total_items": total_archived,
            "total_size_mb": round(total_size_mb, 2),
            "by_type": results,
            "archives_created": sum(1 for r in results.values() if r.get('success'))
        }
    
    def list_archives(self) -> List[Dict]:
        """List all available memvid archives"""
        archives = []
        
        for video_file in self.archive_dir.glob("*.mp4"):
            index_file = video_file.with_suffix('.idx')
            
            archives.append({
                "video_file": str(video_file),
                "index_file": str(index_file) if index_file.exists() else None,
                "name": video_file.stem,
                "size_mb": round(video_file.stat().st_size / (1024*1024), 2),
                "created": datetime.fromtimestamp(video_file.stat().st_mtime).isoformat()
            })
        
        return sorted(archives, key=lambda x: x['name'], reverse=True)
    
    async def search_archives(self, query: str, archive_types: List[str] = None, 
                            user_id: str = None, top_k: int = 10) -> List[Dict]:
        """
        Search across memvid archives for patterns
        
        Args:
            query: Search query
            archive_types: Filter by type (chats, journals, tasks, patterns)
            user_id: Filter by user
            top_k: Max results
            
        Returns:
            List of matching results from archives
        """
        results = []
        
        # Get relevant archives
        if archive_types:
            video_files = []
            for atype in archive_types:
                video_files.extend(self.archive_dir.glob(f"{atype}-*.mp4"))
        else:
            video_files = list(self.archive_dir.glob("*.mp4"))
        
        # Search each archive
        for video_file in video_files:
            index_file = video_file.with_suffix('.idx')
            if not index_file.exists():
                continue
            
            try:
                retriever = MemvidRetriever(str(video_file))
                matches = retriever.search(query, top_k=top_k)
                
                for match in matches:
                    # Parse JSON content
                    try:
                        data = json.loads(match['text'])
                        
                        # Filter by user_id if specified
                        if user_id and data.get('user_id') != user_id:
                            continue
                        
                        results.append({
                            "archive": video_file.name,
                            "score": match.get('score', 0),
                            "data": data,
                            "data_type": data.get('data_type', 'unknown')
                        })
                    except json.JSONDecodeError:
                        pass
            
            except Exception as e:
                logger.error(f"Error searching {video_file}: {e}")
        
        # Sort by score
        results.sort(key=lambda x: x['score'], reverse=True)
        
        return results[:top_k]
    
    def get_archive_stats(self) -> Dict:
        """Get statistics about all archives"""
        archives = self.list_archives()
        
        total_size_mb = sum(a['size_mb'] for a in archives)
        by_type = {}
        
        for archive in archives:
            atype = archive['name'].split('-')[0]
            if atype not in by_type:
                by_type[atype] = {"count": 0, "total_size_mb": 0}
            by_type[atype]["count"] += 1
            by_type[atype]["total_size_mb"] += archive['size_mb']
        
        return {
            "total_archives": len(archives),
            "total_size_mb": round(total_size_mb, 2),
            "by_type": by_type,
            "archives": archives
        }
    
    async def archive_photos_metadata_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive photo metadata (Phase 6D: Multi-Modal)
        
        What's archived: Photo metadata (location, people, journal links, timestamps)
        What stays: Actual image files (archived separately if needed)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = f"{year}-{start_month:02d}-01"
        end_date = f"{year}-{end_month:02d}-31"
        
        cutoff_date = (datetime.now() - timedelta(days=self.archive_threshold_days)).isoformat()
        
        cursor.execute("""
            SELECT id, user_id, file_path, thumbnail_path, original_filename,
                   exif_data, created_at, size_bytes, format
            FROM uploaded_photos
            WHERE created_at BETWEEN ? AND ?
              AND created_at < ?
            ORDER BY created_at
        """, (start_date, end_date, cutoff_date))
        
        photos = []
        for row in cursor.fetchall():
            exif_data = json.loads(row[5]) if row[5] else {}
            photos.append({
                "photo_id": row[0],
                "user_id": row[1],
                "file_path": row[2],
                "thumbnail_path": row[3],
                "original_filename": row[4],
                "exif_data": exif_data,
                "timestamp": row[6],
                "size_bytes": row[7],
                "format": row[8],
                "data_type": "photo_metadata",
                "quarter": f"{year}-Q{quarter}"
            })
        
        conn.close()
        
        if not photos:
            return {"archived": 0, "message": "No photos to archive", "dry_run": dry_run}
        
        if dry_run:
            return {"dry_run": True, "would_archive": len(photos)}
        
        # Archive metadata only
        encoder = MemvidEncoder(enable_docker=False)
        full_text = "\n\n".join([json.dumps(p) for p in photos])
        encoder.add_text(full_text)
        
        output_file = str(self.archive_dir / f"photos-metadata-{year}-Q{quarter}.mp4")
        index_file = str(self.archive_dir / f"photos-metadata-{year}-Q{quarter}.idx")
        
        encoder.build_video(output_file, index_file, show_progress=False, 
                           auto_build_docker=False, allow_fallback=True)
        
        if os.path.exists(output_file):
            return {
                "success": True,
                "archived": len(photos),
                "file": output_file,
                "size_mb": os.path.getsize(output_file) / (1024*1024),
                "note": "Metadata archived - image files preserved separately"
            }
        
        return {"success": False}
    
    async def archive_voice_interactions_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive voice interaction transcriptions (Phase 6D: Multi-Modal)
        
        What's archived: Transcriptions, timestamps, success rates
        What stays: Audio files can be archived separately if needed
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = f"{year}-{start_month:02d}-01"
        end_date = f"{year}-{end_month:02d}-31"
        
        cutoff_date = (datetime.now() - timedelta(days=self.archive_threshold_days)).isoformat()
        
        # Check if agent_messages table exists (voice transcriptions)
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='agent_messages'
        """)
        
        if not cursor.fetchone():
            conn.close()
            return {"archived": 0, "message": "Voice messages table not found", "dry_run": dry_run}
        
        cursor.execute("""
            SELECT id, role, content, created_at
            FROM agent_messages
            WHERE created_at BETWEEN ? AND ?
              AND created_at < ?
            ORDER BY created_at
        """, (start_date, end_date, cutoff_date))
        
        voice_interactions = []
        for row in cursor.fetchall():
            voice_interactions.append({
                "message_id": row[0],
                "role": row[1],
                "transcription": row[2],
                "timestamp": row[3],
                "data_type": "voice_interaction",
                "quarter": f"{year}-Q{quarter}"
            })
        
        conn.close()
        
        if not voice_interactions:
            return {"archived": 0, "message": "No voice interactions to archive", "dry_run": dry_run}
        
        if dry_run:
            return {"dry_run": True, "would_archive": len(voice_interactions)}
        
        # Archive transcriptions
        encoder = MemvidEncoder(enable_docker=False)
        full_text = "\n\n".join([json.dumps(v) for v in voice_interactions])
        encoder.add_text(full_text)
        
        output_file = str(self.archive_dir / f"voice-{year}-Q{quarter}.mp4")
        index_file = str(self.archive_dir / f"voice-{year}-Q{quarter}.idx")
        
        encoder.build_video(output_file, index_file, show_progress=False, 
                           auto_build_docker=False, allow_fallback=True)
        
        if os.path.exists(output_file):
            # Delete archived voice messages
            message_ids = [v['message_id'] for v in voice_interactions]
            conn = sqlite3.connect(self.db_path)
            placeholders = ','.join(['?'] * len(message_ids))
            conn.execute(f"DELETE FROM agent_messages WHERE id IN ({placeholders})", message_ids)
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "archived": len(voice_interactions),
                "file": output_file,
                "size_mb": os.path.getsize(output_file) / (1024*1024)
            }
        
        return {"success": False}
    
    async def archive_home_events_quarterly(self, year: int, quarter: int, dry_run: bool = True) -> Dict:
        """
        Archive home automation patterns (Phase 6D: Multi-Modal)
        
        What's archived: Device state changes, automation triggers, user patterns
        What stays: Current device states
        """
        # Placeholder - would integrate with HomeAssistant event log
        # For now, return structure for future implementation
        return {
            "archived": 0,
            "message": "Home event archival - ready for integration with HomeAssistant logs",
            "dry_run": dry_run,
            "note": "Will archive: device states, automation triggers, usage patterns"
        }
    
    def _calculate_hours(self, start: str, end: str) -> float:
        """Calculate hours between two timestamps"""
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            return (end_dt - start_dt).total_seconds() / 3600
        except:
            return 0.0


# Global instance
archiver = MemvidArchiver()

