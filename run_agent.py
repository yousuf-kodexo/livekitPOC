# Simple agent runner
from livekit.agents import cli, WorkerOptions
from api import entrypoint

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))