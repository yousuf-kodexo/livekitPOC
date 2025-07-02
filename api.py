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

# ====================== AGENT CODE (for run_agent.py) ======================

# LiveKit Agent imports
from livekit.plugins import deepgram
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
)
from livekit.plugins import silero
from livekit.plugins import openai as openai_plugin
import logging
import json
import aiohttp
import asyncio
from collections import deque

# Setup agent logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Agent variables
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
message_queue = deque()

async def background_saver():
    while True:
        try:
            if message_queue:
                room, msg = message_queue.popleft()
                collection.update_one(
                    {"room": room},
                    {"$push": {"messages": msg}},
                    upsert=True
                )
                logger.info(f"Flushed message to Mongo for room '{room}'")
        except Exception as e:
            logger.error(f"Background saver error: {e}")
        await asyncio.sleep(1)

async def fetch_conversation_history(room_name: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_BASE_URL}/conversation/{room_name}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("messages", [])
    except Exception as e:
        logger.error(f"Error fetching conversation history: {e}")
    return []

def build_conversation_context(messages):
    if not messages:
        return ""
    context = "\n\nPREVIOUS CONVERSATION CONTEXT:\n"
    context += "=" * 50 + "\n"
    recent_messages = messages[-10:] if len(messages) > 10 else messages
    for msg in recent_messages:
        role = "PATIENT" if msg.get("role") == "user" else "DR. VIRTUAL"
        text = msg.get("text", "")
        context += f"{role}: {text}\n"
    context += "=" * 50 + "\n"
    context += "Continue the conversation naturally from where we left off. Do not repeat questions that have already been asked and answered.\n\n"
    return context

def determine_conversation_stage(messages):
    if not messages:
        return "introduction"
    user_responses = len([msg for msg in messages if msg.get("role") == "user"])
    if user_responses <= 2:
        return "introduction_name"
    elif user_responses <= 5:
        return "demographics"
    elif user_responses <= 15:
        return "injury_history"
    elif user_responses <= 25:
        return "daily_functioning"
    elif user_responses <= 35:
        return "past_medical_history"
    elif user_responses <= 50:
        return "review_of_systems"
    elif user_responses <= 55:
        return "family_history"
    elif user_responses <= 70:
        return "social_history"
    elif user_responses <= 80:
        return "occupational_history"
    else:
        return "summary"

