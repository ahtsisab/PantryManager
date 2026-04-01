# Family Grocery List API

A simple FastAPI + SQLite backend for managing shared grocery lists.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --reload
```

API will be live at: http://localhost:8000  
Interactive docs: http://localhost:8000/docs

Script for Railway:

```
const API = 'https://web-production-fb421.up.railway.app';

```

\---

## API Reference

### Lists

|Method|Endpoint|Description|
|-|-|-|
|GET|`/lists`|Get all lists|
|POST|`/lists`|Create a list `{"name": "Costco Run"}`|
|DELETE|`/lists/{list\\\_id}`|Delete a list (and all its items)|

### Items

|Method|Endpoint|Description|
|-|-|-|
|GET|`/lists/{list\\\_id}/items`|Get all items in a list|
|POST|`/lists/{list\\\_id}/items`|Add an item `{"name": "Milk"}`|
|PATCH|`/lists/{list\\\_id}/items/{item\\\_id}`|Toggle purchased `{"purchased": true}`|
|DELETE|`/lists/{list\\\_id}/items/{item\\\_id}`|Remove an item|

\---

## Data

SQLite database is stored in `grocery.db` in the same directory.  
No setup needed — it's created automatically on first run.

## Next Steps

* Add a simple HTML/JS frontend
* Deploy to a home server or free tier (Railway, Render, Fly.io)
* Add a shared link / PIN so family members can bookmark the URL

