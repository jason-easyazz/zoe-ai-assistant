import yaml

# Read current docker-compose
with open('docker-compose.yml', 'r') as f:
    compose = yaml.safe_load(f)

# Add Docker socket to zoe-core volumes
if 'services' in compose and 'zoe-core' in compose['services']:
    if 'volumes' not in compose['services']['zoe-core']:
        compose['services']['zoe-core']['volumes'] = []
    
    # Add Docker socket if not already there
    docker_socket = '/var/run/docker.sock:/var/run/docker.sock'
    if docker_socket not in compose['services']['zoe-core']['volumes']:
        compose['services']['zoe-core']['volumes'].append(docker_socket)
        print("✅ Added Docker socket mount")
    else:
        print("✅ Docker socket already mounted")

# Write back
with open('docker-compose.yml', 'w') as f:
    yaml.dump(compose, f, default_flow_style=False, sort_keys=False)
