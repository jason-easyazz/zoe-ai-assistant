# Voice AI Deployment Checklist

Complete this checklist to deploy Zoe's new voice capabilities.

## ☐ Pre-Deployment

- [ ] **Backup current system**
  ```bash
  docker-compose down
  cd /home/pi
  tar -czf zoe-backup-$(date +%Y%m%d).tar.gz zoe/
  ```

- [ ] **Check disk space** (need ~5GB for models)
  ```bash
  df -h /home/pi
  ```

- [ ] **Check available RAM** (need ~4GB free)
  ```bash
  free -h
  ```

## ☐ Download Dependencies

- [ ] **Download sample voice files**
  ```bash
  cd /home/pi/zoe/services/zoe-tts/samples
  wget https://raw.githubusercontent.com/neuphonic/neutts-air/main/samples/dave.wav
  wget https://raw.githubusercontent.com/neuphonic/neutts-air/main/samples/jo.wav
  # Verify
  ls -lh dave.wav jo.wav
  ```

## ☐ Configuration

- [ ] **Set LiveKit API keys** (optional, defaults work)
  ```bash
  nano /home/pi/zoe/.env
  # Add:
  # LIVEKIT_API_KEY=devkey
  # LIVEKIT_API_SECRET=secret
  ```

- [ ] **Verify docker-compose.yml updated**
  ```bash
  grep -A 5 "livekit:" /home/pi/zoe/docker-compose.yml
  # Should show livekit service configuration
  ```

## ☐ Build Services

- [ ] **Stop conflicting services**
  ```bash
  docker-compose stop zoe-tts
  ```

- [ ] **Build TTS service** (this will take 10-15 minutes)
  ```bash
  docker-compose build zoe-tts
  ```

- [ ] **Build voice agent service** (5 minutes)
  ```bash
  docker-compose build zoe-voice-agent
  ```

- [ ] **Rebuild zoe-core** (2 minutes)
  ```bash
  docker-compose build zoe-core
  ```

## ☐ Start Services

- [ ] **Start all services**
  ```bash
  docker-compose up -d
  ```

- [ ] **Wait for services to initialize** (2-3 minutes for model downloads)
  ```bash
  docker-compose logs -f zoe-tts | grep "voice profiles"
  # Wait for: "✅ Loaded X voice profiles"
  ```

## ☐ Verify Installation

- [ ] **Check TTS health**
  ```bash
  curl http://localhost:9002/health
  # Expected: {"status":"healthy","engine":"NeuTTS Air",...}
  ```

- [ ] **Check LiveKit**
  ```bash
  curl http://localhost:7880/
  # Expected: HTML response
  ```

- [ ] **Check voice agent**
  ```bash
  curl http://localhost:9003/health
  # Expected: {"status":"healthy",...}
  ```

- [ ] **Check available voices**
  ```bash
  curl http://localhost:8000/api/tts/voices
  # Expected: JSON with voices array
  ```

## ☐ Test TTS

- [ ] **Generate test audio**
  ```bash
  curl -X POST http://localhost:8000/api/tts/synthesize \
    -H "Content-Type: application/json" \
    -d '{"text":"Hello, this is Zoe speaking with my new voice!","voice":"default"}' \
    --output /tmp/test-voice.wav
  ```

- [ ] **Play test audio**
  ```bash
  aplay /tmp/test-voice.wav
  # Listen: Should sound much better than espeak!
  ```

- [ ] **Test with voice cloning**
  ```bash
  curl -X POST http://localhost:8000/api/tts/synthesize \
    -H "Content-Type: application/json" \
    -d '{"text":"My name is Dave, and this is voice cloning!","voice":"dave"}' \
    --output /tmp/test-dave.wav
  
  aplay /tmp/test-dave.wav
  # Listen: Should sound natural and human-like
  ```

## ☐ Test Voice Conversation

- [ ] **Open voice client in browser**
  - Navigate to: `http://YOUR_PI_IP/voice-client.html`
  - Or: `http://zoe.local/voice-client.html`

