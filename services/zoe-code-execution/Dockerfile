FROM node:20-slim

WORKDIR /app

# Install Python and pip
RUN apt-get update && apt-get install -y python3 python3-pip curl && rm -rf /var/lib/apt/lists/*

# Install TypeScript and tsx globally (faster than ts-node)
RUN npm install -g typescript tsx @types/node

# Copy Python requirements first (for better caching)
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt || pip3 install -r requirements.txt

# Copy package.json and install Node dependencies
COPY package.json .
RUN npm install

# Copy application code
COPY . .

EXPOSE 8010

# Create workspace directory
RUN mkdir -p /tmp/zoe-code-workspace

CMD ["python3", "main.py"]

