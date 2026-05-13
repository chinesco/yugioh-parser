import asyncio
import os
from google import genai

async def main():
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"), http_options={'api_version': 'v1alpha'})
    try:
        async with client.aio.live.connect(model="gemini-2.0-flash-exp") as session:
            print("Connected successfully!")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