- [ ] **Test conversation flow**
  1. Enter your name
  2. Select voice profile: "zoe"
  3. Click "Start Conversation"
  4. Grant microphone permission
  5. Say: "Hello Zoe, can you hear me?"
  6. Listen for response
  7. Try interrupting Zoe while she's speaking

- [ ] **Verify conversation features**
  - [ ] Audio plays smoothly
  - [ ] Can interrupt Zoe
  - [ ] Latency is acceptable (<1 second)
  - [ ] Voice sounds natural

## ☐ Monitoring

- [ ] **Check CPU usage**
  ```bash
  docker stats
  # zoe-tts should be <80% during synthesis
  ```

- [ ] **Check memory usage**
  ```bash
  docker stats
  # Total should be <6GB
  ```

- [ ] **Monitor logs for errors**
  ```bash
  docker-compose logs -f | grep -i error
  # Should be minimal/no errors
  ```

## ☐ Performance Benchmarks

- [ ] **Measure TTS latency**
  ```bash
  time curl -X POST http://localhost:9002/synthesize \
    -H "Content-Type: application/json" \
    -d '{"text":"Quick test","voice":"default"}' \
    -o /dev/null 2>&1
  # Expected: 1-3 seconds
  ```

- [ ] **Test concurrent requests** (optional)
  ```bash
  for i in {1..3}; do
    curl -X POST http://localhost:9002/synthesize \
      -H "Content-Type: application/json" \
      -d '{"text":"Test '$i'","voice":"default"}' \
      -o /tmp/test$i.wav &
  done
  wait
  # All should complete successfully
  ```

## ☐ Troubleshooting (If Issues)

- [ ] **If TTS fails to start**
  ```bash
  docker-compose logs zoe-tts | tail -50
  docker-compose restart zoe-tts
  ```

- [ ] **If voice is robotic**
  - Check sample files downloaded: `ls -lh services/zoe-tts/samples/*.wav`
  - Verify using voice profile, not "default"
  - Check CPU not throttling: `vcgencmd measure_temp`

- [ ] **If LiveKit won't connect**
  ```bash
  # Check ports
  sudo netstat -tuln | grep -E "(7880|7881)"
  
  # Check firewall
  sudo ufw status
  sudo ufw allow 7880/tcp
  sudo ufw allow 50000:50200/udp
  ```

- [ ] **If latency is too high**
  - Close other Docker containers
  - Use "default" voice (fastest)
  - Check network latency if accessing remotely

## ☐ Optional Enhancements

- [ ] **Create custom Zoe voice**
  - Record 10 seconds of your preferred voice
  - Upload via API or create zoe.wav manually
  - Restart zoe-tts service

- [ ] **Set up HTTPS** (for remote access)
  - Required for microphone access over internet
  - Use Let's Encrypt or self-signed cert

- [ ] **Configure production API keys**
  ```bash
  # Generate secure keys
  openssl rand -base64 32
  # Update .env file
  ```

## ☐ Documentation

- [ ] **Read setup guide**
  - Location: `/home/pi/zoe/docs/guides/voice-agent-setup.md`

- [ ] **Bookmark API docs**
  - URL: `http://YOUR_PI_IP:8000/docs`

- [ ] **Save this checklist for reference**

## ✅ Deployment Complete!

Once all boxes are checked:
- [x] TTS service running with NeuTTS Air
- [x] Voice cloning working
- [x] LiveKit server operational
- [x] Voice conversations functional
- [x] Performance acceptable

**You're ready to enjoy ultra-realistic AI conversations with Zoe!** 🎉

---

## Support

If you encounter issues:

1. **Check logs**: `docker-compose logs -f zoe-tts zoe-voice-agent livekit`
2. **Review guide**: `/home/pi/zoe/docs/guides/voice-agent-setup.md`
3. **Check status**: `curl http://localhost:8000/api/voice/status`
4. **Restart services**: `docker-compose restart zoe-tts livekit zoe-voice-agent`

---

**Implementation Date**: October 10, 2025  
**Version**: 1.0.0  
**Status**: Production-ready for personal use












