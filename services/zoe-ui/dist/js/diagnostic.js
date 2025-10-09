/**
 * Chat Interface Diagnostic Tool
 * Run this in browser console to debug mixed content issues
 */

console.log('═══════════════════════════════════════════════════════════');
console.log('       CHAT INTERFACE DIAGNOSTIC TOOL v1.0');
console.log('═══════════════════════════════════════════════════════════');
console.log('');

// 1. Page Information
console.log('📄 PAGE INFORMATION:');
console.log('  Protocol:', window.location.protocol);
console.log('  Host:', window.location.host);
console.log('  Href:', window.location.href);
console.log('  Origin:', window.location.origin);
console.log('');

// 2. Base Tags
console.log('🔗 BASE TAGS:');
const baseTags = document.querySelectorAll('base');
if (baseTags.length > 0) {
    baseTags.forEach((base, i) => {
        console.log(`  Base ${i + 1}:`, base.href);
    });
} else {
    console.log('  ✅ No base tags found');
}
console.log('');

// 3. Fetch Interceptor Check
console.log('🔍 FETCH INTERCEPTOR:');
console.log('  Type:', typeof window.fetch);
const fetchSource = window.fetch.toString();
if (fetchSource.includes('setupFetchInterceptor') || fetchSource.includes('originalFetch')) {
    console.log('  ✅ Custom interceptor detected');
    console.log('  Source (first 200 chars):', fetchSource.substring(0, 200));
} else {
    console.log('  ❌ Using native fetch (interceptor NOT installed)');
    console.log('  Source:', fetchSource);
}
console.log('');

// 4. Service Workers
console.log('⚙️  SERVICE WORKERS:');
navigator.serviceWorker.getRegistrations().then(registrations => {
    if (registrations.length > 0) {
        console.log('  ⚠️ Active service workers found:', registrations.length);
        registrations.forEach((reg, i) => {
            console.log(`    SW ${i + 1}:`, reg.scope);
        });
    } else {
        console.log('  ✅ No service workers registered');
    }
});

// 5. Auth System Check
console.log('🔐 AUTH SYSTEM:');
if (window.zoeAuth) {
    console.log('  ✅ zoeAuth object found');
    console.log('  Is authenticated:', window.zoeAuth.isAuthenticated());
    console.log('  Session:', window.zoeAuth.getSession() ? 'exists' : 'none');
} else {
    console.log('  ❌ zoeAuth not found');
}
console.log('');

// 6. API Base Configuration
console.log('🌐 API CONFIGURATION:');
if (window.API_BASE) {
    console.log('  API_BASE:', window.API_BASE);
} else {
    console.log('  ℹ️  API_BASE not set (using functions)');
}
if (typeof getApiBase === 'function') {
    console.log('  getApiBase():', getApiBase());
}
console.log('');

// 7. Test Fetch Call
console.log('🧪 TEST FETCH CALL:');
console.log('  Testing fetch with relative URL...');
fetch('/api/health').then(response => {
    console.log('  ✅ Fetch successful');
    console.log('    Status:', response.status);
    console.log('    URL:', response.url);
    console.log('    Protocol:', new URL(response.url).protocol);
}).catch(error => {
    console.log('  ❌ Fetch failed:', error.message);
});

// 8. Check for HTTP/HTTPS Mixed Content
console.log('');
console.log('🔒 MIXED CONTENT CHECK:');
const allScripts = document.querySelectorAll('script[src]');
const allLinks = document.querySelectorAll('link[href]');
const allImages = document.querySelectorAll('img[src]');

let httpResources = [];

allScripts.forEach(script => {
    if (script.src.startsWith('http://')) {
        httpResources.push(`Script: ${script.src}`);
    }
});

allLinks.forEach(link => {
    if (link.href.startsWith('http://')) {
        httpResources.push(`Link: ${link.href}`);
    }
});

allImages.forEach(img => {
    if (img.src.startsWith('http://')) {
        httpResources.push(`Image: ${img.src}`);
    }
});

if (httpResources.length > 0) {
    console.log('  ⚠️ HTTP resources found on HTTPS page:');
    httpResources.forEach(resource => {
        console.log('   ', resource);
    });
} else {
    console.log('  ✅ No HTTP resources detected');
}
console.log('');

// 9. Manual Interceptor Test
console.log('🔧 MANUAL INTERCEPTOR TEST:');
console.log('  Creating test URL: http://zoe.local/api/test');

const testUrl = 'http://zoe.local/api/test';
console.log('  Before interceptor:', testUrl);

// Simulate what interceptor should do
let processedUrl = testUrl;
if (processedUrl.startsWith('http://')) {
    processedUrl = processedUrl.replace(/^http:\/\//, 'https://');
    console.log('  After HTTP→HTTPS:', processedUrl);
}
if (processedUrl.startsWith('https://')) {
    processedUrl = processedUrl.replace(/^https:\/\/[^/]+/, '');
    console.log('  After HTTPS→Relative:', processedUrl);
}
console.log('  Final URL:', processedUrl);
console.log('');

// 10. Summary
console.log('═══════════════════════════════════════════════════════════');
console.log('                      SUMMARY');
console.log('═══════════════════════════════════════════════════════════');
console.log('');
console.log('📋 Run these additional checks manually:');
console.log('');
console.log('1. Check if interceptor logs appear:');
console.log('   fetch("/api/test").then(r => console.log("Done"));');
console.log('   Look for: 🔒 Forced HTTP → HTTPS');
console.log('');
console.log('2. Check URL transformation:');
console.log('   fetch("http://zoe.local/api/test");');
console.log('   Should see debug logs if interceptor works');
console.log('');
console.log('3. Test apiRequest directly:');
console.log('   apiRequest("/api/health").then(r => console.log(r));');
console.log('');
console.log('═══════════════════════════════════════════════════════════');

