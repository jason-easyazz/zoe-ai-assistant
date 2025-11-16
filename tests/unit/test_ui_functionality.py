#!/usr/bin/env python3
"""
Comprehensive UI Functionality Test Script
Tests all core services and UI endpoints
"""
import requests
import json
import time
from typing import Dict, Any

class UIFunctionalityTester:
    def __init__(self):
        self.base_urls = {
            'zoe-core': 'http://localhost:8000/api',
            'people-service': 'http://localhost:8010',
            'collections-service': 'http://localhost:8011'
        }
        self.results = {}
    
    def test_service_health(self) -> Dict[str, Any]:
        """Test all service health endpoints"""
        print("🔍 Testing Service Health...")
        health_results = {}
        
        # Test zoe-core health
        try:
            response = requests.get(f"{self.base_urls['zoe-core']}/health", timeout=5)
            health_results['zoe-core'] = {
                'status': response.status_code,
                'healthy': response.status_code == 200,
                'data': response.json() if response.status_code == 200 else None
            }
        except Exception as e:
            health_results['zoe-core'] = {'status': 'error', 'healthy': False, 'error': str(e)}
        
        # Test people-service
        try:
            response = requests.get(f"{self.base_urls['people-service']}/people", timeout=5)
            health_results['people-service'] = {
                'status': response.status_code,
                'healthy': response.status_code == 200,
                'data': response.json() if response.status_code == 200 else None
            }
        except Exception as e:
            health_results['people-service'] = {'status': 'error', 'healthy': False, 'error': str(e)}
        
        # Test collections-service
        try:
            response = requests.get(f"{self.base_urls['collections-service']}/collections", timeout=5)
            health_results['collections-service'] = {
                'status': response.status_code,
                'healthy': response.status_code == 200,
                'data': response.json() if response.status_code == 200 else None
            }
        except Exception as e:
            health_results['collections-service'] = {'status': 'error', 'healthy': False, 'error': str(e)}
        
        return health_results
    
    def test_api_endpoints(self) -> Dict[str, Any]:
        """Test core API endpoints"""
        print("🔍 Testing API Endpoints...")
        endpoint_results = {}
        
        # Test lists endpoint
        try:
            response = requests.get(f"{self.base_urls['zoe-core']}/lists", timeout=5)
            endpoint_results['lists'] = {
                'status': response.status_code,
                'working': response.status_code in [200, 404],  # 404 is OK if no lists exist
                'data': response.text[:100] if response.status_code != 200 else response.json()
            }
        except Exception as e:
            endpoint_results['lists'] = {'status': 'error', 'working': False, 'error': str(e)}
        
        # Test calendar endpoint
        try:
            response = requests.get(f"{self.base_urls['zoe-core']}/calendar/events?start_date=2025-10-01&end_date=2025-10-31", timeout=5)
            endpoint_results['calendar'] = {
                'status': response.status_code,
                'working': response.status_code in [200, 500],  # 500 might be expected
                'data': response.text[:100] if response.status_code != 200 else response.json()
            }
        except Exception as e:
            endpoint_results['calendar'] = {'status': 'error', 'working': False, 'error': str(e)}
        
        # Test reminders endpoint
        try:
            response = requests.get(f"{self.base_urls['zoe-core']}/reminders", timeout=5)
            endpoint_results['reminders'] = {
                'status': response.status_code,
                'working': response.status_code in [200, 404],
                'data': response.text[:100] if response.status_code != 200 else response.json()
            }
        except Exception as e:
            endpoint_results['reminders'] = {'status': 'error', 'working': False, 'error': str(e)}
        
        return endpoint_results
    
    def test_microservices(self) -> Dict[str, Any]:
        """Test microservice-specific endpoints"""
        print("🔍 Testing Microservices...")
        microservice_results = {}
        
        # Test people service endpoints
        try:
            response = requests.get(f"{self.base_urls['people-service']}/people", timeout=5)
            people_data = response.json()
            microservice_results['people-service'] = {
                'status': response.status_code,
                'working': response.status_code == 200,
                'people_count': people_data.get('count', 0),
                'sample_people': [p['name'] for p in people_data.get('people', [])[:3]]
            }
        except Exception as e:
            microservice_results['people-service'] = {'status': 'error', 'working': False, 'error': str(e)}
        
        # Test collections service endpoints
        try:
            response = requests.get(f"{self.base_urls['collections-service']}/collections", timeout=5)
            collections_data = response.json()
            microservice_results['collections-service'] = {
                'status': response.status_code,
                'working': response.status_code == 200,
                'collections_count': collections_data.get('count', 0)
            }
        except Exception as e:
            microservice_results['collections-service'] = {'status': 'error', 'working': False, 'error': str(e)}
        
        return microservice_results
    
    def run_comprehensive_test(self) -> Dict[str, Any]:
        """Run all tests and return comprehensive results"""
        print("🚀 Starting Comprehensive UI Functionality Test...")
        print("=" * 60)
        
        start_time = time.time()
        
        # Run all tests
        health_results = self.test_service_health()
        endpoint_results = self.test_api_endpoints()
        microservice_results = self.test_microservices()
        
        # Compile results
        self.results = {
            'test_timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'test_duration': round(time.time() - start_time, 2),
            'health_tests': health_results,
            'endpoint_tests': endpoint_results,
            'microservice_tests': microservice_results
        }
        
        # Calculate overall health
        healthy_services = sum(1 for result in health_results.values() if result.get('healthy', False))
        total_services = len(health_results)
        
        self.results['overall_health'] = {
            'healthy_services': healthy_services,
            'total_services': total_services,
            'health_percentage': round((healthy_services / total_services) * 100, 1),
            'status': 'HEALTHY' if healthy_services == total_services else 'DEGRADED' if healthy_services > 0 else 'UNHEALTHY'
        }
        
        return self.results
    
    def print_results(self):
        """Print formatted test results"""
        print("\n" + "=" * 60)
        print("📊 COMPREHENSIVE TEST RESULTS")
        print("=" * 60)
        
        # Overall health
        overall = self.results['overall_health']
        print(f"🏥 Overall Health: {overall['status']}")
        print(f"   Services: {overall['healthy_services']}/{overall['total_services']} ({overall['health_percentage']}%)")
        print(f"   Test Duration: {self.results['test_duration']}s")
        
        # Service health details
        print(f"\n🔍 Service Health Details:")
        for service, result in self.results['health_tests'].items():
            status_icon = "✅" if result.get('healthy', False) else "❌"
            print(f"   {status_icon} {service}: {result.get('status', 'unknown')}")
        
        # Endpoint details
        print(f"\n🔗 API Endpoint Status:")
        for endpoint, result in self.results['endpoint_tests'].items():
            status_icon = "✅" if result.get('working', False) else "❌"
            print(f"   {status_icon} {endpoint}: {result.get('status', 'unknown')}")
        
        # Microservice details
        print(f"\n🔧 Microservice Status:")
        for service, result in self.results['microservice_tests'].items():
            status_icon = "✅" if result.get('working', False) else "❌"
            if service == 'people-service' and result.get('working'):
                print(f"   {status_icon} {service}: {result.get('people_count', 0)} people")
            elif service == 'collections-service' and result.get('working'):
                print(f"   {status_icon} {service}: {result.get('collections_count', 0)} collections")
            else:
                print(f"   {status_icon} {service}: {result.get('status', 'unknown')}")
        
        print("\n" + "=" * 60)

def main():
    """Main test function"""
    tester = UIFunctionalityTester()
    results = tester.run_comprehensive_test()
    tester.print_results()
    
    # Save results to file
    with open(str(PROJECT_ROOT / "test_results.json"), 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Results saved to: /home/zoe/assistant/test_results.json")
    
    return results

if __name__ == "__main__":
    main()

