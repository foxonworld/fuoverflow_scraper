import requests
import json

url = "http://localhost:8000/api/drafts/MLN122_v3/clean"
try:
    resp = requests.get(url)
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Data type: {type(data)}")
    if isinstance(data, list):
        print(f"Count: {len(data)}")
        if len(data) > 0:
            print(f"First item: {data[0]}")
    else:
        print(f"Keys: {data.keys()}")
except Exception as e:
    print(f"Error: {e}")
