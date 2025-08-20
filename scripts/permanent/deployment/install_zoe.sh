#!/bin/bash
# ZOE AI ASSISTANT - MASTER INSTALLER

echo "🤖 ZOE AI ASSISTANT - MASTER INSTALLER"
echo "======================================"
echo "Version: 1.1"
echo ""

SCRIPT_DIR="/home/pi/zoe/scripts/permanent/deployment"

# Function to run a phase
run_phase() {
    local phase_script=$1
    local phase_name=$2
    
    echo -e "\n🚀 Running ${phase_name}..."
    echo "----------------------------------------"
    
    if [ -f "${SCRIPT_DIR}/${phase_script}" ]; then
        bash "${SCRIPT_DIR}/${phase_script}"
        if [ $? -eq 0 ]; then
            echo "✅ ${phase_name} completed successfully"
            echo "$(date): ${phase_name} completed" >> /home/pi/zoe/documentation/dynamic/install_log.txt
        else
            echo "❌ ${phase_name} failed"
            echo "$(date): ${phase_name} FAILED" >> /home/pi/zoe/documentation/dynamic/install_log.txt
            exit 1
        fi
    else
        echo "⚠️ Script not found: ${phase_script}"
    fi
    
    sleep 2
}

# Check if running with required permissions
if [ "$EUID" -eq 0 ]; then 
   echo "❌ Please do not run as root (no sudo)"
   exit 1
fi

# Run all phases
run_phase "phase0_system_update.sh" "Phase 0: System Update"
run_phase "phase1_dependencies.sh" "Phase 1: Dependencies"
run_phase "phase2_directories.sh" "Phase 2: Directory Structure"
run_phase "phase3_samba.sh" "Phase 3: Samba Configuration"
run_phase "phase4_cloudflare.sh" "Phase 4: Cloudflare Check"
run_phase "phase5_github.sh" "Phase 5: GitHub Setup"

echo -e "\n======================================"
echo "✅ ZOE INSTALLATION COMPLETE!"
echo "======================================"

IP_ADDR=$(hostname -I | awk '{print $1}')
echo ""
echo "📂 Network share: \\\\${IP_ADDR}\\zoe"
echo "🐙 GitHub: https://github.com/jason-easyazz/zoe-ai-assistant"
echo ""
echo "⚠️ Logout and login for Docker permissions to take effect"
