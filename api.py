from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pydantic import BaseModel
from typing import Optional
from livekit import api
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv(".env.local")

mongodb_uri = os.getenv("MONGODB_URI")
mongo_client = MongoClient(mongodb_uri)

# LiveKit credentials
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET") 
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://demo-project-92ni5pxo.livekit.cloud")

db = mongo_client["livekitpoc"]
collection = db["conversations"]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # replace with your frontend URL later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SessionRequest(BaseModel):
    room: str
    user_id: Optional[str] = None

class TokenRequest(BaseModel):
    room: str
    identity: Optional[str] = None

class SessionResponse(BaseModel):
    room: str
    status: str
    message: str

# Token generation endpoint
@app.post("/token")
async def generate_token(request: TokenRequest):
    try:
        if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
            raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
        
        identity = request.identity or f"patient_{int(datetime.now().timestamp())}"
        
        # Create access token
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(identity) \
            .with_name(identity) \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=request.room,
                can_publish=True,
                can_subscribe=True,
            )) \
            .with_ttl(timedelta(hours=2))
        
        return {
            "token": token.to_jwt(),
            "identity": identity,
            "room": request.room,
            "url": LIVEKIT_URL
        }
        
    except Exception as e:
        print(f"Error generating token: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate token: {str(e)}")

# Connect endpoint - starts or resumes a session
@app.post("/connect", response_model=SessionResponse)
async def connect_session(request: SessionRequest):
    try:
        # Check if conversation exists
        existing_conversation = collection.find_one({"room": request.room})
        
        if existing_conversation:
            return SessionResponse(
                room=request.room,
                status="resumed",
                message=f"Session resumed for room {request.room}"
            )
        else:
            return SessionResponse(
                room=request.room,
                status="new",
                message=f"New session started for room {request.room}"
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Pause endpoint
@app.post("/pause/{room}", response_model=SessionResponse)
async def pause_session(room: str):
    try:
        return SessionResponse(
            room=room,
            status="paused", 
            message=f"Session paused for room {room}"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Simple resume endpoint - just return the conversation history
@app.post("/resume/{room}")
async def resume_session(room: str):
    try:
        # Check if conversation exists in DB
        conversation = collection.find_one({"room": room})
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Room not found")
        
        messages = conversation.get("messages", [])
        
        # Return the conversation history for LiveKit to continue with
        return {
            "room": room,
            "messages": messages,
            "status": "resumed",
            "total_messages": len(messages)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get conversation history
@app.get("/conversation/{room}")
async def get_conversation(room: str):
    try:
        doc = collection.find_one({"room": room})
        if doc:
            return {
                "room": room,
                "messages": doc.get("messages", [])
            }
        else:
            return {
                "room": room,
                "messages": []
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# List all available rooms
@app.get("/rooms")
async def list_rooms():
    try:
        rooms = collection.distinct("room")
        return {"rooms": rooms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)