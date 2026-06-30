import re

with open("server.py", "r") as f:
    content = f.read()

# Replace RATE_LIMIT_MAX and window
content = re.sub(
    r'RATE_LIMIT_MAX = 60\s*# requests per window\s*RATE_LIMIT_WINDOW = 60\s*# seconds',
    r'''BT_RATE_LIMIT_PER_MINUTE = int(os.environ.get("BT_RATE_LIMIT_PER_MINUTE", "120"))
BT_RATE_LIMIT_RETRY_AFTER = int(os.environ.get("BT_RATE_LIMIT_RETRY_AFTER", "10"))

RATE_LIMIT_MAX = BT_RATE_LIMIT_PER_MINUTE
RATE_LIMIT_WINDOW = 60''',
    content
)

# Replace rate limit response
content = re.sub(
    r'return jsonify\(\{\s*"error": "Rate limit exceeded\. Max 60 requests per minute\.",\s*"request_id": request\.request_id,\s*\}\), 429',
    r'''response = jsonify({
                "error": "rate_limited",
                "retry_after": BT_RATE_LIMIT_RETRY_AFTER,
                "request_id": request.request_id,
            })
            response.headers["Retry-After"] = str(BT_RATE_LIMIT_RETRY_AFTER)
            return response, 429''',
    content
)

with open("server.py", "w") as f:
    f.write(content)