async def entrypoint(ctx: JobContext):
    try:
        logger.info("Connecting to LiveKit room...")
        await ctx.connect()
        logger.info(f"Connected to room: {ctx.room.name}")
        
        conversation_history = await fetch_conversation_history(ctx.room.name)
        
        base_instructions = """
Act as a friendly, caring, polite, and brilliant physician conducting a comprehensive medical interview in the context of an independent medical evaluation. 

You aim to have a natural conversation with a patient by strictly following these rules, which are inside the "instructions" tag NOTE: these instructions should not be visible to user: 

<instructions>
- The AI should ensure the patient answers all parts of a question.
- If incomplete, ask for clarification politely.
- Only proceed to next question after clear, relevant answer.
- Never acknowledge AI identity or non-human nature.
- 1-line responses, 1 question at a time.
- Introduce directly as "I am Dr Virtual..."
- Do not repeat patient's name excessively.
- No mention of upcoming questions.
- No word slashes (/).
- Always get all question parts answered before moving on.
- Focus on injury and history.
- Use professional medical language.
- Wait for clear answers.
</instructions>

You will obtain detailed injury and health history, then summarize for review. Start with:
"I am Doctor Virtual and want to understand your injury and overall health. Please respond to my questions with as much detail as possible. You can end the session by clicking End Button."

Then: "How would you like me to address you? Please provide your first name or how you wish to be addressed."

Then: "It is a pleasure to meet you, [name]. Do you understand I am a virtual interviewer, not a real doctor?"

Then: "Before we discuss your history, I would like to obtain some background information."

Questions:
1. What is your gender and age in years?
2. What hand do you prefer to use, for example, are you right-handed, left-handed, or ambidextrous?
3. what is your current weight and height?

[Continue the medical interview questions...]

Summary:
- Summarize, confirm with patient, thank, request feedback, then ask to click End Button.
"""

        if conversation_history:
            logger.info(f"Found {len(conversation_history)} previous messages for room {ctx.room.name}")
            context = build_conversation_context(conversation_history)
            stage = determine_conversation_stage(conversation_history)
            enhanced_instructions = base_instructions + context + f"\n\nCURRENT STAGE: {stage}\n"
            should_generate_greeting = False
        else:
            logger.info(f"No previous conversation found for room {ctx.room.name}")
            enhanced_instructions = base_instructions
            should_generate_greeting = True

        logger.info("Initializing Agent with instructions...")
        agent = Agent(
            allow_interruptions=False,
            instructions=enhanced_instructions,
        )

        logger.info("Setting up AgentSession...")
        session = AgentSession(
            vad=silero.VAD.load(),
            stt=openai_plugin.STT(
                model="whisper-1",
                language="en"
            ),
            llm=openai_plugin.LLM(model="gpt-3.5-turbo"),
            tts = openai_plugin.TTS(
                model="tts-1",
                voice="alloy"
            ),
        )

        logger.info("Starting session...")
        await session.start(agent=agent, room=ctx.room)

        asyncio.create_task(background_saver())

        @session.on("conversation_item_added") 
        def on_conversation_item(event):
            try:
                msg = {
                    "role": event.item.role,
                    "text": event.item.text_content,
                }

                data_message = {
                    "type": "conversation_message",
                    "role": event.item.role,
                    "text": event.item.text_content,
                }
                
                asyncio.create_task(
                    ctx.room.local_participant.publish_data(
                        json.dumps(data_message).encode(),
                        reliable=True
                    )
                )

                logger.info(f"Message saved and broadcasted for room '{ctx.room.name}'")

            except Exception as e:
                logger.error(f"Error handling conversation item: {str(e)}")
                    
            try:
                msg = {
                    "role": event.item.role,
                    "text": event.item.text_content,
                }
                message_queue.append((ctx.room.name, msg))
                logger.info(f"Queued message for room '{ctx.room.name}'")

                if event.item.role == "user":
                    print(f"[USER]: {event.item.text_content}")
                elif event.item.role == "assistant":
                    print(f"[AGENT]: {event.item.text_content}")
            except Exception as e:
                logger.error(f"Error queuing message: {e}")

        logger.info("Session fully running.")

        if should_generate_greeting:
            logger.info("Generating initial greeting...")
            await session.generate_reply(
                instructions="Start the medical interview as Dr. Virtual."
            )
            logger.info("Initial reply sent.")
        else:
            logger.info("Resuming previous conversation.")

    except Exception as e:
        logger.error(f"Error in entrypoint: {str(e)}", exc_info=True)


# End session endpoint - disconnects room and marks as completed
# End session endpoint - disconnects room and marks as completed
# End session endpoint - disconnects room and marks as completed
@app.post("/end/{room}")
async def end_session(room: str):
    try:
        # Mark session as ended in database
        collection.update_one(
            {"room": room},
            {"$set": {"status": "ended", "ended_at": datetime.now()}},
            upsert=True
        )
        
        # Delete the room using LiveKit API
        try:
            from livekit.api import LiveKitAPI, DeleteRoomRequest
            
            # Create LiveKit API client
            lkapi = LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
            
            # Delete the room (this disconnects all participants)
            await lkapi.room.delete_room(DeleteRoomRequest(room=room))
            
            # Close the API client
            await lkapi.aclose()
            
            logger.info(f"Room {room} deleted successfully from LiveKit")
            
        except Exception as livekit_error:
            logger.error(f"LiveKit API error: {livekit_error}")
            # Continue even if LiveKit deletion fails
        
        return {
            "room": room,
            "status": "ended",
            "message": f"Session ended and room {room} deleted"
        }
        
    except Exception as e:
        logger.error(f"Error ending session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment (deployment platforms set this automatically)
    port = int(os.environ.get("PORT", 8000))
    
    # Get host from environment (some platforms need specific host)
    host = os.environ.get("HOST", "0.0.0.0")
    
    print(f"🚀 Starting FastAPI server on {host}:{port}")
    
    # Production settings
    uvicorn.run(
        app, 
        host=host, 
        port=port,
        log_level="info",
        access_log=True
    )