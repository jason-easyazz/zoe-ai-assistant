#!/bin/bash
echo "🔧 Fixing deployment and completing setup..."
cd ~/zoe-ai-assistant
mkdir -p services/zoe-ui/dist

# Deploy working interface (simplified version for now)
cat > services/zoe-ui/dist/index.html << 'HTML_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe - Personal AI Companion</title>
    <style>
        body { 
            font-family: system-ui; 
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            height: 100vh; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            margin: 0;
        }
        .container { 
            text-align: center; 
            background: rgba(255,255,255,0.9); 
            padding: 40px; 
            border-radius: 20px; 
            box-shadow: 0 20px 60px rgba(0,0,0,0.1);
        }
        .orb { 
            width: 120px; 
            height: 120px; 
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
            border-radius: 50%; 
            margin: 0 auto 20px; 
            animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.05); } }
        h1 { color: #333; margin-bottom: 15px; }
        .status { background: #7B61FF; color: white; padding: 10px 20px; border-radius: 25px; display: inline-block; }
    </style>
</head>
<body>
    <div class="container">
        <div class="orb"></div>
        <h1>Hey there! I'm Zoe</h1>
        <p>Your personal AI companion is ready!</p>
        <div class="status">✨ Interface Active</div>
        <br><br>
        <p><strong>Access Points:</strong></p>
        <p>🌐 UI: http://$(hostname -I | awk '{print $1}'):8080</p>
        <p>🔌 API: http://$(hostname -I | awk '{print $1}'):8000</p>
    </div>
    <script>
        console.log('🤖 Zoe v3.1 Interface Loaded!');
        setTimeout(() => {
            fetch('/health').then(r => r.json()).then(d => {
                document.querySelector('.status').innerHTML = '🟢 Connected & Ready';
            }).catch(() => {
                document.querySelector('.status').innerHTML = '🟡 Backend Starting...';
            });
        }, 2000);
    </script>
</body>
</html>
HTML_EOF

echo "✅ Working interface deployed!"

# Restart services
docker compose restart zoe-ui

PI_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "🎉 Zoe v3.1 is now running!"
echo "🌐 Access: http://$PI_IP:8080"
echo "🔌 API: http://$PI_IP:8000/health"
echo ""
