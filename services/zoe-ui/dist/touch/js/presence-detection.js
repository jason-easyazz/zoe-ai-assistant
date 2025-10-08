"use strict";

// Presence detection and auto-ambient mode inspired by MagicMirror MMM-PIR-Sensor

const PresenceDetection = (() => {
  let idleTimer = null;
  let isUserPresent = true;
  let isAmbientMode = false;
  
  const DEFAULTS = {
    idleTimeout: 30000, // 30 seconds of no activity = go ambient
    deepSleepTimeout: 300000, // 5 minutes = deep sleep
    wakeOnTouch: true,
    wakeOnMovement: false, // Set to true if PIR sensor connected
    pirSensorPin: null, // GPIO pin for PIR sensor
    debugMode: false
  };

  let config = { ...DEFAULTS };
  let callbacks = {
    onUserPresent: [],
    onUserIdle: [],
    onAmbientMode: [],
    onDeepSleep: []
  };

  function init(options = {}) {
    config = { ...DEFAULTS, ...options };
    setupEventListeners();
    resetIdleTimer();
    
    if (config.debugMode) {
      console.log('PresenceDetection initialized with config:', config);
    }
  }

  function setupEventListeners() {
    // Track user activity
    const activityEvents = ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart', 'touchmove'];
    activityEvents.forEach(event => {
      document.addEventListener(event, handleUserActivity, { passive: true });
    });

    // Custom gesture events
    document.addEventListener('gesture:tap', handleUserActivity);
    document.addEventListener('gesture:swipeleft', handleUserActivity);
    document.addEventListener('gesture:swiperight', handleUserActivity);
    
    // Voice activity
    document.addEventListener('voice:started', handleUserActivity);
    document.addEventListener('voice:command', handleUserActivity);

    // Page visibility changes
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        enterAmbientMode();
      } else {
        setUserPresent();
      }
    });

    // PIR sensor simulation (replace with actual GPIO if available)
    if (config.wakeOnMovement && config.pirSensorPin) {
      setupPIRSensor();
    }
  }

  function setupPIRSensor() {
    // Placeholder for actual PIR sensor integration
    // In real implementation, would use GPIO library to read PIR sensor
    if (config.debugMode) {
      console.log('PIR sensor would be set up on pin:', config.pirSensorPin);
      
      // Simulate random motion detection for demo
      setInterval(() => {
        if (Math.random() > 0.95) { // 5% chance every second
          handleMotionDetected();
        }
      }, 1000);
    }
  }

  function handleUserActivity() {
    if (!isUserPresent) {
      setUserPresent();
    }
    resetIdleTimer();
  }

  function handleMotionDetected() {
    if (config.debugMode) {
      console.log('Motion detected by PIR sensor');
    }
    handleUserActivity();
  }

  function resetIdleTimer() {
    if (idleTimer) clearTimeout(idleTimer);
    
    idleTimer = setTimeout(() => {
      setUserIdle();
      
      // Set up deep sleep timer
      setTimeout(() => {
        if (!isUserPresent) {
          enterDeepSleep();
        }
      }, config.deepSleepTimeout - config.idleTimeout);
      
    }, config.idleTimeout);
  }

  function setUserPresent() {
    const wasIdle = !isUserPresent;
    isUserPresent = true;
    
    if (isAmbientMode) {
      exitAmbientMode();
    }
    
    if (wasIdle) {
      triggerCallbacks('onUserPresent');
      TouchCommon.emit('presence:userPresent');
      
      if (config.debugMode) {
        console.log('User present detected');
      }
    }
  }

  function setUserIdle() {
    isUserPresent = false;
    enterAmbientMode();
    
    triggerCallbacks('onUserIdle');
    TouchCommon.emit('presence:userIdle');
    
    if (config.debugMode) {
      console.log('User idle detected');
    }
  }

  function enterAmbientMode() {
    if (isAmbientMode) return;
    
    isAmbientMode = true;
    document.body.classList.add('ambient');
    
    // Dim screen brightness if supported
    if ('screen' in navigator && 'brightness' in navigator.screen) {
      try {
        navigator.screen.brightness = 0.3;
      } catch (e) {
        // Brightness control not supported
      }
    }
    
    triggerCallbacks('onAmbientMode');
    TouchCommon.emit('presence:ambientMode');
    
    if (config.debugMode) {
      console.log('Entered ambient mode');
    }
  }

  function exitAmbientMode() {
    if (!isAmbientMode) return;
    
    isAmbientMode = false;
    document.body.classList.remove('ambient');
    
    // Restore screen brightness
    if ('screen' in navigator && 'brightness' in navigator.screen) {
      try {
        navigator.screen.brightness = 1.0;
      } catch (e) {
        // Brightness control not supported
      }
    }
    
    TouchCommon.emit('presence:exitAmbient');
    
    if (config.debugMode) {
      console.log('Exited ambient mode');
    }
  }

  function enterDeepSleep() {
    document.body.classList.add('deep-sleep');
    
    // Turn off screen if supported (Pi-specific)
    if (config.debugMode) {
      console.log('Would turn off screen for deep sleep');
    }
    
    triggerCallbacks('onDeepSleep');
    TouchCommon.emit('presence:deepSleep');
  }

  function triggerCallbacks(type) {
    callbacks[type].forEach(callback => {
      try {
        callback();
      } catch (e) {
        console.error('Presence callback error:', e);
      }
    });
  }

  function on(event, callback) {
    if (callbacks[event]) {
      callbacks[event].push(callback);
    }
  }

  function off(event, callback) {
    if (callbacks[event]) {
      const index = callbacks[event].indexOf(callback);
      if (index > -1) {
        callbacks[event].splice(index, 1);
      }
    }
  }

  // Manual controls
  function forceAmbient() { enterAmbientMode(); }
  function forceActive() { setUserPresent(); }
  function toggleAmbient() { isAmbientMode ? exitAmbientMode() : enterAmbientMode(); }

  return {
    init,
    on,
    off,
    forceAmbient,
    forceActive,
    toggleAmbient,
    get isPresent() { return isUserPresent; },
    get isAmbient() { return isAmbientMode; }
  };
})();

window.PresenceDetection = PresenceDetection;
