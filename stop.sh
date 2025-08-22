#!/bin/bash

# Copy Trading Bot - Stop Script
# Stops the bot running in background (Linux and macOS compatible)

echo "========================================"
echo "COPY TRADING BOT - STOP SCRIPT"
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

# Stop screen session
if screen -list | grep -q "copy-trading-bot"; then
    print_info "Stopping bot running in screen..."
    screen -S copy-trading-bot -X quit
    sleep 1
    
    if ! screen -list | grep -q "copy-trading-bot"; then
        print_success "Bot stopped successfully (screen)"
    else
        print_warning "Bot may still be running in screen"
    fi
fi

# Stop tmux session
if command -v tmux &> /dev/null && tmux list-sessions 2>/dev/null | grep -q "copy-trading-bot"; then
    print_info "Stopping bot running in tmux..."
    tmux kill-session -t copy-trading-bot
    sleep 1
    
    if ! tmux list-sessions 2>/dev/null | grep -q "copy-trading-bot"; then
        print_success "Bot stopped successfully (tmux)"
    else
        print_warning "Bot may still be running in tmux"
    fi
fi

# Kill any remaining Python processes (be careful with this)
if pgrep -f "main.py" > /dev/null; then
    print_warning "Found Python processes that might be the bot"
    print_info "Processes:"
    pgrep -f "main.py" -l
    echo
    read -p "Kill these processes? (y/N): " -r
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        pkill -f "main.py"
        print_info "Processes killed"
    fi
fi

# Check if bot is still running
if ! screen -list | grep -q "copy-trading-bot" && ! (command -v tmux &> /dev/null && tmux list-sessions 2>/dev/null | grep -q "copy-trading-bot") && ! pgrep -f "main.py" > /dev/null; then
    print_success "Bot is completely stopped"
else
    print_warning "Bot may still be running. Check manually:"
    print_info "screen -list"
    print_info "tmux list-sessions"
    print_info "pgrep -f main.py"
fi
