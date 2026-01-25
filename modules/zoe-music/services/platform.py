"""
Platform detection for music module.
Provides hardware detection independent of zoe-core.
"""
import os
import platform as sys_platform
import logging

logger = logging.getLogger(__name__)


def detect_hardware():
    """
    Detect hardware platform.
    
    Returns:
        str: Platform identifier ('jetson', 'pi5', 'pi', or 'unknown')
    """
    # Check environment variable first (preferred method)
    env_platform = os.getenv("PLATFORM", "").lower()
    if env_platform in ["jetson", "pi5", "pi"]:
        logger.info(f"Platform detected from environment: {env_platform}")
        return env_platform
    
    # Auto-detect from system
    machine = sys_platform.machine().lower()
    system = sys_platform.system().lower()
    
    logger.debug(f"System detection: machine={machine}, system={system}")
    
    # Check for ARM/AArch64 (Jetson or Pi)
    if "aarch64" in machine or "arm" in machine:
        # Check for NVIDIA Jetson-specific files
        if os.path.exists("/etc/nv_tegra_release"):
            logger.info("Platform detected: Jetson (found /etc/nv_tegra_release)")
            return "jetson"
        
        # Check for Raspberry Pi specific files
        if os.path.exists("/proc/device-tree/model"):
            try:
                with open("/proc/device-tree/model", "r") as f:
                    model = f.read().lower()
                    if "raspberry pi 5" in model:
                        logger.info("Platform detected: Raspberry Pi 5")
                        return "pi5"
                    elif "raspberry pi" in model:
                        logger.info("Platform detected: Raspberry Pi")
                        return "pi"
            except Exception as e:
                logger.warning(f"Failed to read Pi model: {e}")
        
        # Default ARM to pi5
        logger.info("Platform detected: ARM architecture, assuming Pi5")
        return "pi5"
    
    # Not ARM architecture
    logger.info("Platform: Unknown (not ARM/AArch64)")
    return "unknown"


def get_platform_capabilities():
    """
    Get platform-specific capabilities.
    
    Returns:
        dict: Platform capabilities configuration
    """
    platform = detect_hardware()
    
    if platform == "jetson":
        return {
            "platform": "jetson",
            "ml_enabled": True,
            "audio_bitrate": "256",
            "audio_format": "opus",
            "gpu_acceleration": True,
            "max_concurrent_streams": 4
        }
    elif platform in ["pi5", "pi"]:
        return {
            "platform": platform,
            "ml_enabled": False,
            "audio_bitrate": "128",
            "audio_format": "opus",
            "gpu_acceleration": False,
            "max_concurrent_streams": 2
        }
    else:
        return {
            "platform": "unknown",
            "ml_enabled": False,
            "audio_bitrate": "192",
            "audio_format": "opus",
            "gpu_acceleration": False,
            "max_concurrent_streams": 3
        }


# Export for compatibility with model_config
PLATFORM = detect_hardware()
CAPABILITIES = get_platform_capabilities()

logger.info(f"Platform initialized: {PLATFORM}, ML: {CAPABILITIES['ml_enabled']}")
