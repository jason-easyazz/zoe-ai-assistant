"use strict";

// Family photo slideshow system inspired by MagicMirror MMM-BackgroundSlideshow

const PhotoSlideshow = (() => {
  let photos = [];
  let currentIndex = 0;
  let slideTimer = null;
  let preloadedImages = new Map();
  
  const DEFAULTS = {
    interval: 15000, // 15 seconds
    transition: 2000, // 2 second fade
    photoDirectory: './photos/', // Default photo directory
    fallbackGradients: [
      'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)', // Primary Zoe gradient
      'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', // Secondary Zoe gradient
      'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)', // Zoe background
      'linear-gradient(135deg, #7B61FF 30%, #667eea 70%)', // Purple variation
      'linear-gradient(135deg, #5AE0E0 30%, #4facfe 70%)', // Teal variation
      'linear-gradient(135deg, #7B61FF 0%, #667eea 50%, #5AE0E0 100%)', // Full spectrum
      'linear-gradient(135deg, #1a1a2e 0%, #7B61FF 50%, #5AE0E0 100%)', // Dark to brand
      'linear-gradient(135deg, #0f3460 0%, #16213e 50%, #7B61FF 100%)', // Blue to purple
    ]
  };

  let config = { ...DEFAULTS };
  let backdrop = null;

  function init(options = {}) {
    config = { ...DEFAULTS, ...options };
    backdrop = document.querySelector('.photo-backdrop') || createBackdrop();
    loadPhotoList();
    start();
  }

  function createBackdrop() {
    const el = document.createElement('div');
    el.className = 'photo-backdrop';
    document.body.prepend(el);
    return el;
  }

  async function loadPhotoList() {
    try {
      // Try to fetch photo list from server
      const response = await fetch(config.photoDirectory + 'list.json');
      if (response.ok) {
        const data = await response.json();
        photos = data.photos || [];
      }
    } catch (e) {
      console.log('No photo list found, trying journal photos');
    }
    
    // Try to get photos from journal entries
    if (photos.length === 0) {
      try {
        await loadJournalPhotos();
      } catch (e) {
        console.log('No journal photos found');
      }
    }
    
    // Fallback to gradients if no photos
    if (photos.length === 0) {
      photos = config.fallbackGradients.map(gradient => ({ type: 'gradient', value: gradient }));
    } else {
      photos = photos.map(photo => ({ type: 'image', value: photo.startsWith('http') ? photo : config.photoDirectory + photo }));
    }
    
    preloadNextImages();
  }

  async function loadJournalPhotos() {
    try {
      // Fetch recent journal entries with photos from Zoe's backend
      const response = await TouchCommon.fetchJSON('/api/journal/photos?limit=20');
      if (response.photos && response.photos.length > 0) {
        photos = response.photos.map(photo => ({
          type: 'image',
          value: photo.url,
          caption: photo.caption || '',
          date: photo.date || ''
        }));
        console.log(`Loaded ${photos.length} journal photos for slideshow`);
      }
    } catch (e) {
      console.log('Journal photos API not available:', e.message);
      // Try local journal photo directory
      try {
        const response = await fetch('./photos/journal/list.json');
        if (response.ok) {
          const data = await response.json();
          photos = data.photos || [];
        }
      } catch (e) {
        console.log('Local journal photos not found');
      }
    }
  }

  function preloadNextImages() {
    // Preload next 3 images for smooth transitions
    for (let i = 1; i <= 3; i++) {
      const nextIndex = (currentIndex + i) % photos.length;
      const photo = photos[nextIndex];
      if (photo.type === 'image' && !preloadedImages.has(photo.value)) {
        const img = new Image();
        img.onload = () => preloadedImages.set(photo.value, img);
        img.src = photo.value;
      }
    }
  }

  function showNext() {
    if (photos.length === 0) return;
    
    const photo = photos[currentIndex];
    
    if (photo.type === 'gradient') {
      backdrop.style.background = photo.value;
      backdrop.style.backgroundImage = '';
    } else {
      backdrop.style.background = '';
      backdrop.style.backgroundImage = `url(${photo.value})`;
    }
    
    // Trigger CSS transition
    backdrop.style.opacity = '0';
    setTimeout(() => {
      backdrop.style.opacity = '0.7';
    }, 50);
    
    currentIndex = (currentIndex + 1) % photos.length;
    preloadNextImages();
  }

  function start() {
    if (slideTimer) clearInterval(slideTimer);
    showNext();
    slideTimer = setInterval(showNext, config.interval);
  }

  function stop() {
    if (slideTimer) {
      clearInterval(slideTimer);
      slideTimer = null;
    }
  }

  function setPhotos(newPhotos) {
    photos = newPhotos.map(photo => ({ type: 'image', value: photo }));
    currentIndex = 0;
    preloadedImages.clear();
    preloadNextImages();
    if (slideTimer) showNext();
  }

  function setGradients(gradients) {
    photos = gradients.map(gradient => ({ type: 'gradient', value: gradient }));
    currentIndex = 0;
    if (slideTimer) showNext();
  }

  function pause() { stop(); }
  function resume() { start(); }

  return {
    init,
    start,
    stop,
    pause,
    resume,
    setPhotos,
    setGradients,
    showNext
  };
})();

window.PhotoSlideshow = PhotoSlideshow;
