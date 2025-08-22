#!/bin/bash

# Copy Trading Bot - Status Check Script
# Check if the bot is running and show status (Linux and macOS compatible)

echo "========================================"
echo "COPY TRADING BOT - STATUS CHECK"
echo "========================================"
echo

# Function to print colored output
print_info() {
    echo -e "\033[1;34m[INFO]\033[0m $1"
}

print_success() {
    echo -e "\033[1;32m[SUCCESS]\033[0m $1"
}

print_warning() {
    echo -e "\033[1;33m[WARNING]\033[0m $1"
}

print_error() {
    echo -e "\033[1;31m[ERROR]\033[0m $1"
}

# Check if bot is running in screen
if screen -list | grep -q "copy-trading-bot"; then
    print_success "Bot is running in screen session"
    print_info "View with: screen -r copy-trading-bot"
    RUNNING=true
fi

# Check if bot is running in tmux
if command -v tmux &> /dev/null && tmux list-sessions 2>/dev/null | grep -q "copy-trading-bot"; then
    print_success "Bot is running in tmux session"
    print_info "View with: tmux attach-session -t copy-trading-bot"
    RUNNING=true
fi

# Check if Python process is running
if pgrep -f "main.py" > /dev/null; then
    print_success "Bot Python process is running"
    print_info "Process IDs:"
    pgrep -f "main.py" -l
    RUNNING=true
fi

if [ "$RUNNING" != "true" ]; then
    print_warning "Bot is not running"
    echo
    print_info "To start the bot:"
    print_info "./start.sh                 # Interactive mode"
    print_info "./start_background.sh      # Background mode"
    echo
    exit 1
fi

echo
print_info "Checking server connectivity..."

# Check API server
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    print_success "API Server: http://localhost:8000 - ONLINE"
else
    print_warning "API Server: http://localhost:8000 - OFFLINE"
fi

# Check Dashboard
if curl -s http://localhost:5000 > /dev/null 2>&1; then
    print_success "Dashboard: http://localhost:5000 - ONLINE"
else
    print_warning "Dashboard: http://localhost:5000 - OFFLINE"
fi

echo
print_info "System Resources:"
echo "Memory usage:"
ps aux | grep "main.py" | grep -v grep | awk '{print "  Process " $2 ": " $4 "% memory, " $3 "% CPU"}'

echo
echo "Disk usage:"
df -h . | tail -1 | awk '{print "  Available: " $4 " (" $5 " used)"}'

echo
print_info "Log files:"
if [ -d "logs" ]; then
    ls -la logs/ | tail -5
else
    print_warning "No logs directory found"
fi

echo
print_info "Recent log entries (last 10 lines):"
if [ -f "copy_trading.log" ]; then
    tail -10 copy_trading.log
elif [ -d "logs" ] && ls logs/*.log >/dev/null 2>&1; then
    tail -10 logs/*.log | tail -10
else
    print_warning "No log files found"
fi
