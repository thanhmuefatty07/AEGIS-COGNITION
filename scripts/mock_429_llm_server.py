"""
AEGIS Mock 429 Rate-Limit Server (always 429 mode).
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()
request_count = 0

@app.post("/v1/chat/completions")
async def chat(request: Request):
    global request_count
    request_count += 1
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "message": "Rate limit exceeded. Try again later.",
                "type": "rate_limit_error",
                "code": 429
            }
        },
        headers={
            "Retry-After": "60",
            "X-RateLimit-Limit": "4",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "9999999999",
        }
    )

@app.get("/health")
async def health():
    return {"status": "ok", "request_count": request_count}

if __name__ == "__main__":
    print("Mock 429 server starting on :19999 (ALWAYS returns 429)")
    uvicorn.run(app, host="0.0.0.0", port=19999, log_level="warning")