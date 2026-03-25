@echo off
echo Starting InboxPilot (React + FastAPI)...
echo.
echo Frontend will open at: http://localhost:3000
echo Backend API at: http://localhost:8000
echo API Docs at:  http://localhost:8000/docs
echo.
echo Starting backend...
start cmd /K "cd backend && python main.py"
timeout /t 2

echo Starting frontend...
start cmd /K "cd frontend && npm install && npm run dev"

echo.
echo Both servers starting...
echo Press Ctrl+C in each terminal to stop.
