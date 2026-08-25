#!/usr/bin/env python3
"""Test login endpoint and print response"""

import requests
import json
import time

# Wait for server to be ready
print("Waiting 2 seconds for server to be ready...")
time.sleep(2)

url = "http://localhost:8444/admin/login"
payload = {"user": "admin", "password": "admin"}

print(f"\n=== Login Test ===")
print(f"URL: {url}")
print(f"Payload: {payload}")
print()

try:
    response = requests.post(url, json=payload)
    
    print(f"Status Code: {response.status_code}")
    print(f"Headers: {dict(response.headers)}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'N/A')}")
    print()
    print("Response Body (raw):")
    print(response.text)
    print()
    print("Response Body (JSON if parseable):")
    try:
        json_response = response.json()
        print(json.dumps(json_response, indent=2))
    except:
        print("  (Not JSON)")
        
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
