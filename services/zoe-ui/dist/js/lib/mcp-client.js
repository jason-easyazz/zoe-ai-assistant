/**
 * MCP Client for Zoe UI
 * =====================
 * 
 * Provides dynamic tool discovery and execution for modular Zoe architecture.
 * This allows the UI to discover and use capabilities from any installed module
 * without hardcoding specific endpoints.
 * 
 * Usage:
 *   const mcp = new MCPClient();
 *   await mcp.init();
 *   const tools = await mcp.discoverTools();
 *   const result = await mcp.callTool('music_search', { query: 'Beatles' });
 * 
 * Version: 1.0.0
 * Architecture: Modular, MCP-based
 */

class MCPClient {
    constructor(options = {}) {
        this.baseUrl = options.baseUrl || '';
        // Use relative URL so auth interceptor routes it properly
        this.mcpServerUrl = options.mcpServerUrl || '/api/mcp';
        this.tools = new Map();
        this.toolsByDomain = new Map();
        this.initialized = false;
        this.cache = {
            tools: null,
            timestamp: null,
            ttl: options.cacheTTL || 60000 // 1 minute default
        };
    }

    /**
     * Initialize the MCP client
     */
    async init() {
        console.log('🔧 MCPClient: Initializing...');
        
        try {
            await this.discoverTools();
            this.initialized = true;
            console.log(`✅ MCPClient: Discovered ${this.tools.size} tools from modules`);
            return true;
        } catch (error) {
            console.error('❌ MCPClient: Initialization failed:', error);
            throw error;
        }
    }

    /**
     * Discover available tools from MCP server
     */
    async discoverTools(forceRefresh = false) {
        // Check cache
        if (!forceRefresh && this.cache.tools && 
            Date.now() - this.cache.timestamp < this.cache.ttl) {
            return this.cache.tools;
        }

        try {
            const response = await fetch(`${this.mcpServerUrl}/tools/list`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });

            if (!response.ok) {
                throw new Error(`Tool discovery failed: ${response.status}`);
            }

            const data = await response.json();
            const tools = data.tools || [];

            // Clear existing
            this.tools.clear();
            this.toolsByDomain.clear();

            // Index tools
            tools.forEach(tool => {
                this.tools.set(tool.name, tool);
                
                // Group by domain (e.g., "music_search" -> "music")
                const domain = tool.name.split('_')[0];
                if (!this.toolsByDomain.has(domain)) {
                    this.toolsByDomain.set(domain, []);
                }
                this.toolsByDomain.get(domain).push(tool);
            });

            // Update cache
            this.cache.tools = tools;
            this.cache.timestamp = Date.now();

            console.log(`🔍 MCPClient: Discovered ${tools.length} tools`);
            return tools;
        } catch (error) {
            console.error('❌ MCPClient: Tool discovery failed:', error);
            throw error;
        }
    }

    /**
     * Get tools for a specific domain (e.g., "music", "calendar")
     */
    getToolsForDomain(domain) {
        return this.toolsByDomain.get(domain) || [];
    }

    /**
     * Check if a tool is available
     */
    hasTool(toolName) {
        return this.tools.has(toolName);
    }

    /**
     * Get tool definition
     */
    getTool(toolName) {
        return this.tools.get(toolName);
    }

    /**
     * Call an MCP tool
     * @param {string} toolName - Name of the tool (e.g., "music_search")
     * @param {object} params - Tool parameters
     * @param {object} options - Call options (timeout, retries, etc.)
     */
    async callTool(toolName, params = {}, options = {}) {
        if (!this.initialized) {
            throw new Error('MCPClient not initialized. Call init() first.');
        }

        if (!this.hasTool(toolName)) {
            throw new Error(`Tool "${toolName}" not found. Available tools: ${Array.from(this.tools.keys()).join(', ')}`);
        }

        const timeout = options.timeout || 30000;
        const retries = options.retries || 0;

        return this._executeToolCall(toolName, params, timeout, retries);
    }

    /**
     * Execute tool call with retry logic
     */
    async _executeToolCall(toolName, params, timeout, retriesLeft) {
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), timeout);

            const response = await fetch(`${this.mcpServerUrl}/tools/${toolName}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params),
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.error || `Tool call failed: ${response.status}`);
            }

            const result = await response.json();
            
            // Log for debugging
            console.log(`🔧 MCP Call: ${toolName}`, { params, result });

            return result;
        } catch (error) {
            if (retriesLeft > 0) {
                console.warn(`⚠️ MCP: Retrying ${toolName} (${retriesLeft} retries left)`);
                await this._sleep(1000);
                return this._executeToolCall(toolName, params, timeout, retriesLeft - 1);
            }
            
            console.error(`❌ MCP: Tool call failed: ${toolName}`, error);
            throw error;
        }
    }

    /**
     * Batch call multiple tools in parallel
     */
    async callTools(calls) {
        const promises = calls.map(({ tool, params, options }) => 
            this.callTool(tool, params, options)
                .catch(error => ({ error: error.message }))
        );
        
        return Promise.all(promises);
    }

    /**
     * Subscribe to tool updates (for real-time features)
     */
    subscribe(toolPattern, callback) {
        // TODO: Implement WebSocket subscription for real-time updates
        console.warn('MCP subscriptions not yet implemented');
    }

    /**
     * Get available capabilities grouped by domain
     */
    getCapabilities() {
        const capabilities = {};
        
        for (const [domain, tools] of this.toolsByDomain) {
            capabilities[domain] = {
                available: true,
                tools: tools.map(t => ({
                    name: t.name,
                    description: t.description
                }))
            };
        }
        
        return capabilities;
    }

    /**
     * Utility: Sleep
     */
    _sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
     * Check if a module is enabled by checking for its tools
     */
    isModuleEnabled(moduleName) {
        return this.toolsByDomain.has(moduleName);
    }

    /**
     * Get all enabled modules
     */
    getEnabledModules() {
        return Array.from(this.toolsByDomain.keys());
    }
}

// Export for use in other scripts
if (typeof window !== 'undefined') {
    window.MCPClient = MCPClient;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MCPClient;
}
