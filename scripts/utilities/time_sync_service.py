#!/usr/bin/env python3
"""
Time synchronization service for Zoe
Automatically syncs time and manages timezone settings
"""

import sys
import os
import time
import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Add the zoe-core directory to the path
sys.path.append(str(PROJECT_ROOT / "services/zoe-core"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(PROJECT_ROOT / "logs/time_sync.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TimeSyncService:
    def __init__(self):
        self.settings_file = PROJECT_ROOT / "data/time_settings.json"
        self.log_file = PROJECT_ROOT / "logs/time_sync.log"
        self.running = False
        
        # Ensure directories exist
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load settings
        self.settings = self.load_settings()
        
    def load_settings(self):
        """Load time synchronization settings"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load settings: {e}")
        
        # Default settings
        return {
            "timezone": "UTC",
            "ntp_servers": ["pool.ntp.org", "time.nist.gov", "time.google.com"],
            "sync_interval": 3600,  # 1 hour
            "auto_sync": True,
            "last_sync": None,
            "sync_attempts": 0,
            "location": None
        }
    
    def save_settings(self):
        """Save settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
    
    def sync_with_ntp(self, ntp_server="pool.ntp.org"):
        """Synchronize system time with NTP server"""
        try:
            logger.info(f"Attempting to sync with {ntp_server}")
            
            # Try timedatectl first (systemd systems)
            result = subprocess.run([
                "sudo", "timedatectl", "set-ntp", "true"
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("Successfully enabled NTP sync via timedatectl")
                return True
            else:
                logger.warning(f"timedatectl failed: {result.stderr}")
                
                # Fallback: try ntpdate
                result = subprocess.run([
                    "sudo", "ntpdate", "-s", ntp_server
                ], capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    logger.info(f"Successfully synced with {ntp_server} via ntpdate")
                    return True
                else:
                    logger.error(f"ntpdate failed: {result.stderr}")
                    return False
                    
        except subprocess.TimeoutExpired:
            logger.error("NTP sync timed out")
            return False
        except Exception as e:
            logger.error(f"NTP sync error: {e}")
            return False
    
    def set_timezone(self, timezone):
        """Set system timezone"""
        try:
            result = subprocess.run([
                "sudo", "timedatectl", "set-timezone", timezone
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                logger.info(f"Successfully set timezone to {timezone}")
                return True
            else:
                logger.error(f"Failed to set timezone: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Timezone setting error: {e}")
            return False
    
    def get_current_time_info(self):
        """Get current time information"""
        try:
            now = datetime.now()
            
            # Get timezone info
            result = subprocess.run([
                "timedatectl", "show", "--property=Timezone"
            ], capture_output=True, text=True, timeout=5)
            
            timezone = "UTC"
            if result.returncode == 0:
                timezone = result.stdout.strip().split("=")[1]
            
            # Get NTP status
            result = subprocess.run([
                "timedatectl", "show", "--property=NTPSynchronized"
            ], capture_output=True, text=True, timeout=5)
            
            ntp_synced = False
            if result.returncode == 0:
                ntp_synced = result.stdout.strip().split("=")[1] == "yes"
            
            return {
                "current_time": now.isoformat(),
                "timezone": timezone,
                "ntp_synced": ntp_synced,
                "unix_timestamp": int(now.timestamp())
            }
        except Exception as e:
            logger.error(f"Failed to get time info: {e}")
            return {
                "current_time": datetime.now().isoformat(),
                "timezone": "UTC",
                "ntp_synced": False,
                "unix_timestamp": int(time.time())
            }
    
    def sync_time(self):
        """Perform time synchronization"""
        if not self.settings.get("auto_sync", True):
            logger.info("Auto sync is disabled")
            return False
        
        ntp_servers = self.settings.get("ntp_servers", ["pool.ntp.org"])
        sync_success = False
        
        for server in ntp_servers:
            if self.sync_with_ntp(server):
                sync_success = True
                break
        
        # Update settings
        self.settings["last_sync"] = datetime.now().isoformat()
        self.settings["sync_attempts"] = self.settings.get("sync_attempts", 0) + 1
        self.save_settings()
        
        if sync_success:
            logger.info("Time synchronization completed successfully")
        else:
            logger.error("Time synchronization failed")
        
        return sync_success
    
    def apply_timezone(self):
        """Apply the configured timezone"""
        timezone = self.settings.get("timezone", "UTC")
        if timezone != "UTC":
            self.set_timezone(timezone)
    
    def start_service(self):
        """Start the time synchronization service"""
        logger.info("Starting Time Sync Service")
        self.running = True
        
        # Initial sync
        self.sync_time()
        self.apply_timezone()
        
        # Main loop
        while self.running:
            try:
                # Wait for sync interval
                sync_interval = self.settings.get("sync_interval", 3600)
                logger.info(f"Waiting {sync_interval} seconds until next sync")
                
                for _ in range(sync_interval):
                    if not self.running:
                        break
                    time.sleep(1)
                
                if self.running:
                    self.sync_time()
                    
                    # Reload settings in case they changed
                    self.settings = self.load_settings()
                    
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                self.stop_service()
            except Exception as e:
                logger.error(f"Service error: {e}")
                time.sleep(60)  # Wait 1 minute before retrying
    
    def stop_service(self):
        """Stop the time synchronization service"""
        logger.info("Stopping Time Sync Service")
        self.running = False
    
    def status(self):
        """Get service status"""
        time_info = self.get_current_time_info()
        return {
            "running": self.running,
            "settings": self.settings,
            "current_time": time_info["current_time"],
            "timezone": time_info["timezone"],
            "ntp_synced": time_info["ntp_synced"]
        }

def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Zoe Time Sync Service")
    parser.add_argument("command", choices=["start", "stop", "status", "sync"], 
                       help="Command to execute")
    
    args = parser.parse_args()
    
    service = TimeSyncService()
    
    if args.command == "start":
        service.start_service()
    elif args.command == "stop":
        service.stop_service()
    elif args.command == "status":
        status = service.status()
        print(json.dumps(status, indent=2))
    elif args.command == "sync":
        success = service.sync_time()
        print(f"Sync {'successful' if success else 'failed'}")

if __name__ == "__main__":
    main()



