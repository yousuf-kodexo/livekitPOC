import subprocess
import time
import sys
import os
import signal
import threading
from dotenv import load_dotenv

# Load environment
load_dotenv(".env.local")

print("🚀 Starting LiveKit Medical Interview System...")

# Store process references
fastapi_process = None
agent_process = None

def cleanup_processes():
    """Clean up processes on exit"""
    global fastapi_process, agent_process
    
    print("\n🛑 Shutting down services...")
    
    if fastapi_process:
        fastapi_process.terminate()
        print("✅ FastAPI server stopped")
        
    if agent_process:
        agent_process.terminate() 
        print("✅ LiveKit agent stopped")

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    cleanup_processes()
    sys.exit(0)

def main():
    global fastapi_process, agent_process
    
    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Start FastAPI server
        print("🌐 Starting FastAPI server...")
        fastapi_process = subprocess.Popen([
            sys.executable, "api.py"
        ])
        print("✅ FastAPI server started (PID: {})".format(fastapi_process.pid))
        
        # Wait a moment for FastAPI to start
        time.sleep(3)
        
        # Start LiveKit agent
        print("🤖 Starting LiveKit agent...")
        agent_process = subprocess.Popen([
            sys.executable, "run_agent.py", "start"
        ])
        print("✅ LiveKit agent started (PID: {})".format(agent_process.pid))
        
        print("\n🎉 System is running!")
        print("📡 FastAPI server: http://localhost:8000")
        print("🎤 LiveKit agent: Ready for voice conversations")
        print("\nPress Ctrl+C to stop all services\n")
        
        # Monitor processes
        while True:
            # Check if FastAPI is still running
            if fastapi_process.poll() is not None:
                print("❌ FastAPI server died, restarting...")
                fastapi_process = subprocess.Popen([
                    sys.executable, "api.py"
                ])
                
            # Check if agent is still running  
            if agent_process.poll() is not None:
                print("❌ LiveKit agent died, restarting...")
                agent_process = subprocess.Popen([
                    sys.executable, "run_agent.py", "start"
                ])
                
            time.sleep(5)  # Check every 5 seconds
            
    except Exception as e:
        print(f"❌ Error: {e}")
        cleanup_processes()

if __name__ == "__main__":
    main()