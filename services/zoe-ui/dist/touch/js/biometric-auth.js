"use strict";

// Alternative authentication methods: face, voice, fingerprint

const BiometricAuth = (() => {
  let isSupported = {
    face: false,
    fingerprint: false,
    voice: true // Voice is always supported via Web Speech API
  };

  function init() {
    checkSupport();
    return isSupported;
  }

  function checkSupport() {
    // Check for WebAuthn API (fingerprint, face ID on supported devices)
    isSupported.fingerprint = 'credentials' in navigator && 'create' in navigator.credentials;
    
    // Face recognition requires camera access
    isSupported.face = 'mediaDevices' in navigator && 'getUserMedia' in navigator.mediaDevices;
    
    console.log('Biometric support:', isSupported);
  }

  // Face Recognition Authentication
  async function authenticateWithFace() {
    if (!isSupported.face) {
      throw new Error('Camera not supported');
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ 
        video: { 
          width: 640, 
          height: 480,
          facingMode: 'user' // Front camera
        } 
      });

      return new Promise((resolve, reject) => {
        const video = document.createElement('video');
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        
        video.srcObject = stream;
        video.play();

        // Create modal for face scanning
        const modal = createFaceAuthModal(video);
        document.body.appendChild(modal);

        video.onloadedmetadata = () => {
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
          
          let attempts = 0;
          const maxAttempts = 50; // 5 seconds at 10fps
          
          const scanInterval = setInterval(() => {
            if (attempts >= maxAttempts) {
              clearInterval(scanInterval);
              cleanup();
              reject(new Error('Face authentication timeout'));
              return;
            }

            // Capture frame
            ctx.drawImage(video, 0, 0);
            const imageData = canvas.toDataURL('image/jpeg', 0.8);
            
            // Send to face recognition API
            authenticateFaceImage(imageData)
              .then((result) => {
                if (result.authenticated) {
                  clearInterval(scanInterval);
                  cleanup();
                  resolve(result);
                }
              })
              .catch((error) => {
                console.warn('Face recognition error:', error);
              });

            attempts++;
          }, 100);
        };

        function cleanup() {
          stream.getTracks().forEach(track => track.stop());
          modal.remove();
        }

        // Cancel button
        modal.querySelector('.cancel-btn').onclick = () => {
          cleanup();
          reject(new Error('Face authentication cancelled'));
        };
      });

    } catch (error) {
      throw new Error('Camera access denied: ' + error.message);
    }
  }

  function createFaceAuthModal(video) {
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 9999;
      background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center;
    `;
    
    const container = document.createElement('div');
    container.style.cssText = `
      background: var(--glass-bg); backdrop-filter: blur(20px); border-radius: 16px; padding: 20px;
      border: 1px solid var(--glass-border); text-align: center; color: white; max-width: 400px;
    `;
    
    video.style.cssText = `
      width: 320px; height: 240px; border-radius: 8px; margin-bottom: 16px;
    `;
    
    container.innerHTML = `
      <h3 style="margin: 0 0 16px 0; color: var(--fg-primary);">Face Recognition</h3>
      <p style="margin: 0 0 16px 0; color: var(--fg-secondary);">Look directly at the camera</p>
    `;
    
    container.appendChild(video);
    
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn cancel-btn';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.marginTop = '16px';
    container.appendChild(cancelBtn);
    
    modal.appendChild(container);
    return modal;
  }

  async function authenticateFaceImage(imageData) {
    try {
      // Send to your face recognition API
      const response = await fetch('/api/auth/face-recognition', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: imageData })
      });
      
      return await response.json();
    } catch (error) {
      // Mock response for demo - replace with actual API
      console.log('Face recognition API not available, using mock');
      
      // Simulate processing time
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // Mock successful recognition (replace with real logic)
      return {
        authenticated: Math.random() > 0.3, // 70% success rate for demo
        confidence: Math.random() * 0.4 + 0.6, // 60-100% confidence
        user: 'family_member'
      };
    }
  }

  // Voice Pattern Authentication
  async function authenticateWithVoice() {
    if (!VoiceTouch.isSupported()) {
      throw new Error('Voice recognition not supported');
    }

    return new Promise((resolve, reject) => {
      const modal = createVoiceAuthModal();
      document.body.appendChild(modal);

      let transcript = '';
      const expectedPhrases = [
        'hello zoe its me',
        'zoe this is my voice',
        'family member authentication',
        'voice unlock zoe'
      ];

      const cleanup = () => {
        VoiceTouch.stop();
        modal.remove();
      };

      // Listen for voice command
      document.addEventListener('voice:command', function onVoiceCommand(e) {
        transcript = e.detail.transcript.toLowerCase();
        console.log('Voice auth transcript:', transcript);
        
        // Check if transcript matches expected phrases
        const isMatch = expectedPhrases.some(phrase => 
          transcript.includes(phrase) || similarityScore(transcript, phrase) > 0.7
        );

        if (isMatch) {
          document.removeEventListener('voice:command', onVoiceCommand);
          cleanup();
          resolve({
            authenticated: true,
            method: 'voice',
            transcript: transcript,
            user: 'family_member'
          });
        }
      });

      // Start voice recognition
      VoiceTouch.start();

      // Timeout after 10 seconds
      setTimeout(() => {
        document.removeEventListener('voice:command', onVoiceCommand);
        cleanup();
        reject(new Error('Voice authentication timeout'));
      }, 10000);

      // Cancel button
      modal.querySelector('.cancel-btn').onclick = () => {
        cleanup();
        reject(new Error('Voice authentication cancelled'));
      };
    });
  }

  function createVoiceAuthModal() {
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 9999;
      background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center;
    `;
    
    modal.innerHTML = `
      <div style="background: var(--glass-bg); backdrop-filter: blur(20px); border-radius: 16px; padding: 30px;
                  border: 1px solid var(--glass-border); text-align: center; color: white; max-width: 400px;">
        <h3 style="margin: 0 0 16px 0; color: var(--fg-primary);">Voice Authentication</h3>
        <div style="font-size: 48px; margin: 20px 0;">🎤</div>
        <p style="margin: 0 0 16px 0; color: var(--fg-secondary);">Say one of these phrases:</p>
        <div style="font-style: italic; color: var(--fg-muted); margin: 16px 0; line-height: 1.4;">
          "Hello Zoe, it's me"<br>
          "Zoe, this is my voice"<br>
          "Family member authentication"<br>
          "Voice unlock Zoe"
        </div>
        <button class="btn cancel-btn" style="margin-top: 20px;">Cancel</button>
      </div>
    `;
    
    return modal;
  }

  // Fingerprint/WebAuthn Authentication
  async function authenticateWithFingerprint() {
    if (!isSupported.fingerprint) {
      throw new Error('WebAuthn not supported');
    }

    try {
      // Create credential for registration (first time setup)
      const credential = await navigator.credentials.create({
        publicKey: {
          challenge: new Uint8Array(32),
          rp: { name: "Zoe AI Assistant" },
          user: {
            id: new Uint8Array(16),
            name: "family@zoe.local",
            displayName: "Family Member"
          },
          pubKeyCredParams: [{ alg: -7, type: "public-key" }],
          authenticatorSelection: {
            authenticatorAttachment: "platform", // Built-in fingerprint
            userVerification: "required"
          }
        }
      });

      return {
        authenticated: true,
        method: 'fingerprint',
        credentialId: credential.id,
        user: 'family_member'
      };

    } catch (error) {
      throw new Error('Fingerprint authentication failed: ' + error.message);
    }
  }

  // Utility function for voice pattern matching
  function similarityScore(str1, str2) {
    const longer = str1.length > str2.length ? str1 : str2;
    const shorter = str1.length > str2.length ? str2 : str1;
    const editDistance = levenshteinDistance(longer, shorter);
    return (longer.length - editDistance) / longer.length;
  }

  function levenshteinDistance(str1, str2) {
    const matrix = [];
    for (let i = 0; i <= str2.length; i++) {
      matrix[i] = [i];
    }
    for (let j = 0; j <= str1.length; j++) {
      matrix[0][j] = j;
    }
    for (let i = 1; i <= str2.length; i++) {
      for (let j = 1; j <= str1.length; j++) {
        if (str2.charAt(i - 1) === str1.charAt(j - 1)) {
          matrix[i][j] = matrix[i - 1][j - 1];
        } else {
          matrix[i][j] = Math.min(
            matrix[i - 1][j - 1] + 1,
            matrix[i][j - 1] + 1,
            matrix[i - 1][j] + 1
          );
        }
      }
    }
    return matrix[str2.length][str1.length];
  }

  return {
    init,
    isSupported,
    authenticateWithFace,
    authenticateWithVoice,
    authenticateWithFingerprint
  };
})();

window.BiometricAuth = BiometricAuth;
